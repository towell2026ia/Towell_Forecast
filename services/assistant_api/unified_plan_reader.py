"""Project a sealed annual run into the existing report reader's two row types."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID, uuid5

from .persistence import content_hash
from .supabase_access import PreviewError, matches_source, require_sales_context


@lru_cache(maxsize=1)
def _read(path, expected_sha, file_stamp):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("planning_file_changed")
    run = json.loads(raw)
    signature = run.pop("content_digest")
    if content_hash(run) != signature or run.get("schema") != "unified-planning-run-1":
        raise ValueError("planning_seal_invalid")
    run["content_digest"] = signature
    return run


def pinned_run(reference, context):
    require_sales_context(context, required=True)
    try:
        path = Path(reference["path"])
        if not path.is_absolute():
            raise ValueError("planning_path")
        stat = path.stat()
        run = _read(path, reference["sha256"], (stat.st_ino, stat.st_size, stat.st_mtime_ns))
        if (not matches_source(run.get("source_context"), context) or run["objective"] != "Pedido"
                or run["cutoff"] != context["training_cutoff"] or run["planning_year"] != 2027):
            raise ValueError("planning_source_mismatch")
        return run
    except (KeyError, TypeError, OSError, ValueError, AttributeError) as error:
        raise PreviewError("PLANNING_RUN_UNAVAILABLE", 409) from error


def _number(value):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("planning_quantity")
    return number


def product_rows(run, product):
    run_id = UUID(run["run_id"])
    product_id = product["product_id"]
    calculation_id = str(uuid5(run_id, product_id))
    identity = {"product_id": product_id, "chain_id": product["chain_id"]}
    families = product["candidates"]
    metrics = {}
    values = {}
    for name in ("STATISTICAL", "ML", "ENSEMBLE"):
        candidate = families.get(name)
        if candidate is None:
            continue
        curve = {}
        for row in candidate["horizons"]:
            index = row["horizon"]
            if (type(index) is not int or index not in range(1, 13) or index in curve
                    or row["target_period"] != f"{run['planning_year']}-{index:02}"
                    or row["forecast_distance"] != index + 3):
                raise ValueError("planning_calendar")
            curve[index] = {**row, "value": _number(row["value"])}
        if set(curve) != set(range(1, 13)):
            raise ValueError("planning_month_missing")
        values[name] = curve
        if candidate.get("model") and all(r["value"] is not None for r in curve.values()):
            stats = candidate["metrics"]
            metrics[name] = {**stats, "observations": stats.get("observations") or 0,
                "windows": stats.get("windows") or 0, "product_id": product_id, "family": name.lower(),
                "model": candidate["model"], "strategy": candidate["model"], "evidence_mode": run["evaluation_mode"],
                "evaluation_signature": candidate.get("evaluation_signature"), "by_horizon": [],
                "horizons": [{k: row[k] for k in ("horizon", "target_period", "value")} for row in curve.values()]}
    if not any(cell["value"] is not None for curve in values.values() for cell in curve.values()):
        return None, []
    horizons = []
    for h in range(1, 13):
        stat = values["STATISTICAL"][h]
        ml = values["ML"][h]
        ensemble = values["ENSEMBLE"][h]
        horizons.append({**identity, "id": str(uuid5(UUID(calculation_id), str(h))),
            "calculation_id": calculation_id, "horizon": h, "target_period": stat["target_period"] + "-01",
            "statistical_value": stat["value"], "ml_value": ml["value"], "ensemble_value": ensemble["value"],
            "statistical_model": families["STATISTICAL"]["model"], "ml_model": families["ML"]["model"],
            "ensemble_ml_model": ensemble["ensemble_ml_model"], "ensemble_ml_component": _number(ensemble["ensemble_ml_component"]),
            "ml_weight": ensemble["ml_weight"], "statistical_weight": ensemble["statistical_weight"],
            "p10": None, "p50": None, "p90": None, "p95": None,
            "band_basis": "INSUFFICIENT", "band_status": "INSUFFICIENT_BAND_EVIDENCE", "band_observations": 0})
    snap = {**product["input_snapshot"], "planning_run_id": str(run_id),
        "product": {"offline_provenance": {"emitted_at": run["emitted_at"]}},
        "champion_eligible": [name for name, c in families.items() if c.get("eligible") is True]}
    cutoff = date.fromisoformat(run["cutoff"] + "-01")
    month_end = (cutoff.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    header = {**identity, "id": calculation_id, "objective": run["objective"], "calculation_no": 1,
        "calculation_code": str(run["planning_year"]), "issue_period": cutoff.isoformat(),
        "cutoff_at": month_end.isoformat() + "T23:59:59Z", "status": "READY_FOR_DECISION",
        "created_at": run["emitted_at"], "created_by": run.get("actor_id"), "preview_id": str(run_id),
        "engine_version": run["engine_version"], "data_snapshot_hash": run["training_fingerprint"],
        "git_sha": run.get("git_sha"), "content_hash": run["content_digest"],
        "evidence_mode": run["evaluation_mode"], "input_snapshot": snap, "candidate_metrics": metrics}
    return header, horizons


def report_records(run, table, allowed_ids):
    """Membership comes from a live, invoker/RLS PostgreSQL read for this request."""
    records = []
    for product in run["products"]:
        if (product["product_id"], product["chain_id"]) not in allowed_ids:
            continue
        header, horizons = product_rows(run, product)
        if header is not None:
            if table == "forecast_calculations":
                records.append(header)
            else:
                records.extend(horizons)
    return records


def annual_preview(run, selected):
    """Reuse the sealed run for this RLS-checked set, including uncovered SKUs."""
    by_id = {p["product_id"]: p for p in run["products"]}
    scopes = {}
    for sku in sorted(selected, key=lambda row: (row["chain_id"], row["product_id"])):
        chain = sku["chain_id"]
        scope = scopes.setdefault(chain, {"chain_id": chain, "objective": "Pedido", "issue_period": run["cutoff"],
            "latest_actual_period": run["cutoff"], "source_context": run["source_context"], "status": "PREVIEW",
            "mode": run["evaluation_mode"], "engine_version": run["engine_version"], "planning_run_id": run["run_id"],
            "products": [], "selection": {"product_candidates": [], "suggested_references": {}},
            "dataset_hash": run["training_fingerprint"], "aggregates": []})
        pid = sku["product_id"]
        item = by_id.get(pid)
        if item and item["chain_id"] != chain:
            raise PreviewError("LINEAGE_MISMATCH", 409)
        header, horizons = product_rows(run, item) if item else (None, [])
        curve = [{**r, "target_period": r["target_period"][:7], "forecast_towell": r["ensemble_value"]}
                 for r in horizons if r["statistical_value"] is not None and r["ensemble_value"] is not None]
        scope["products"].append({"product_id": pid, "description": sku.get("product_description"),
            "product_code": sku.get("product_code"), "category_id": sku.get("category_id"),
            "forecast_status": "ACTIVE" if curve else "INSUFFICIENT", "horizons": curve})
        if header:
            scope["selection"]["product_candidates"].extend(header["candidate_metrics"].values())
    return list(scopes.values())
