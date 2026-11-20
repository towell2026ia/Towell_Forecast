"""Authenticated, source-pinned rolling revisions. Computing never adopts a plan."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import threading

from services.forecast_engine.engine import add_month
from .auth import ADMIN, EXECUTE, READ, AuthFailure, authorize
from .persistence import content_hash
from .rolling_store import RollingPlanningStore, RollingWriterLease, now
from .supabase_access import PreviewError, require_sales_context, uuid_value

MONTH = re.compile(r"20\d{2}-(0[1-9]|1[0-2])$")
FAMILIES = {"STATISTICAL", "ML", "ENSEMBLE", "COMMERCIAL_PLAN", "COLD_START"}


def permission(principal, level):
    try:
        authorize(principal, level)
    except AuthFailure:
        raise PreviewError("MANAGER_REQUIRED" if level == ADMIN else "SCOPE_FORBIDDEN", 403) from None


def idempotency(key):
    if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,128}", key):
        raise PreviewError("REQUEST_001", 422)


def scope(principal, catalogue, chains, products, *, write=False, partial=False):
    permission(principal, EXECUTE if write else READ)
    if (not isinstance(chains, list) or not isinstance(products, list) or not 1 <= len(chains) <= 100
            or not 1 <= len(products) <= 2000 or len(set(chains)) != len(chains) or len(set(products)) != len(products)):
        raise PreviewError("REQUEST_001", 422)
    chains, products = {uuid_value(x) for x in chains}, {uuid_value(x) for x in products}
    if not chains.issubset(principal.execute_chain_ids if write else principal.chain_ids):
        raise PreviewError("SCOPE_FORBIDDEN", 403)
    selected = [row for row in catalogue if row.get("product_id") in products and row.get("chain_id") in chains]
    found = {row["product_id"]: row for row in selected}
    if (len(selected) != len(found) or not partial and (set(found) != products or {r["chain_id"] for r in selected} != chains)
            or any(r.get("purchase_type") != "RS" for r in selected)):
        raise PreviewError("SCOPE_FORBIDDEN", 403)
    return found


def curve(candidate, start, end):
    if not isinstance(candidate, dict) or not isinstance(candidate.get("model"), str) or not candidate["model"].strip():
        raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
    rows = candidate.get("horizons")
    if not isinstance(rows, list) or len(rows) != 12 or add_month(start, 11) != end:
        raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
    result = []
    for index in range(1, 13):
        matches = [row for row in rows if isinstance(row, dict) and type(row.get("horizon")) is int and row["horizon"] == index]
        if len(matches) != 1:
            raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
        row = matches[0]
        try:
            if (row.get("target_period") != add_month(start, index - 1) or not isinstance(row.get("value"), str)
                    or type(row.get("planning_horizon")) is not int or row["planning_horizon"] != index
                    or type(row.get("forecast_distance")) is not int or row["forecast_distance"] != index):
                raise ValueError()
            value = Decimal(row["value"])
            if not value.is_finite() or value < 0:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409) from None
        result.append({**deepcopy(row), "planning_horizon": index, "value": format(value, "f")})
    return result


class LocalRollingInputLoader:
    """Trusted references are server configuration, never paths from a request."""
    def __init__(self, policy):
        self.policy = policy

    @staticmethod
    def _json(config, path_key, hash_key):
        path = Path(config[path_key])
        if not path.is_absolute():
            raise ValueError()
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != config[hash_key]:
            raise ValueError()
        result = json.loads(raw)
        if isinstance(result, dict):
            result = result.get("records")
        if not isinstance(result, list):
            raise ValueError()
        return result

    def __call__(self, provider, context, catalogue, chains):
        config = self.policy.config
        if not self.policy.available():
            raise PreviewError("PLANNING_SOURCE_UNAVAILABLE", 503)
        if (context.get("planning_basis") != "OBSERVED_AND_COMMERCIAL_CLOSE"
                or context.get("observed_cutoff") == context.get("training_cutoff")):
            from .rolling_inputs import observed_inputs
            metadata, observed = observed_inputs(provider, context, catalogue, chains)
            return {"rows": deepcopy(observed), "observed_records": observed, "metadata": metadata,
                    "input_hashes": {"source_hash": context["source_hash"]}}
        try:
            rows = self._json(config, "scenario_records_path", "scenario_records_sha256")
            observed = self._json(config, "observed_records_path", "observed_records_sha256")
        except (OSError, KeyError, ValueError, TypeError):
            raise PreviewError("PLANNING_SOURCE_UNAVAILABLE", 503) from None
        metadata = {r["product_id"]: deepcopy(r) for r in catalogue if r.get("chain_id") in chains}
        def bounded(records):
            return [deepcopy(r) for r in records if r.get("chain_id") in chains and r.get("product_id") in metadata]
        return {"rows": bounded(rows), "observed_records": bounded(observed), "metadata": metadata,
                "input_hashes": {key: config[key] for key in ("scenario_records_sha256", "observed_records_sha256")}}


class RollingPlanningService:
    def __init__(self, persistence, input_loader, *, compute=None, concurrency=1, hosted=False):
        if not 1 <= concurrency <= 2:
            raise ValueError("rolling_concurrency_invalid")
        self.lease = RollingWriterLease(persistence.path) if hosted else None
        self.hosted = hosted
        self.store = RollingPlanningStore(persistence)
        self.store.interrupted()
        self.input_loader, self.compute = input_loader, compute
        self.executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="rolling-plan")
        self.slots = threading.BoundedSemaphore(8)

    def close(self):
        self.executor.shutdown(wait=True)
        if self.lease:
            self.lease.close()

    @staticmethod
    def _current(provider, expected):
        current = provider.refresh_source_context() if hasattr(provider, "refresh_source_context") else provider.source_context()
        require_sales_context(current, required=True)
        if current != expected or current.get("objective") != "Pedido" or current.get("target_metric") != "ORDER":
            raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 409)
        return current

    @staticmethod
    def _catalogue(provider):
        catalogue = deepcopy(provider.client.catalog_rows())
        # Commercial state belongs to the real product record, never to a
        # statistical classification, recent zeros or inventory availability.
        read = getattr(provider.client, "rows", None)
        if callable(read):
            states = {}
            for chain in sorted({r["chain_id"] for r in catalogue}):
                for row in read("products", {"select": "id,chain_id,status", "chain_id": f"eq.{chain}", "order": "id.asc"}):
                    if row.get("chain_id") != chain:
                        raise PreviewError("LINEAGE_MISMATCH", 409)
                    states[row["id"]] = row.get("status")
            for row in catalogue:
                row["status"] = states.get(row["product_id"]) or "UNKNOWN"
        return catalogue

    def create(self, request, principal, provider, *, idempotency_key):
        permission(principal, EXECUTE)
        idempotency(idempotency_key)
        context = request.get("source_context")
        self._current(provider, context)
        catalogue = self._catalogue(provider)
        selected = scope(principal, catalogue, request.get("chain_ids"), request.get("product_ids"), write=True)
        cutoff = context.get("model_cutoff") or context.get("training_cutoff")
        observed = context.get("observed_cutoff") or context.get("training_cutoff")
        if (request.get("objective") != "Pedido" or not MONTH.fullmatch(str(cutoff)) or not MONTH.fullmatch(str(observed))
                or observed > cutoff or request.get("issue_period") not in (None, cutoff)):
            raise PreviewError("PLANNING_CUTOFF_INCOMPATIBLE", 409)
        signature = content_hash({**request, "chain_ids": sorted(request["chain_ids"]), "product_ids": sorted(selected), "actor_id": principal.user_id})
        # Inputs include authorized comparables, not merely the requested SKUs.
        inputs = self.input_loader(provider, context, catalogue, request["chain_ids"])
        for kind in ("rows", "observed_records"):
            if not isinstance(inputs.get(kind), list) or any(r.get("chain_id") not in request["chain_ids"]
                    or r.get("objective") != "Pedido" or r.get("period", "9999") > (observed if kind == "observed_records" else cutoff) for r in inputs[kind]):
                raise PreviewError("LINEAGE_MISMATCH", 409)
        self._current(provider, context)
        if not self.slots.acquire(blocking=False):
            raise PreviewError("RATE_001", 429)
        try:
            frozen, created = self.store.reserve(principal.user_id, idempotency_key, signature,
                {"source_context": deepcopy(context), "request": deepcopy(request), "inputs": inputs,
                 "issue_period": cutoff, "observed_cutoff": observed, "window_start": add_month(cutoff, 1),
                 "window_end": add_month(cutoff, 12), "product_ids": sorted(selected), "chain_ids": sorted(request["chain_ids"]),
                 "input_digest": content_hash(inputs)})
        except ValueError:
            self.slots.release()
            raise PreviewError("PLANNING_IDEMPOTENCY_CONFLICT", 409) from None
        except Exception:
            self.slots.release()
            raise
        if created:
            self.executor.submit(self._execute, frozen, provider)
        else:
            self.slots.release()
        return self._summary(frozen)

    def _execute(self, frozen, provider):
        run_id = frozen["run_id"]
        try:
            if not self.store.mark_running(run_id):
                return
            self._current(provider, frozen["source_context"])
            compute = self.compute
            if compute is None:
                from services.operational_update.rolling_operation import calculate_rolling_revision
                compute = calculate_rolling_revision
            inputs = frozen["inputs"]
            result = compute(deepcopy(inputs["rows"]), deepcopy(inputs["metadata"]), deepcopy(frozen["source_context"]),
                requested_product_ids=frozen["product_ids"], observed_records=deepcopy(inputs["observed_records"]),
                model_cutoff=frozen["issue_period"], observed_cutoff=frozen["observed_cutoff"], run_id=run_id,
                emitted_at=datetime.now(timezone.utc).isoformat())
            result = self._validate_run(result, frozen)
            self._current(provider, frozen["source_context"])
            self.store.finish(run_id, result)
        except PreviewError as error:
            self.store.fail(run_id, error.code)
        except Exception:
            self.store.fail(run_id, "PLANNING_EXECUTION_FAILED")
        finally:
            self.slots.release()

    @staticmethod
    def _validate_run(result, frozen):
        if (not isinstance(result, dict) or result.get("run_id") != frozen["run_id"]
                or result.get("source_context") != frozen["source_context"] or result.get("objective") != "Pedido"
                or result.get("cutoff") != frozen["issue_period"] or not isinstance(result.get("products"), list)):
            raise PreviewError("LINEAGE_MISMATCH", 409)
        rows = result["products"]
        metadata = frozen["inputs"]["metadata"]
        if (len(rows) != len(frozen["product_ids"]) or {r.get("product_id") for r in rows} != set(frozen["product_ids"])
                or any(r.get("chain_id") != metadata[r["product_id"]].get("chain_id") for r in rows)):
            raise PreviewError("LINEAGE_MISMATCH", 409)
        for product in rows:
            for family, candidate in product.get("candidates", {}).items():
                if family not in FAMILIES or not isinstance(candidate, dict) or candidate.get("family") != family:
                    raise PreviewError("LINEAGE_MISMATCH", 409)
                # Absent curves remain explicit exceptions, never zero-filled.
                if candidate.get("horizons"):
                    try:
                        curve(candidate, frozen["window_start"], frozen["window_end"])
                    except PreviewError:
                        if candidate.get("calculable") is True:
                            raise
        result = {**deepcopy(result), "window_start": frozen["window_start"], "window_end": frozen["window_end"],
                  "planning_start": frozen["window_start"], "planning_end": frozen["window_end"],
                  "creator_id": frozen["creator_id"], "input_digest": frozen["input_digest"],
                  "automatic_promotion": False, "champion_changed": False}
        result.pop("content_digest", None)
        result["content_digest"] = content_hash(result)
        return result

    def _summary(self, item, product_ids=None):
        context = item["source_context"]
        return {"run_id": item["run_id"], **self.store.status(item["run_id"]), "created_at": item["created_at"],
            "issue_period": item["issue_period"], "model_cutoff": item["issue_period"], "observed_cutoff": item["observed_cutoff"],
            "window_start": item["window_start"], "window_end": item["window_end"], "source_hash": context["source_hash"],
            "generation": context["generation"], "objective": "Pedido", "source_context": context,
            "product_ids": sorted(product_ids if product_ids is not None else item["product_ids"])}

    def summary(self, run_id, principal, provider):
        item = self.store.request(uuid_value(run_id))
        if item is None:
            raise PreviewError("PLANNING_RUN_NOT_FOUND", 404)
        permission(principal, READ)
        allowed = {r["product_id"] for r in self._catalogue(provider) if r.get("chain_id") in principal.chain_ids}
        visible = allowed.intersection(item["product_ids"])
        if not visible:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        return self._summary(item, visible)

    def list(self, principal, provider):
        permission(principal, READ)
        result = []
        for item in self.store.entries():
            try:
                result.append(self.summary(item["run_id"], principal, provider))
            except PreviewError as error:
                if error.status != 403:
                    raise
        return {"runs": result}

    def selected(self, run_id, principal, provider, request, *, write=False):
        run = self.store.run(uuid_value(run_id))
        if run is None:
            raise PreviewError("PLANNING_RUN_NOT_READY", 409)
        if request.get("source_context") != run["source_context"]:
            raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 409)
        selected = scope(principal, self._catalogue(provider), request.get("chain_ids"), request.get("product_ids"), write=write)
        members = {r["product_id"]: r for r in run["products"]}
        selected = {pid: row for pid, row in selected.items() if pid in members and members[pid]["chain_id"] == row["chain_id"]}
        if write and set(selected) != set(request["product_ids"]):
            raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
        return run, selected

    def report(self, run_id, principal, provider, request):
        from .rolling_report import report_rows
        run, selected = self.selected(run_id, principal, provider, request)
        allowed = [r for r in self._catalogue(provider) if r.get("chain_id") in principal.chain_ids]
        return report_rows(run, set(selected), requested_ids=request["product_ids"],
                           allowed_ids={r["product_id"] for r in allowed}, allowed_chains=set(principal.chain_ids))

    def current_champion(self, run_id, principal, provider, request):
        run, selected = self.selected(run_id, principal, provider, request)
        self._window(request, run)
        events = self.store.champions({pid: self.store.scope_key(run, pid) for pid in selected})
        artifact = self.store.artifact(run_id)
        for pid, event in events.items():
            product = next((p for p in event.get("products", []) if p.get("product_id") == pid), None)
            if (event.get("run_id") != run_id or event.get("source_context") != run["source_context"]
                    or event.get("window_start") != run["window_start"] or event.get("window_end") != run["window_end"]
                    or event.get("run_content_digest") != run["content_digest"] or event.get("run_file_sha256") != artifact["sha256"]
                    or product is None or product.get("chain_id") != selected[pid]["chain_id"]):
                raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503)
            curve(product, run["window_start"], run["window_end"])
        rows = [{**{k: v for k, v in event.items() if k != "products"}, "product": next(p for p in event["products"] if p["product_id"] == pid)}
                for pid, event in sorted(events.items())]
        return {"run_id": run_id, "source_context": run["source_context"], "planning_year": int(run["window_start"][:4]),
                "window_start": run["window_start"], "window_end": run["window_end"], "products": rows, "covered": len(rows), "eligible": len(selected)}

    @staticmethod
    def _window(request, run):
        if request.get("window_start") != run["window_start"] or request.get("window_end") != run["window_end"]:
            raise PreviewError("CHAMPION_WINDOW_MISMATCH", 409)

    def promote(self, run_id, principal, provider, request, *, idempotency_key):
        permission(principal, ADMIN)
        idempotency(idempotency_key)
        run, selected = self.selected(run_id, principal, provider, request, write=True)
        self._current(provider, run["source_context"])
        self._window(request, run)
        family = request.get("candidate_family")
        comment = request.get("comment")
        if request.get("confirm") is not True or family not in FAMILIES or not isinstance(comment, str) or not 5 <= len(comment.strip()) <= 1000:
            raise PreviewError("REQUEST_001", 422)
        published = []
        for item in run["products"]:
            if item["product_id"] not in selected:
                continue
            candidate = item.get("candidates", {}).get(family)
            rows = curve(candidate, run["window_start"], run["window_end"])
            if family != "COMMERCIAL_PLAN" and candidate.get("origin") not in {"PYTHON_EXISTING_ENGINE", "INTERNAL_PEER_ORDER"}:
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            admission, effect = candidate.get("admission") or {}, candidate.get("incremental_effect") or {}
            consolidation_allowed = True
            if family == "COLD_START":
                required = {"COLD_START_METHOD", "NO_TARGET_WAPE", "UNDETERMINED_INCREMENTAL_EFFECT"}
                acknowledgements = request.get("acknowledgements")
                if (request.get("accept_cold_start") is not True or not isinstance(admission, dict)
                        or admission.get("status") != "REQUIRES_EXPLICIT_ACCEPTANCE" or admission.get("manual_selection_allowed") is not True
                        or not isinstance(effect, dict) or not isinstance(acknowledgements, list)
                        or not all(isinstance(value, str) for value in acknowledgements)
                        or not required.issubset(acknowledgements)
                        or not set(admission.get("required_acknowledgements") or []).issubset(acknowledgements)):
                    raise PreviewError("COLD_START_ACCEPTANCE_REQUIRED", 409)
                consolidation_allowed = effect.get("status") in {"INCREMENTAL", "SUBSTITUTION_ACCOUNTED", "VERIFIED_INCREMENTAL"}
                if not consolidation_allowed and (effect.get("status") != "UNDETERMINED" or len(selected) != 1):
                    raise PreviewError("COLD_START_ACCEPTANCE_REQUIRED", 409)
            if family == "COMMERCIAL_PLAN" and not candidate.get("source_reference"):
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            published.append({"product_id": item["product_id"], "chain_id": item["chain_id"], "candidate_family": family,
                "model": candidate["model"], "origin": candidate["origin"], "horizons": rows,
                "technical_eligible": candidate.get("eligible") is True, "technical_reason": candidate.get("reason"),
                "technical_status": candidate.get("status"), "technical_metrics": candidate.get("metrics"),
                "composition": candidate.get("composition"), "cold_start_accepted": family == "COLD_START",
                "admission": deepcopy(admission), "incremental_effect": deepcopy(effect), "consolidation_allowed": consolidation_allowed,
                "admission_status": "ACCEPTED_INDIVIDUAL_ONLY" if not consolidation_allowed else "ACCEPTED",
                "accepted_acknowledgements": deepcopy(request.get("acknowledgements") or [])})
        signature = content_hash({**request, "product_ids": sorted(selected), "chain_ids": sorted(request["chain_ids"]), "run_id": run_id, "actor_id": principal.user_id})
        artifact = self.store.artifact(run_id)
        event = {"environment": "hosted" if self.hosted else "localhost",
            "publication_scope": "HOSTED_OPERATIONAL" if self.hosted else "LOCAL_OPERATIONAL", "official_publication": False,
            "status": "ACTIVE", "objective": "Pedido", "unit": "PIEZAS", "run_id": run_id, "run_content_digest": run["content_digest"],
            "run_file_sha256": artifact["sha256"], "run_artifact": artifact,
            "run_emitted_at": run.get("emitted_at"), "source_context": run["source_context"], "cutoff": run["cutoff"],
            "planning_year": int(run["window_start"][:4]), "window_start": run["window_start"], "window_end": run["window_end"],
            "candidate_family": family, "products": published, "selected_by": principal.user_id, "selected_at": now(),
            "comment": comment.strip(), "recommendation_changed": False, "ft_decision_changed": False}
        try:
            return self.store.promote(principal.user_id, idempotency_key, signature, event,
                {pid: self.store.scope_key(run, pid) for pid in selected})
        except ValueError:
            raise PreviewError("CHAMPION_IDEMPOTENCY_CONFLICT", 409) from None
