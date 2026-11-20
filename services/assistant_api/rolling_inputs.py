"""JWT-scoped hosted input adapter, deliberately not enabled by installation."""
from copy import deepcopy
from decimal import Decimal
import re

from .hosted_planning import hosted_run
from .supabase_access import COMMERCIAL_CLOSE_BASIS, PreviewError


def rolling_scenario(client, context, *, product_ids, chain_ids):
    """Future sealed scenario carrier; the original G6 reader stays unchanged."""
    result = client.read_rpc("operational_read_rolling_scenario", {"p_source_hash": context["source_hash"],
        "p_generation": context["generation"], "p_product_ids": product_ids, "p_chain_ids": chain_ids})
    if (not isinstance(result, dict) or result.get("transport") != "SUPABASE_SEALED_ROLLING_SCENARIO"
            or result.get("source_context") != context or not isinstance(result.get("products"), list)
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(result.get(key, ""))) for key in ("content_digest", "file_sha256"))):
        raise PreviewError("PLANNING_SOURCE_UNAVAILABLE", 503)
    seen = set()
    for product in result["products"]:
        if (not isinstance(product, dict) or product.get("product_id") not in product_ids
                or product.get("chain_id") not in chain_ids or product["product_id"] in seen):
            raise PreviewError("LINEAGE_MISMATCH", 409)
        seen.add(product["product_id"])
    return result


def observed_inputs(provider, context, catalogue, chains):
    metadata = {r["product_id"]: deepcopy(r) for r in catalogue if r.get("chain_id") in chains}
    rows = []
    for chain in chains:
        for item in provider.records(chain_id=chain, objective="Pedido", mode="RETROSPECTIVE_TRAINING",
                                     issue_period=context.get("observed_cutoff") or context["training_cutoff"]):
            if item.get("product_id") not in metadata or item.get("chain_id") != chain or item.get("objective") != "Pedido":
                raise PreviewError("LINEAGE_MISMATCH", 409)
            rows.append({**deepcopy(item), "basis": "OBSERVED", "metric_code": "ORDER",
                "source_file_sha256": context["target_file_sha256"]})
    return metadata, rows


class HostedRollingInputLoader:
    """Read current observed values and sealed commercial cells using user JWT.

    No filesystem, archive download, service_role or mutable latest-version
    selection is a fallback. The output is frozen by the same run journal.
    """
    def __call__(self, provider, context, catalogue, chains):
        metadata, observed = observed_inputs(provider, context, catalogue, chains)
        if (context.get("planning_basis") != COMMERCIAL_CLOSE_BASIS
                or context.get("observed_cutoff") == context.get("training_cutoff")):
            return {"rows": deepcopy(observed), "observed_records": observed, "metadata": metadata,
                    "input_hashes": {"source_hash": context["source_hash"]}}
        reader = hosted_run if context.get("generation") == 6 else rolling_scenario
        run = reader(provider.client, context, product_ids=sorted(metadata), chain_ids=chains)
        baseline = {(row["chain_id"], row["product_id"], row["period"]): row for row in observed}
        fit = deepcopy(observed)
        seen = set(baseline)
        for product in run["products"]:
            pid, chain = product["product_id"], product["chain_id"]
            snapshot = product.get("input_snapshot") or {}
            if snapshot.get("source_context") != context:
                raise PreviewError("LINEAGE_MISMATCH", 409)
            history = snapshot.get("commercial_history")
            if not isinstance(history, list):
                raise PreviewError("PLANNING_SOURCE_UNAVAILABLE", 503)
            for row in history:
                period = row.get("period")
                if not isinstance(period, str) or period > context["training_cutoff"] or not row.get("source_reference"):
                    raise PreviewError("LINEAGE_MISMATCH", 409)
                key = chain, pid, period
                basis = row.get("basis")
                if basis == "OBSERVED":
                    # The archived row does not override the RLS-bound real
                    # observation. It must agree before serving as lineage.
                    existing = baseline.get(key)
                    if existing is None or (row.get("value") is None) != (existing.get("value") is None):
                        raise PreviewError("LINEAGE_MISMATCH", 409)
                    if row.get("value") is not None and Decimal(str(row["value"])) != Decimal(str(existing["value"])):
                        raise PreviewError("LINEAGE_MISMATCH", 409)
                    continue
                if basis != "COMMERCIAL_CLOSE" or period <= context["observed_cutoff"] or key in seen:
                    raise PreviewError("LINEAGE_MISMATCH", 409)
                seen.add(key)
                meta = metadata[pid]
                fit.append({**deepcopy(row), "chain_id": chain, "product_id": pid, "objective": "Pedido",
                    "metric_code": "ORDER", "source_file_sha256": context["target_file_sha256"],
                    "category": meta.get("product_type") or meta.get("category_id") or "UNCLASSIFIED",
                    "category_id": meta.get("category_id"), "product_code": meta.get("upc") or meta.get("product_code"),
                    "variant": meta.get("color") or "", "description": meta.get("product_description") or meta.get("description")})
        return {"rows": fit, "observed_records": observed, "metadata": metadata,
                "input_hashes": {"source_hash": context["source_hash"], "scenario_hash": context["scenario_hash"],
                    "parent_run_content_digest": run["content_digest"], "parent_run_file_sha256": run["file_sha256"]}}
