"""Source-bound, explicit local Champion publication; never an E4 adoption.

The trusted, immutable planning run supplies every amount. The browser supplies
only its exact selection, family and confirmation. Existing SQLite artifacts
retain the event and one current pointer per product/year atomically.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .auth import ADMIN, READ, AuthFailure, authorize
from .persistence import LocalPersistenceProvider, content_hash
from .supabase_access import PreviewError, require_sales_context, uuid_value

FAMILIES = frozenset({"STATISTICAL", "ML", "ENSEMBLE", "COMMERCIAL_PLAN"})


class LocalChampionService:
    def __init__(self, persistence: LocalPersistenceProvider, reference: dict):
        if not isinstance(persistence, LocalPersistenceProvider):
            raise ValueError("local_champion_sqlite_required")
        self.persistence = persistence
        self.reference = deepcopy(reference)

    @staticmethod
    def context(context):
        require_sales_context(context, required=True)
        if (context.get("forecast_contract") != "pedido-unificado-v1"
                or context.get("objective") != "Pedido" or context.get("target_metric") != "ORDER"
                or context.get("target_sheet") != "Base_2024_2026"
                or context.get("forecast_available") is not True):
            raise PreviewError("CHAMPION_SOURCE_INELIGIBLE", 409)
        return context

    def run(self, run_id, context):
        reference = self.reference
        if (not isinstance(reference, dict) or not isinstance(reference.get("path"), str)
                or not Path(reference["path"]).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", str(reference.get("sha256", "")))):
            raise PreviewError("CHAMPION_RUN_UNAVAILABLE", 503)
        try:
            raw = Path(reference["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
                raise ValueError()
            run = json.loads(raw)
            digest = run.pop("content_digest")
            if digest != content_hash(run):
                raise ValueError()
            run["content_digest"] = digest
            if (run.get("schema") != "unified-planning-run-1" or run.get("run_id") != uuid_value(run_id)
                    or run.get("objective") != "Pedido" or run.get("cutoff") != context.get("training_cutoff")
                    or run.get("source_context") != context or type(run.get("planning_year")) is not int
                    or not 2020 <= run["planning_year"] <= 2099
                    or context.get("planning_start") != f"{run['planning_year']}-01"
                    or context.get("planning_end") != f"{run['planning_year']}-12"):
                raise ValueError()
            emitted = datetime.fromisoformat(run["emitted_at"].replace("Z", "+00:00"))
            if (emitted.tzinfo is None or emitted > datetime.now(timezone.utc)
                    or emitted.strftime("%Y-%m") <= run["cutoff"]):
                raise ValueError()
            return run
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise PreviewError("CHAMPION_RUN_UNAVAILABLE", 409) from None

    @staticmethod
    def _scope(principal, chain_ids, product_ids, catalogue, *, write=False):
        try:
            authorize(principal, ADMIN if write else READ)
        except AuthFailure:
            raise PreviewError("MANAGER_REQUIRED" if write else "SCOPE_FORBIDDEN", 403) from None
        if (not isinstance(chain_ids, list) or not isinstance(product_ids, list) or not chain_ids or not product_ids
                or len(chain_ids) > 100 or len(product_ids) > 2000
                or len(set(chain_ids)) != len(chain_ids) or len(set(product_ids)) != len(product_ids)):
            raise PreviewError("REQUEST_001", 422)
        chains = {uuid_value(value) for value in chain_ids}
        products = {uuid_value(value) for value in product_ids}
        if not chains.issubset(principal.execute_chain_ids if write else principal.chain_ids):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        selected = [row for row in catalogue if row.get("product_id") in products]
        binding = {row["product_id"]: row["chain_id"] for row in selected}
        if (len(selected) != len(binding) or set(binding) != products or set(binding.values()) != chains
                or any(row.get("purchase_type") != "RS" for row in selected)):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        return binding

    @staticmethod
    def _key(context, year, product_id):
        return "LOCAL_CHAMPION:" + content_hash({"source_context": context, "objective": "Pedido",
                                                "planning_year": year, "product_id": product_id})

    @staticmethod
    def _curve(candidate, year, cutoff):
        rows = candidate.get("horizons")
        if (candidate.get("eligible") is not True or not isinstance(candidate.get("model"), str)
                or not candidate["model"].strip() or not isinstance(candidate.get("origin"), str)
                or not candidate["origin"].strip() or not isinstance(rows, list) or len(rows) != 12):
            raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
        curve = []
        for month in range(1, 13):
            matches = [row for row in rows if isinstance(row, dict) and row.get("planning_horizon") == month]
            if len(matches) != 1:
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            row = matches[0]
            distance = (year - int(cutoff[:4])) * 12 + month - int(cutoff[5:7])
            if (type(row.get("planning_horizon")) is not int or type(row.get("horizon")) is not int
                    or type(row.get("forecast_distance")) is not int
                    or row.get("horizon") != month or row.get("target_period") != f"{year}-{month:02}"
                    or row.get("forecast_distance") != distance
                    or not isinstance(row.get("value"), str) or not row["value"].strip()):
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            try:
                amount = Decimal(str(row["value"]))
                if not amount.is_finite() or amount < 0:
                    raise InvalidOperation()
            except InvalidOperation:
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409) from None
            curve.append({**deepcopy(row), "value": format(amount, "f")})
        return curve

    def publish(self, request, principal, context, catalogue, *, idempotency_key):
        self.context(context)
        if (request.get("confirm") is not True or request.get("source_context") != context
                or request.get("candidate_family") not in FAMILIES
                or not isinstance(request.get("comment"), str) or not 5 <= len(request["comment"].strip()) <= 1000
                or not isinstance(idempotency_key, str) or not re.fullmatch(r"[!-~]{1,128}", idempotency_key)):
            raise PreviewError("REQUEST_001", 422)
        binding = self._scope(principal, request.get("chain_ids"), request.get("product_ids"), catalogue, write=True)
        run = self.run(request.get("run_id"), context)
        if request.get("planning_year") != run["planning_year"]:
            raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
        products = run.get("products")
        if not isinstance(products, list):
            raise PreviewError("CHAMPION_RUN_UNAVAILABLE", 409)
        published = []
        for product_id, chain_id in sorted(binding.items()):
            found = [row for row in products if isinstance(row, dict) and row.get("product_id") == product_id]
            if len(found) != 1 or found[0].get("chain_id") != chain_id:
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            product = found[0]
            candidates = product.get("candidates")
            candidate = candidates.get(request["candidate_family"]) if isinstance(candidates, dict) else None
            if not isinstance(candidate, dict) or candidate.get("family") != request["candidate_family"]:
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            if request["candidate_family"] != "COMMERCIAL_PLAN" and candidate.get("origin") != "PYTHON_EXISTING_ENGINE":
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            curve = self._curve(candidate, run["planning_year"], run["cutoff"])
            if request["candidate_family"] == "COMMERCIAL_PLAN" and not candidate.get("source_reference"):
                raise PreviewError("CHAMPION_CANDIDATE_INELIGIBLE", 409)
            published.append({"product_id": product_id, "chain_id": chain_id,
                "identity_key": product.get("identity_key"), "model_group_key": product.get("model_group_key"),
                "candidate_family": request["candidate_family"], "model": candidate["model"],
                "origin": candidate["origin"], "source_reference": candidate.get("source_reference"),
                "evaluation_signature": candidate.get("evaluation_signature"), "horizons": curve})
        request_data = {**request, "chain_ids": sorted(binding.values()), "product_ids": sorted(binding),
                        "actor_id": principal.user_id, "run_content_digest": run["content_digest"]}
        event = {"request_hash": content_hash(request_data), "environment": "localhost", "status": "ACTIVE",
            "objective": "Pedido", "unit": "PIEZAS", "planning_year": run["planning_year"], "cutoff": run["cutoff"],
            "run_id": run["run_id"], "run_content_digest": run["content_digest"],
            "run_file_sha256": self.reference["sha256"], "run_emitted_at": run["emitted_at"],
            "source_context": deepcopy(context), "candidate_family": request["candidate_family"],
            "products": published, "selected_by": principal.user_id,
            "selected_at": datetime.now(timezone.utc).isoformat(), "comment": request["comment"].strip(),
            "recommendation_changed": False, "ft_decision_changed": False}
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localhost-champion:" + principal.user_id + ":" + idempotency_key))
        keys = {pid: self._key(context, run["planning_year"], pid) for pid in binding}
        try:
            return self.persistence.promote_local_champion(keys, event_id, event)
        except sqlite3.Error:
            raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503) from None
        except ValueError as error:
            raise PreviewError("CHAMPION_IDEMPOTENCY_CONFLICT" if str(error) == "champion_idempotency_conflict"
                               else "CHAMPION_PERSISTENCE_FAILED", 409) from None

    def current(self, principal, context, catalogue, *, planning_year, chain_ids, product_ids):
        self.context(context)
        if type(planning_year) is not int or not 2020 <= planning_year <= 2099:
            raise PreviewError("REQUEST_001", 422)
        binding = self._scope(principal, chain_ids, product_ids, catalogue)
        result = []
        try:
            events = self.persistence.read_local_champions({product_id: self._key(context, planning_year, product_id) for product_id in binding})
        except (ValueError, KeyError, sqlite3.Error):
            raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503) from None
        for product_id, chain_id in sorted(binding.items()):
            event = events.get(product_id)
            if not event:
                continue
            if (event.get("source_context") != context or event.get("planning_year") != planning_year
                    or event.get("objective") != "Pedido"):
                raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503)
            matches = [row for row in event["products"] if row["product_id"] == product_id and row["chain_id"] == chain_id]
            if len(matches) != 1:
                raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503)
            result.append({**{key: value for key, value in event.items() if key not in {"products", "previous_champion_ids"}},
                           "product": matches[0]})
        return {"source_context": deepcopy(context), "planning_year": planning_year,
                "products": result, "covered": len(result), "eligible": len(binding)}
