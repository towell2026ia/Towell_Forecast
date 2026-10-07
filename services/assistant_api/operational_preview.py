"""Asynchronous multi-scope previews; local storage only, never official writes."""
from __future__ import annotations

import threading
import time
import uuid
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from services.forecast_engine.engine import (
    ENGINE_VERSION, ForecastPolicy, forecast_dataset, month_end, add_month,
    normalize_dataset, normalize_retrospective_dataset,
)
from .persistence import content_hash
from .cold_start import launch_forecast
from .supabase_access import PreviewError
from services.forecast_engine.registry import ChampionRegistry

PREVIEW_ENGINE_VERSION = ENGINE_VERSION + "-operational-preview-2-retrospective"
STAGES = ("QUEUED", "READING_DATA", "ELIGIBILITY", "STATISTICAL", "ML", "ENSEMBLE", "QUALITY_GATE", "READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED")


def snapshot_hash(rows: list[dict[str, Any]]) -> str:
    fields = ("chain_id", "product_id", "period", "objective", "value", "available_at",
              "availability_source", "source_id", "version_no", "source_batch_id", "category_id", "variant")
    canonical = [{field: row.get(field) for field in fields} for row in rows]
    canonical.sort(key=lambda row: (row["chain_id"], row["product_id"], row["period"]))
    return content_hash({"rows": canonical})


