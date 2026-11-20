"""Project immutable rolling results into existing calculation/report DTOs."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from uuid import UUID, uuid5

from services.forecast_engine.engine import add_month
from .rolling_planning import curve


def sanitized(value, allowed_ids, allowed_chains, key=""):
    """Peer information remains useful only within the reader's current ACL."""
    if isinstance(value, dict):
        if (value.get("product_id") is not None and value["product_id"] not in allowed_ids
                or value.get("chain_id") is not None and value["chain_id"] not in allowed_chains):
            return None
        return {k: sanitized(v, allowed_ids, allowed_chains, k) for k, v in value.items()}
    if isinstance(value, list):
        if key.endswith("product_ids") or key in {"peers", "comparables"} and all(isinstance(v, str) for v in value):
            return [v for v in value if v in allowed_ids]
        if key.endswith("chain_ids"):
            return [v for v in value if v in allowed_chains]
        return [clean for v in value if (clean := sanitized(v, allowed_ids, allowed_chains)) is not None]
    return value


def scope_evaluation(run, products):
    families = ("STATISTICAL", "ML", "ENSEMBLE")
    common = {p["product_id"] for p in products if all(
        p.get("candidates", {}).get(f, {}).get("calculable") is True for f in families)}
    methods = {p["product_id"]: p.get("candidates", {}) for p in products}
    grouped = defaultdict(dict)
    duplicate = False
    for row in run.get("paired_evidence") or []:
        pid = row.get("product_id")
        if pid not in common or row.get("target_period", "9999") > run["observed_cutoff"] or row.get("basis", "OBSERVED") != "OBSERVED":
            continue
        try:
            stat = row["statistical"].get(row["selected_statistical_model"])
            ml = row["ml"].get(row.get("selected_ml_model"))
            global_ml = row["ml"].get(row.get("ensemble_ml_model"))
            weight = row.get("ensemble_statistical_weight")
            ensemble = stat if methods[pid]["ENSEMBLE"].get("model") == "Estadístico solo" else (
                Decimal(str(stat)) * Decimal(str(weight)) + Decimal(str(global_ml)) * (1 - Decimal(str(weight)))
                if stat is not None and weight is not None and global_ml is not None else None)
            actual = Decimal(str(row["actual"]))
            predictions = {f: Decimal(str(v)) for f, v in zip(families, (stat, ml, ensemble)) if v is not None}
            if len(predictions) != 3 or any(not v.is_finite() or v < 0 for v in [actual, *predictions.values()]):
                continue
            key = row["origin"], row["target_period"], row["horizon"]
            if pid in grouped[key]:
                duplicate = True
            grouped[key][pid] = actual, predictions
        except (KeyError, TypeError, InvalidOperation, ValueError):
            continue
    compared = [values for values in grouped.values() if set(values) == common and common]
    result = {"basis": "OBSERVED_ONLY_IDENTICAL_ORIGIN_TARGET_HORIZON_PRODUCTS", "bias_convention": "OBSERVED_MINUS_FORECAST",
        "product_ids": sorted(common), "covered_variants": len(common), "total_variants": len(products),
        "observations": sum(len(values) for values in compared), "windows": len(compared),
        "families": {}, "scope_leader": None, "certified": False, "reason": None}
    with localcontext() as ctx:
        ctx.prec = 100
        denominator = sum((v[0] for values in compared for v in values.values()), Decimal(0))
        for family in families:
            # Preserve each identity's error before summing. Opposing misses
            # on two colours cannot cancel each other in a scope WAPE.
            errors = [(v[0], v[1][family]) for values in compared for v in values.values()]
            wape = 100 * sum((abs(a - p) for a, p in errors), Decimal(0)) / denominator if denominator and not duplicate else None
            bias = 100 * sum((a - p for a, p in errors), Decimal(0)) / denominator if denominator and not duplicate else None
            result["families"][family] = {"wape": float(wape) if wape is not None else None, "bias": float(bias) if bias is not None else None,
                "observations": len(errors), "windows": len(compared), "denominator_text": str(denominator), "wape_text": str(wape) if wape is not None else None,
                "bias_text": str(bias) if bias is not None else None}
    if compared and denominator and not duplicate and common == {p["product_id"] for p in products}:
        leader = min(families, key=lambda f: (result["families"][f]["wape"], abs(result["families"][f]["bias"]), families.index(f)))
        result["scope_leader"] = {"family": leader, **result["families"][leader], "published": False}
    else:
        result["reason"] = ("DUPLICATE_EVALUATION_PAIRS" if duplicate else "INCOMPLETE_COMMON_VARIANT_COVERAGE" if len(common) != len(products)
                            else "ZERO_OBSERVED_DENOMINATOR" if compared else "NO_COMPARABLE_OBSERVED_PAIRS")
    return result


