"""Explicit hosted G6 transport. Every access carries the caller's Auth JWT.

The import verifies the complete file seal; the RPC returns only RLS-visible
products from that immutable release. A subset is never claimed as a full-file
digest, and no local file, service role or statistical execution is a fallback.
"""
from datetime import datetime, timezone
import re

from .auth import ADMIN, READ, AuthFailure, authorize
from .local_champion import LocalChampionService
from .supabase_access import COMMERCIAL_CLOSE_BASIS, PreviewError, matches_source, observed_cutoff, require_sales_context, uuid_value
from .unified_plan_reader import product_rows


def _array(values):
    return [uuid_value(value) for value in values]


def require_hosted_context(context):
    require_sales_context(context, required=True)
    if (context.get("forecast_contract") != "pedido-unificado-v1" or context.get("generation") != 6
            or context.get("planning_basis") != COMMERCIAL_CLOSE_BASIS):
        raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)


def hosted_run(client, context, *, product_ids=(), chain_ids=()):
    require_hosted_context(context)
    run = client.read_rpc("operational_read_planning_run", {
        "p_source_hash": context["source_hash"], "p_generation": context["generation"],
        "p_product_ids": _array(product_ids), "p_chain_ids": _array(chain_ids)})
    try:
        if (not isinstance(run, dict) or run.get("schema") != "unified-planning-run-1"
                or run.get("transport") != "SUPABASE_SEALED_G6" or run.get("objective") != "Pedido"
                or not matches_source(run.get("source_context"), context) or run.get("cutoff") != context["training_cutoff"]
                or run.get("model_cutoff") != context["training_cutoff"] or run.get("observed_cutoff") != context["observed_cutoff"]
                or run.get("planning_basis") != COMMERCIAL_CLOSE_BASIS or run.get("planning_year") != 2027
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(run.get(key, ""))) for key in ("content_digest", "file_sha256"))
                or not isinstance(run.get("products"), list)):
            raise ValueError()
        uuid_value(run["run_id"])
        emitted = datetime.fromisoformat(run["emitted_at"].replace("Z", "+00:00"))
        if emitted.tzinfo is None or emitted > datetime.now(timezone.utc) or emitted.strftime("%Y-%m") <= observed_cutoff(context):
            raise ValueError()
        seen = set()
        for product in run["products"]:
            pid, chain = uuid_value(product["product_id"]), uuid_value(product["chain_id"])
            if pid in seen or pid not in product_ids or chain not in chain_ids:
                raise ValueError()
            seen.add(pid)
            product_rows(run, product)
        return run
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PreviewError("PLANNING_RUN_UNAVAILABLE", 503) from None


class HostedChampionService:
    """User-token RPC persistence, separate from local SQLite and certified E3."""

    @staticmethod
    def _scope(principal, context, catalogue, chain_ids, product_ids, *, write=False):
        require_hosted_context(context)
        try:
            authorize(principal, ADMIN if write else READ)
        except AuthFailure:
            raise PreviewError("MANAGER_REQUIRED" if write else "SCOPE_FORBIDDEN", 403) from None
        # Reuse the pure identity/role validator, never the local service or its
        # persistence. PostgreSQL independently validates all scopes again.
        return LocalChampionService._scope(principal, chain_ids, product_ids, catalogue, write=write)

    def current(self, principal, context, catalogue, *, planning_year, chain_ids, product_ids, client):
        self._scope(principal, context, catalogue, chain_ids, product_ids)
        result = client.read_rpc("operational_read_champions", {
            "p_source_hash": context["source_hash"], "p_generation": context["generation"],
            "p_planning_year": planning_year, "p_chain_ids": _array(chain_ids), "p_product_ids": _array(product_ids)})
        if (not isinstance(result, dict) or not matches_source(result.get("source_context"), context)
                or result.get("planning_year") != planning_year or result.get("eligible") != len(product_ids)
                or not isinstance(result.get("products"), list) or result.get("covered") != len(result["products"])):
            raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503)
        return result

    def publish(self, request, principal, context, catalogue, *, idempotency_key, client):
        self._scope(principal, context, catalogue, request.get("chain_ids"), request.get("product_ids"), write=True)
        if (request.get("confirm") is not True or request.get("source_context") != context
                or not isinstance(idempotency_key, str) or not re.fullmatch(r"[!-~]{1,128}", idempotency_key)):
            raise PreviewError("REQUEST_001", 422)
        result = client.champion_rpc({"p_source_hash": context["source_hash"], "p_generation": context["generation"],
            "p_run_id": uuid_value(request["run_id"]), "p_candidate_family": request["candidate_family"],
            "p_planning_year": request["planning_year"], "p_chain_ids": request["chain_ids"], "p_product_ids": request["product_ids"],
            "p_comment": request["comment"], "p_idempotency_key": idempotency_key, "p_confirm": True})
        if not isinstance(result, dict) or not matches_source(result.get("source_context"), context) or not result.get("id"):
            raise PreviewError("CHAMPION_PERSISTENCE_FAILED", 503)
        return result