class OperationalMultiChainForecastRunner:
    def __init__(self, persistence, *, concurrency: int = 2, environment: str = "production",
                 policy: ForecastPolicy | None = None, compute=forecast_dataset, researcher=None):
        if not 1 <= concurrency <= 2:
            raise ValueError("operational_concurrency_maximum_two")
        self.persistence, self.environment = persistence, environment
        self.policy, self.compute = policy or ForecastPolicy(), compute
        self.researcher = researcher
        self.champions = ChampionRegistry(persistence)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="preview")
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}
        self._child_locks: dict[str, threading.Lock] = {}
        # Tokens are deliberately not recoverable from SQLite. Interrupted jobs fail safely.
        for job in persistence.list("forecast_jobs"):
            if job.get("kind") == "OPERATIONAL_PREVIEW" and job.get("status") not in {"READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"}:
                job.update(status="FAILED", error_code="PREVIEW_INTERRUPTED")
                persistence.put("forecast_jobs", job["job_id"], job)

    def close(self):
        self._executor.shutdown(wait=True)

    def submit(self, provider, principal, *, chain_ids: list[str], product_id: str | None = None,
               mode: str = "RETROSPECTIVE_TRAINING", issue_period: str | None = None, all_scopes: bool = False):
        signature = content_hash({"creator": principal.user_id, "scopes": sorted(chain_ids),
            "product": product_id, "mode": mode, "issue": issue_period, "all_scopes": all_scopes})
        with self._lock:
            if signature in self._active:
                return self.persistence.get("forecast_jobs", self._active[signature])
            # Bounded queue; all-chain children execute sequentially inside a worker.
            if len(self._active) >= 8:
                raise PreviewError("RATE_001", 429)
            job_id = "OPJ-" + uuid.uuid4().hex
            job = {"job_id": job_id, "kind": "OPERATIONAL_PREVIEW", "creator_id": principal.user_id,
                "chain_ids": sorted(chain_ids), "objective": "Venta", "mode": mode,
                "product_id": product_id, "all_scopes": all_scopes, "status": "QUEUED", "scopes": [],
                "engine_version": PREVIEW_ENGINE_VERSION, "created_at": time.time()}
            self.persistence.put("forecast_jobs", job_id, job)
            self._active[signature] = job_id
            self._executor.submit(self._execute, provider, job, issue_period, signature)
            return job

    def _stage(self, job, stage):
        job["status"] = stage
        job.setdefault("stage_history", []).append({"stage": stage, "at": time.time()})
        self.persistence.put("forecast_jobs", job["job_id"], job)

    def _execute(self, provider, job, requested_issue, signature):
        try:
            for chain in job["chain_ids"]:
                try:
                    self._stage(job, "READING_DATA")
                    rows = provider.records(chain_id=chain, mode=job["mode"])
                    if not rows:
                        raise PreviewError("INSUFFICIENT_HISTORY")
                    latest = max(row["period"] for row in rows)
                    # Explicit override cannot invent a later actual cutoff.
                    issue = requested_issue or latest
                    month_end(issue)
                    if issue > latest:
                        raise PreviewError("DATA_LEAKAGE_DETECTED")
                    rows = [row for row in rows if row["period"] <= issue]
                    chain_rows = rows
                    if job["product_id"]:
                        rows = [row for row in rows if row["product_id"] == job["product_id"]]
                    if not rows:
                        provisional = self._cold_start_scope(provider, job, chain, issue, chain_rows)
                        if provisional:
                            job["scopes"].append(provisional)
                            continue
                        raise PreviewError("INSUFFICIENT_HISTORY")
                    digest = snapshot_hash(rows)
                    key = content_hash({"dataset_hash": digest, "chain": chain, "product": job["product_id"],
                        "objective": "Venta", "issue": issue, "mode": job["mode"],
                        "engine": PREVIEW_ENGINE_VERSION, "policy": asdict(self.policy), "environment": self.environment})
                    with self._lock:
                        existing = self.persistence.get("forecast_previews", key)
                    if existing:
                        job["scopes"].append({"chain_id": chain, "preview_id": key, "status": "READY_PREVIEW", "issue_period": issue, "reused": True})
                        continue
                    self._stage(job, "ELIGIBILITY")
                    normalize = normalize_retrospective_dataset if job["mode"] == "RETROSPECTIVE_TRAINING" else normalize_dataset
                    series = normalize(rows, issue)
                    counts = {name: 0 for name in ("ACTIVE", "COLD_START", "INACTIVE", "PRE-LAUNCH", "INSUFFICIENT")}
                    states, eligible = [], set()
                    for item in series:
                        state = item.lifecycle(issue, self.policy)
                        if state == "COLD_START" and len(item.history(issue)) < self.policy.min_product_observations:
                            state = "INSUFFICIENT"
                        counts[state] += 1
                        states.append({"product_id": item.product_id, "product_code": item.product_code,
                            "variant_code": item.variant,
                            "description": item.description, "category_id": None if item.category == "UNCLASSIFIED" else item.category,
                            "forecast_status": state, "history_months": len(item.history(issue)),
                            "minimum_history_months": self.policy.min_product_observations})
                        if state in {"ACTIVE", "COLD_START"}:
                            eligible.add(item.product_id)
                    if hasattr(provider, "product_catalog"):
                        for product in provider.product_catalog(chain):
                            if product["id"] in {item["product_id"] for item in states} or job["product_id"] and product["id"] != job["product_id"]:
                                continue
                            state = "PRE-LAUNCH" if product.get("first_seen_period", "")[:7] > issue else "INSUFFICIENT"
                            counts[state] += 1
                            states.append({"product_id": product["id"], "product_code": product["product_code"],
                                "variant_code": product.get("variant_code") or "",
                                "description": product["description"], "category_id": product.get("category_id"),
                                "forecast_status": state, "history_months": 0,
                                "minimum_history_months": self.policy.min_product_observations})
                    if not eligible:
                        provisional = self._cold_start_scope(provider, job, chain, issue, chain_rows, states, counts)
                        if provisional:
                            job["scopes"].append(provisional)
                            continue
                        job["scopes"].append({"chain_id": chain, "issue_period": issue, "status": "NOT_ELIGIBLE",
                            "error_code": "NO_ELIGIBLE_PRODUCTS", "products": states, "eligibility": counts})
                        continue
                    with self._lock:
                        child_lock = self._child_locks.setdefault(key, threading.Lock())
                    with child_lock:
                        existing = self.persistence.get("forecast_previews", key)
                        if existing:
                            job["scopes"].append({"chain_id": chain, "preview_id": key, "status": "READY_PREVIEW", "issue_period": issue, "reused": True})
                            continue
                        self._stage(job, "STATISTICAL")
                        incumbent = self._compatible_champion(chain, digest)
                        result = self.compute([row for row in rows if row["product_id"] in eligible], issue,
                            chain_id=chain, objective="Venta", policy=self.policy, research=None,
                            incumbent=incumbent, evidence_mode=job["mode"], on_stage=lambda stage: self._stage(job, stage))["chains"][0]
                        preview = self._result(chain, issue, latest, digest, job, states, counts, result)
                        preview["selection"]["published_champion"] = {"version": incumbent["version"]} if incumbent else None
                        preview["chain_name"] = rows[0].get("chain_name", chain)
                        self._stage(job, "QUALITY_GATE")
                        self._quality(preview)
                        self.persistence.put("forecast_previews", key, preview)
                    job["scopes"].append({"chain_id": chain, "preview_id": key, "status": "READY_PREVIEW", "issue_period": issue, "reused": False})
                except Exception as error:
                    safe = error.code if isinstance(error, PreviewError) else {
                        "availability_metadata_missing": "TEMPORAL_METADATA_MISSING",
                        "data_leakage_detected": "DATA_LEAKAGE_DETECTED",
                        "duplicate_observation_conflict": "DUPLICATE_OBSERVATION_CONFLICT",
                    }.get(str(error), "STATISTICAL_FAILED")
                    job["scopes"].append({"chain_id": chain, "status": "FAILED", "error_code": safe})
            cuts = {scope.get("issue_period") for scope in job["scopes"] if scope.get("issue_period")}
            job["cuts_status"] = "CUTS_NOT_ALIGNED" if len(cuts) > 1 else "ALIGNED"
            job["finished_at"] = time.time()
            final = ("READY_PREVIEW" if any(scope["status"] == "READY_PREVIEW" for scope in job["scopes"])
                     else "READY_PROVISIONAL" if any(scope["status"] == "PROVISIONAL_COLD_START" for scope in job["scopes"])
                     else "NOT_ELIGIBLE" if job["scopes"] and all(scope["status"] == "NOT_ELIGIBLE" for scope in job["scopes"])
                     else "FAILED")
            self._stage(job, final)
        finally:
            with self._lock:
                self._active.pop(signature, None)

    def _cold_start_scope(self, provider, job, chain, issue, chain_rows, states=None, counts=None):
        if not job["product_id"] or job["mode"] != "RETROSPECTIVE_TRAINING" or not hasattr(provider, "product_catalog"):
            return None
        target = next((item for item in provider.product_catalog(chain)
                       if item.get("id") == job["product_id"] and item.get("chain_id") == chain), None)
        if target is None:
            return None
        proposal = launch_forecast(chain_rows, issue, target)
        if proposal is None:
            return None
        category_name = next((row.get("category_name") for row in chain_rows
                              if row.get("category_id") == target.get("category_id") and row.get("category_name")), "")
        proposal["research"] = {"status": "DISABLED"}
        if self.researcher:
            try:
                proposal["research"] = self.researcher.start(description=target.get("description") or "",
                    category=category_name, chain_name=chain_rows[0].get("chain_name", ""))
            except Exception:
                proposal["research"] = {"status": "UNAVAILABLE"}
        product = next((item for item in states or [] if item["product_id"] == job["product_id"]), None)
        if product is None:
            product = {"product_id": target["id"], "product_code": target["product_code"],
                       "variant_code": target.get("variant_code") or "", "description": target["description"],
                       "category_id": target.get("category_id"), "forecast_status": "INSUFFICIENT",
                       "history_months": 0, "minimum_history_months": self.policy.min_product_observations}
        return {"chain_id": chain, "chain_name": chain_rows[0].get("chain_name", chain),
                "issue_period": issue, "latest_actual_period": issue, "status": "PROVISIONAL_COLD_START",
                "dataset_hash": snapshot_hash(chain_rows), "products": [product],
                "eligibility": counts or {"evaluated": 1, "visible_products": 1, "stat_eligible": 0, "ml_eligible": 0},
                "provisional_cold_start": proposal}

    def _result(self, chain, issue, latest, digest, job, states, counts, result):
        audit = result["model_audit"] if result else {}
        candidates = audit.get("candidate_models", [])
        horizons = result["forecast_towell"] if result else []
        distribution = {}
        for row in horizons:
            if row["horizon"] == 1:
                name = row["statistical_model"]
                distribution[name] = distribution.get(name, 0) + 1
        selection = result["selection"] if result else {}
        products = [{**state, "horizons": [{**row, "statistical_value": row["statistical"], "ml_value": row["ml"],
            "p10": (row.get("probability") or {}).get("p10"), "p50": row["forecast_towell"],
            "p90": (row.get("probability") or {}).get("p90"), "p95": (row.get("probability") or {}).get("p95"),
            "evidence_mode": job["mode"]} for row in horizons if row["product_id"] == state["product_id"]]} for state in states]
        for product in products:
            first = next((row for row in product["horizons"] if row["horizon"] == 1), {})
            product.update(statistical_model=first.get("statistical_model"), classification=first.get("classification"))
        ml_eligible = sum(any(row["ml_value"] is not None for row in item["horizons"]) for item in products)
        return {"chain_id": chain, "objective": "Venta", "issue_period": issue, "latest_actual_period": latest,
            "cutoff": month_end(issue), "evidence_mode": job["mode"], "mode": job["mode"], "status": "PREVIEW",
            "certification_status": "PROVISIONAL_TEMPORAL_UNKNOWN" if job["mode"] == "RETROSPECTIVE_TRAINING" else "PROVISIONAL",
            "temporal_certification": False, "certified_wape": None, "certified_bias": None,
            "dataset_hash": digest, "dataset_snapshot_hash": digest, "engine_version": PREVIEW_ENGINE_VERSION,
            "policy": asdict(self.policy), "creator_id": job["creator_id"], "environment": self.environment,
            "research": "RESEARCH_DISABLED_EXPECTED", "created_at": time.time(),
            "eligibility": {"visible_products": len(states), "evaluated": len(states), "stat_eligible": len(distribution) and sum(distribution.values()) or 0,
                "ml_eligible": ml_eligible, **counts},
            "statistical": {"status": "COMPLETED" if horizons else "NOT_ELIGIBLE", "models": distribution,
                "available_candidates": audit.get("available_statistical_candidates", []),
                "candidates": [row for row in candidates if row.get("family") == "statistical"],
                "scope_candidates": audit.get("scope_statistical_candidates", []),
                "selected_metrics": audit.get("selected_statistical_metrics"),
                "retrospective_wape": (audit.get("retrospective_statistical_metrics") or {}).get("wape"),
                "retrospective_bias": (audit.get("retrospective_statistical_metrics") or {}).get("bias")},
            "ml": {"status": "COMPLETED" if ml_eligible else "NOT_ELIGIBLE", "training_samples": audit.get("training_samples", 0),
                "training_products": audit.get("training_products"), "features": audit.get("feature_names", []),
                "available_candidates": audit.get("available_ml_candidates", []),
                "leader": next((row["ml_model"] for row in horizons if row["ml_model"]), None),
                "trained_candidates": audit.get("ml_candidates", []), "candidates": [row for row in candidates if row.get("family") == "ml"],
                "retrospective_wape": (audit.get("retrospective_ml_metrics") or {}).get("wape"),
                "retrospective_bias": (audit.get("retrospective_ml_metrics") or {}).get("bias"),
                "failures": audit.get("ml_failures", [])},
            "selection": {"published_champion": None, "preview_leader": selection.get("preview_leader"),
                "preview_challenger": selection.get("preview_challenger"), "no_degradation": selection.get("no_degradation"),
                "automatic_promotion": False, "promotion_allowed": False,
                "failures": audit.get("ensemble_failures", [])},
            "evaluation_mode": audit.get("evaluation_mode"),
            "products": products, "aggregates": result["aggregates"] if result else []}

    def _compatible_champion(self, chain, digest):
        # Read the existing registry, never promote or borrow a pilot Champion.
        champion = self.champions.current(chain, "Venta", "chain")
        if not champion or any(champion.get(key) != value for key, value in {
            "chain_id": chain, "objective": "Venta", "scope": "chain",
            "environment": self.environment, "dataset_hash": digest,
        }.items()):
            return None
        return champion if champion.get("version") else None

    @staticmethod
    def _quality(preview):
        for product in preview["products"]:
            horizons = product["horizons"]
            if horizons and {row["horizon"] for row in horizons} != set(range(1, 13)):
                raise PreviewError("STATISTICAL_FAILED", 503)
            if len(horizons) not in {0, 12}:
                raise PreviewError("STATISTICAL_FAILED", 503)
            for row in horizons:
                if row["target_period"] != add_month(preview["issue_period"], row["horizon"]):
                    raise PreviewError("STATISTICAL_FAILED", 503)
                if any(not math.isfinite(row[key]) or row[key] < 0 for key in ("forecast_towell", "statistical_value", "p50")):
                    raise PreviewError("STATISTICAL_FAILED", 503)
                band = [row[key] for key in ("p10", "p50", "p90", "p95")]
                if any(value is not None and (not math.isfinite(value) or value < 0) for value in band) \
                        or all(value is not None for value in band) and band != sorted(band):
                    raise PreviewError("STATISTICAL_FAILED", 503)

    def get_job(self, job_id, principal):
        job = self.persistence.get("forecast_jobs", job_id)
        if not job or job.get("kind") != "OPERATIONAL_PREVIEW":
            raise PreviewError("PREVIEW_NOT_FOUND", 404)
        if not set(job["chain_ids"]).issubset(principal.chain_ids):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        return job

    def poll_research(self, job_id, principal):
        job = self.get_job(job_id, principal)
        if job["status"] != "READY_PROVISIONAL":
            raise PreviewError("PREVIEW_NOT_READY", 409)
        with self._lock:
            job = self.get_job(job_id, principal)
            for scope in job["scopes"]:
                proposal = scope.get("provisional_cold_start") or {}
                current = proposal.get("research") or {}
                if not self.researcher and current.get("status") == "PENDING":
                    proposal["research"] = {"status": "UNAVAILABLE"}
                if self.researcher and current.get("status") == "PENDING":
                    try:
                        proposal["research"] = self.researcher.poll(current.get("response_id", ""))
                    except Exception:
                        proposal["research"] = {"status": "UNAVAILABLE"}
            self.persistence.put("forecast_jobs", job_id, job)
        return self.result(job_id, principal, job.get("product_id"))

    def result(self, job_id, principal, product_id=None):
        job = self.get_job(job_id, principal)
        if job["status"] not in {"READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE"}:
            raise PreviewError("PREVIEW_NOT_READY", 409)
        return self._render_result(job, job["scopes"], product_id)

    def _render_result(self, job, references, product_id):
        scopes = [{**self.persistence.get("forecast_previews", scope["preview_id"]), "preview_id": scope["preview_id"]}
                  if scope.get("preview_id") else dict(scope) for scope in references]
        for scope in scopes:
            scope["products"] = [{**product, "horizons": product.get("horizons", []) if product_id else []}
                for product in scope.get("products", []) if not product_id or product["product_id"] == product_id]
        return {**job, "chain_ids": [scope["chain_id"] for scope in scopes], "dataset_hash": content_hash({"scopes": sorted(
            (scope["chain_id"], scope.get("dataset_hash")) for scope in scopes)}), "scopes": scopes}

    def latest(self, principal, chain_id=None, product_id=None, mode="RETROSPECTIVE_TRAINING"):
        if chain_id and chain_id not in principal.chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        jobs = [job for job in self.persistence.list("forecast_jobs") if job.get("kind") == "OPERATIONAL_PREVIEW"
                and job["mode"] == mode
                and (job.get("creator_id") == principal.user_id or job["status"] in {"READY_PREVIEW", "READY_PROVISIONAL"})
                and job.get("product_id") in {None, product_id}
                and (chain_id in job["chain_ids"] and (job["status"] in {"READY_PREVIEW", "READY_PROVISIONAL"} or
                    set(job["chain_ids"]).issubset(principal.chain_ids)) if chain_id else
                    job.get("all_scopes", False) and set(job["chain_ids"]).issubset(principal.chain_ids))]
        if not jobs:
            raise PreviewError("PREVIEW_NOT_FOUND", 404)
        newest = max(jobs, key=lambda job: job["created_at"])
        if newest["status"] in {"READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE"}:
            references = [scope for scope in newest["scopes"] if not chain_id or scope["chain_id"] == chain_id]
            return self._render_result(newest, references, product_id)
        return newest