def report_rows(run, selected_ids, *, requested_ids, allowed_ids=None, allowed_chains=None):
    products = [deepcopy(p) for p in run["products"] if p["product_id"] in selected_ids]
    products = sanitized(products, allowed_ids or selected_ids, allowed_chains or {p["chain_id"] for p in products})
    calculations, horizons, provisionals = [], [], []
    context, start, end = run["source_context"], run["window_start"], run["window_end"]
    for product in products:
        pid, chain = product["product_id"], product["chain_id"]
        calculation_id = str(uuid5(UUID(run["run_id"]), pid))
        families = product.get("candidates", {})
        curves, metrics = {}, {}
        for family, candidate in families.items():
            try:
                values = curve(candidate, start, end)
            except Exception:
                continue
            if family == "COLD_START":
                provisionals.append({"product_id": pid, "chain_id": chain, "family": family,
                    "job_id": run["run_id"], "run_id": run["run_id"], "issue_period": run["cutoff"],
                    "model": candidate["model"], "method": candidate["model"], "origin": candidate.get("origin"),
                    "engine_version": run.get("engine_version"), "confidence": candidate.get("confidence", "LOW"),
                    "created_at": run["emitted_at"],
                    "horizons": values, "admission": candidate.get("admission"),
                    "incremental_effect": candidate.get("incremental_effect"), "metrics": candidate.get("metrics"),
                    "source_context": context, "composition": candidate.get("composition"),
                    "window_start": start, "window_end": end})
                continue
            curves[family] = values
            stats = candidate.get("metrics") or {}
            metrics[family] = {**deepcopy(stats), "family": family.lower(), "product_id": pid,
                "model": candidate["model"], "strategy": candidate["model"], "evidence_mode": run.get("evaluation_mode", "RETROSPECTIVE_TRAINING"),
                "evaluation_signature": candidate.get("evaluation_signature"), "observations": stats.get("observations", 0),
                "windows": stats.get("windows", 0), "horizons": [{"horizon": v["horizon"], "target_period": v["target_period"], "value": float(Decimal(v["value"]))} for v in values]}
        if not curves:
            continue
        cutoff = date.fromisoformat(run["cutoff"] + "-01")
        last = (cutoff.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        snapshot = {**deepcopy(product.get("input_snapshot") or {}), "source_context": context,
            "planning_run_id": run["run_id"], "planning_start": start, "planning_end": end,
            "window_start": start, "window_end": end, "bridge_horizons": [],
            "product": {"offline_provenance": {"emitted_at": run["emitted_at"]}},
            "champion_eligible": list(curves), "composition": {f: deepcopy(c.get("composition")) for f, c in families.items()},
            "observed_cutoff": run.get("observed_cutoff"), "model_cutoff": run["cutoff"]}
        calculations.append({"id": calculation_id, "product_id": pid, "chain_id": chain, "objective": "Pedido",
            "calculation_no": 1, "calculation_code": run["run_id"][:8], "issue_period": run["cutoff"] + "-01",
            "cutoff_at": last.isoformat() + "T23:59:59Z", "status": "READY_FOR_DECISION", "created_at": run["emitted_at"],
            "created_by": run.get("creator_id"), "preview_id": run["run_id"], "engine_version": run.get("engine_version"),
            "data_snapshot_hash": run.get("training_fingerprint", run["input_digest"]), "content_hash": run["content_digest"],
            "git_sha": run.get("git_sha"), "evidence_mode": run.get("evaluation_mode", "RETROSPECTIVE_TRAINING"),
            "input_snapshot": snapshot, "candidate_metrics": metrics})
        for h in range(1, 13):
            def get(family):
                return curves[family][h - 1] if family in curves else {}
            ensemble = get("ENSEMBLE")
            record = {"id": str(uuid5(UUID(calculation_id), str(h))), "calculation_id": calculation_id,
                "product_id": pid, "chain_id": chain, "horizon": h, "planning_horizon": h,
                "target_period": add_month(start, h - 1) + "-01", "p10": None, "p50": None, "p90": None, "p95": None,
                "band_basis": "INSUFFICIENT", "band_status": "INSUFFICIENT_BAND_EVIDENCE", "band_observations": 0}
            for family, field in (("STATISTICAL", "statistical_value"), ("ML", "ml_value"), ("ENSEMBLE", "ensemble_value")):
                value = get(family).get("value")
                record[field] = float(Decimal(value)) if value is not None else None
                record[field + "_text"] = value
            record.update(statistical_model=families.get("STATISTICAL", {}).get("model"), ml_model=families.get("ML", {}).get("model"),
                ensemble_ml_model=ensemble.get("ensemble_ml_model"), ensemble_ml_component=ensemble.get("ensemble_ml_component"),
                statistical_weight=ensemble.get("statistical_weight"), ml_weight=ensemble.get("ml_weight"))
            horizons.append(record)
    return {"run_id": run["run_id"], "window_start": start, "window_end": end, "source_context": context,
        "issue_period": run["cutoff"], "model_cutoff": run["cutoff"], "observed_cutoff": run.get("observed_cutoff"),
        "created_at": run["emitted_at"], "products": products, "product_ids": sorted(selected_ids),
        "requested_product_ids": sorted(requested_ids), "covered": len(products), "calculations": calculations,
        "horizons": horizons, "provisionals": provisionals, "content_digest": run["content_digest"],
        "composition": {p["product_id"]: deepcopy(p.get("composition")) for p in products},
        "evaluation": scope_evaluation(run, products), "decisions": []}
