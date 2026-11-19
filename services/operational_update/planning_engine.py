"""An isolated calendar adapter for the accredited unified 2027 plan.

Statistical methods, ML factories, features and Ensemble selection are the
existing implementations. No historical forecast, stock or commercial proposal
is accepted as a training observation. Nothing here writes to a database.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import uuid

from services.forecast_engine.engine import (
    ENGINE_VERSION, ForecastPolicy, add_month, month_distance,
    normalize_retrospective_dataset, _metrics,
)
from services.forecast_engine.retrospective import forecast_chain
from services.forecast_engine.selection_policy import evaluation_signature
from services.assistant_api.forecast_execution import execute_forecast
from .unified_source import MASTER_SHA256, CONTRACT

DISTANCES = tuple(range(1, 16))
PLAN_DISTANCES = tuple(range(4, 16))
GROUP_FIELDS = ("chain_id", "account", "format", "model", "presentation", "commercial_code")
FAMILY_KEYS = {"statistical": "STATISTICAL", "ml": "ML", "ensemble": "ENSEMBLE"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def exact(value):
    return None if value is None else str(Decimal(str(value)))


def validate_input(rows, source_context):
    required = {"forecast_contract": CONTRACT, "target_file_sha256": MASTER_SHA256,
                "target_sheet": "Base_2024_2026", "target_metric": "ORDER",
                "objective": "Pedido", "training_cutoff": "2026-09",
                "planning_start": "2027-01", "planning_end": "2027-12"}
    if any(source_context.get(k) != v for k, v in required.items()):
        raise ValueError("UNIFIED_PLANNING_CONTEXT_UNACCREDITED")
    keys = set()
    for row in rows:
        if (row.get("objective") != "Pedido" or row.get("metric_code") != "ORDER"
                or row.get("training_eligible") is not True
                or row.get("source_file_sha256") != MASTER_SHA256
                or row.get("target_sheet") != "Base_2024_2026"
                or not str(row.get("source_reference", "")).startswith("Base_2024_2026!")
                or not "2024-01" <= row["period"] <= "2026-09"):
            raise ValueError("UNIFIED_PLANNING_INPUT_UNACCREDITED")
        key = row["chain_id"], row["product_id"], row["period"]
        if key in keys:
            raise ValueError("UNIFIED_PLANNING_DUPLICATE")
        keys.add(key)
    if not rows:
        raise ValueError("UNIFIED_PLANNING_EMPTY")


def evaluation_origins(cutoff="2026-09"):
    # All three origins can observe the entire distance4..15 window by cutoff.
    # This calendar depends on dates, never on quantities or model scores.
    last = add_month(cutoff, -max(PLAN_DISTANCES))
    return tuple(add_month(last, offset) for offset in (-2, -1, 0))


def _candidate(raw, family, product_id, forecast_rows, pairs):
    selected = next((c for c in raw["model_audit"]["product_candidates"]
                     if c["product_id"] == product_id and c["family"] == family), None)
    by_distance = {r["horizon"]: r for r in selected["horizons"]} if selected else {}
    current_history = bool(forecast_rows and forecast_rows[0].get("classification") != "Sin historial")
    own_pairs = [r for r in pairs if r["product_id"] == product_id]
    if family == "statistical":
        evidence = [r for r in own_pairs if r["selected_statistical_model"] in r["statistical"]]
    elif family == "ml":
        evidence = [r for r in own_pairs if r.get("selected_ml_model") in r["ml"]]
    else:
        evidence = [r for r in own_pairs if _pair_prediction(r, "ENSEMBLE") is not None]
    weight = selected.get("statistical_weight") if selected else None
    if family == "statistical":
        weight = 1.0
    elif family == "ml":
        weight = 0.0
    model = selected.get("model") if selected else None
    if family == "statistical" and model is None:
        model = next((r["statistical_model"] for r in forecast_rows), None)
    if family == "statistical" and current_history:
        by_distance = {r["horizon"]: {"value": r["statistical"]} for r in forecast_rows}
    elif family == "ml" and selected is None and current_history:
        # A global model can generate this variant without an individual winner.
        # Preserve that literal curve/method, with no invented product metrics.
        by_distance = {r["horizon"]: {"value": r["ml"]} for r in forecast_rows if r["ml"] is not None}
        model = next((r["ml_model"] for r in forecast_rows if r["ml"] is not None), None)
    fallback = family == "ensemble" and selected is None and forecast_rows and all(
        r.get("model_strategy") in {"statistical", "statistical_fallback"} for r in forecast_rows)
    if fallback and current_history:
        weight, model = 1.0, "Estadístico solo"
        evidence = [r for r in own_pairs if r["selected_statistical_model"] in r["statistical"]]
        by_distance = {r["horizon"]: {"value": r["forecast_towell"], "ml_component": None}
                       for r in forecast_rows}
    values = [(r["actual"], r["statistical"][r["selected_statistical_model"]] if fallback else
               _pair_prediction(r, FAMILY_KEYS[family])) for r in evidence]
    values = [(actual, prediction) for actual, prediction in values if prediction is not None]
    metrics = {**_metrics(values), "observations": len(values),
               "windows": len({r["origin"] for r in evidence})}
    signature = evaluation_signature(chain_id=forecast_rows[0]["chain_id"],
        category_id=forecast_rows[0]["category"], product_id=product_id, objective="Pedido",
        issue_period="2026-09", evaluation_mode="RETROSPECTIVE_TRAINING", rows=evidence) if evidence else None
    horizons = []
    for index, distance in enumerate(PLAN_DISTANCES, 1):
        row = by_distance.get(distance)
        supported = any(r["horizon"] == distance for r in evidence)
        value = row["value"] if row is not None and current_history else None
        horizons.append({"horizon": index, "planning_horizon": index,
            "forecast_distance": distance, "target_period": add_month("2026-09", distance),
            "value": exact(value), "status": "AVAILABLE" if value is not None and supported else
                "PROVISIONAL_WITHOUT_EVALUATION" if value is not None else "SIN_COBERTURA",
            "reason": None if value is not None and supported else "NO_COMPARABLE_LONG_DISTANCE_EVIDENCE" if value is not None
                else "NO_CURRENT_CONTIGUOUS_HISTORY" if forecast_rows and not current_history else "NO_COMPUTED_CANDIDATE",
            "statistical_weight": weight,
            "ml_weight": None if weight is None else round(1 - weight, 12),
            "ensemble_ml_model": selected.get("ensemble_ml_model") if selected and family == "ensemble" else None,
            "ensemble_ml_component": exact(row.get("ml_component")) if row and family == "ensemble" else None,
            "evaluation_observations": sum(r["horizon"] == distance for r in evidence)})
    complete = all(r["value"] is not None for r in horizons)
    eligible = bool(complete and metrics["wape"] is not None and metrics["observations"] >= 6
                    and metrics["windows"] >= 3 and all(h["evaluation_observations"] > 0 for h in horizons))
    return {"family": FAMILY_KEYS[family], "model": model,
        "origin": "PYTHON_EXISTING_ENGINE", "eligible": eligible,
        "status": "PROVISIONAL" if complete else "SIN_COBERTURA",
        "reason": None if eligible else "PROVISIONAL_WITHOUT_PRODUCT_EVALUATION" if complete and metrics["wape"] is None else
            "PLANNING_HORIZON_EVALUATION_INCOMPLETE" if complete and any(h["evaluation_observations"] == 0 for h in horizons) else
            "NO_COMPARABLE_CANDIDATE" if selected is None else
            "INSUFFICIENT_PRODUCT_EVIDENCE" if complete else "INCOMPLETE_PLANNING_COVERAGE",
        "metrics": metrics, "evaluation_signature": signature,
        "evidence_mode": "RETROSPECTIVE_TRAINING", "certified_wape": None,
        "horizons": horizons}


def _pair_prediction(row, family):
    stat = row["statistical"].get(row["selected_statistical_model"])
    if family == "STATISTICAL":
        return stat
    if family == "ML":
        return row["ml"].get(row["selected_ml_model"])
    weight = row.get("ensemble_statistical_weight")
    ml = row["ml"].get(row.get("ensemble_ml_model"))
    return stat * weight + ml * (1 - weight) if weight is not None and stat is not None and ml is not None else None


def _bridge(raw, product_id, forecast):
    candidates = {c["family"]: c for c in raw["model_audit"]["product_candidates"] if c["product_id"] == product_id}
    ml = candidates.get("ml")
    ensemble = candidates.get("ensemble")
    ml_points = {r["horizon"]: r for r in ml["horizons"]} if ml else {}
    ensemble_points = {r["horizon"]: r for r in ensemble["horizons"]} if ensemble else {}
    result = []
    current_history = bool(forecast and forecast[0].get("classification") != "Sin historial")
    for source in forecast:
        h = source["horizon"]
        if h > 3:
            continue
        mixed = ensemble_points.get(h)
        stat_only = not mixed and source.get("model_strategy") in {"statistical", "statistical_fallback"}
        weight = ensemble.get("statistical_weight") if mixed else 1.0 if stat_only else None
        result.append({"horizon": h, "forecast_distance": h, "target_period": source["target_period"],
            "statistical_value": exact(source["statistical"]) if current_history else None,
            "ml_value": exact(ml_points.get(h, {}).get("value") if ml else source.get("ml")) if current_history else None,
            "ml_model": ml["model"] if ml else source.get("ml_model"),
            "ml_selection_basis": "PRODUCT_WINNER" if ml else "SCOPE_MODEL_NO_PRODUCT_WINNER" if source.get("ml") is not None else None,
            "ensemble_value": exact(mixed["value"] if mixed else source["statistical"] if stat_only else None) if current_history else None,
            "ensemble_ml_component": exact(mixed.get("ml_component")) if mixed else None,
            "ensemble_ml_model": ensemble.get("ensemble_ml_model") if mixed else None,
            "statistical_weight": weight, "ml_weight": round(1 - weight, 12) if weight is not None else None})
    return result


def aggregate_models(products, paired_evidence):
    groups = defaultdict(list)
    for product in products:
        groups[tuple(product.get(k) for k in GROUP_FIELDS)].append(product)
    result = []
    for key, members in sorted(groups.items(), key=lambda item: canonical(item[0])):
        record = {**dict(zip(GROUP_FIELDS, key)), "model_group_key": canonical(key),
                  "product_ids": sorted(p["product_id"] for p in members), "candidates": {}}
        ids = set(record["product_ids"])
        for family in FAMILY_KEYS.values():
            horizons = []
            for index in range(12):
                rows = [p["candidates"][family]["horizons"][index] for p in members]
                values = [r["value"] for r in rows if r["value"] is not None]
                with localcontext() as context:
                    context.prec = 100
                    total = str(sum((Decimal(v) for v in values), Decimal(0))) if values else None
                horizons.append({"horizon": index + 1, "planning_horizon": index + 1,
                    "forecast_distance": index + 4, "target_period": rows[0]["target_period"],
                    "value": total, "covered_variants": len(values), "expected_variants": len(rows),
                    "status": "COMPLETE" if len(values) == len(rows) else "TOTAL_PARCIAL"})
            # Model metrics compare sums on identical origin/target/member sets.
            # Micro metrics remain separately labelled, never mean product WAPE.
            paired_groups = defaultdict(list)
            pairs = []
            methods = {p["product_id"]: p["candidates"][family]["model"] for p in members}
            for row in paired_evidence:
                if row["product_id"] not in ids or row["horizon"] not in PLAN_DISTANCES:
                    continue
                prediction = _pair_prediction(row, "STATISTICAL" if family == "ENSEMBLE" and
                                              methods[row["product_id"]] == "Estadístico solo" else family)
                if prediction is not None:
                    pairs.append((row["actual"], prediction))
                    paired_groups[row["origin"], row["target_period"], row["horizon"]].append(
                        (row["product_id"], row["actual"], prediction))
            model_pairs = []
            for paired in paired_groups.values():
                if {p[0] for p in paired} == ids and len(paired) == len(ids):
                    model_pairs.append((float(sum((Decimal(str(p[1])) for p in paired), Decimal(0))),
                                        float(sum((Decimal(str(p[2])) for p in paired), Decimal(0)))))
            record["candidates"][family] = {"family": family, "horizons": horizons,
                "metrics": _metrics(model_pairs), "evaluation_observations": len(model_pairs),
                "micro_metrics": _metrics(pairs), "micro_observations": len(pairs),
                "metric_basis": "MODEL_SUM_COMMON_ORIGIN_TARGET_COMPLETE_VARIANTS", "published": False}
        result.append(record)
    return result


def reproject_saved_run(directory, output_dir):
    """Correct presentation from saved results/pairs without fitting any model."""
    directory, output_dir = Path(directory), Path(output_dir)
    result = json.loads((directory / "planning_run.json").read_text(encoding="utf-8"))
    expected = result.pop("content_digest")
    if digest(result) != expected:
        raise ValueError("UNIFIED_PLANNING_SAVED_DIGEST_MISMATCH")
    for name, sha in result.get("artifacts_sha256", {}).items():
        if Path(name).name != name or hashlib.sha256((directory / name).read_bytes()).hexdigest() != sha:
            raise ValueError("UNIFIED_PLANNING_SAVED_ARTIFACT_MISMATCH")
    pairs = json.loads((directory / "Paired_Evidence.json").read_text(encoding="utf-8"))
    scopes = {p.stem[6:]: json.loads(p.read_text(encoding="utf-8")) for p in directory.glob("Scope_*.json")}
    for product in result["products"]:
        raw = scopes.get(product["chain_id"])
        if raw is None:
            continue
        product_id = product["product_id"]
        forecast = [r for r in raw["forecast_towell"] if r["product_id"] == product_id]
        product["candidates"] = {FAMILY_KEYS[f]: _candidate(raw, f, product_id, forecast, pairs) for f in FAMILY_KEYS}
        product["bridge_horizons"] = _bridge(raw, product_id, forecast)
        product["input_snapshot"]["bridge_horizons"] = product["bridge_horizons"]
    result["models"] = aggregate_models(result["products"], pairs)
    result["saved_result_origin"] = {"run_id": result["run_id"], "content_digest": expected,
                                    "recalculated": False}
    result["content_digest"] = digest(result)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "planning_run.json").write_text(canonical(result), encoding="utf-8")
    return result


def run_planning(rows, metadata, source_context, *, output_dir=None, on_stage=None):
    """Compute one immutable planning run from only sealed unified ORDER rows."""
    rows = list(rows)
    validate_input(rows, source_context)
    metadata = list(metadata.values()) if isinstance(metadata, dict) else list(metadata)
    by_id = {row["product_id"]: row for row in metadata}
    if len(by_id) != len(metadata) or any(r["product_id"] not in by_id for r in rows):
        raise ValueError("UNIFIED_PLANNING_METADATA_UNACCREDITED")
    series = normalize_retrospective_dataset(rows, "2026-09", objective="Pedido")
    scopes = defaultdict(list)
    for item in series:
        scopes[item.chain_id].append(item)
    emitted = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    products, all_pairs, audits = [], [], []
    directory = Path(output_dir) if output_dir else None
    if directory:
        directory.mkdir(parents=True, exist_ok=False)
    for chain_id, members in sorted(scopes.items()):
        if on_stage:
            on_stage({"chain_id": chain_id, "stage": "START", "products": len(members)})
        available_periods = {period for item in members for period in item.observations}
        origins = tuple(period for period in evaluation_origins() if period in available_periods)
        if not origins:
            raise ValueError("UNIFIED_PLANNING_NO_COMMON_ORIGIN")
        def compute(remaining, cutoff, **_):
            return forecast_chain(normalize_retrospective_dataset(remaining, cutoff, objective="Pedido"),
                cutoff, ForecastPolicy(), None, horizons=DISTANCES, evaluation_horizons=PLAN_DISTANCES,
                max_folds=36, evaluation_origins=origins, retain_pairs=True,
                on_stage=(lambda stage: on_stage({"chain_id": chain_id, "stage": stage})) if on_stage else None)
        raw, exclusions = execute_forecast([r for r in rows if r["chain_id"] == chain_id], "2026-09",
            compute=compute, objective="Pedido", evidence_mode="RETROSPECTIVE_TRAINING")
        if not raw.get("model_audit"):
            raw.update(model_audit={"product_candidates": []}, forecast_towell=[], selection={})
        raw["engine_exclusions"] = exclusions
        pairs = raw.pop("paired_evidence", [])
        all_pairs.extend(pairs)
        for item in members:
            meta = by_id[item.product_id]
            forecast = [r for r in raw["forecast_towell"] if r["product_id"] == item.product_id]
            bridge = _bridge(raw, item.product_id, forecast)
            product = {**meta, "product_id": item.product_id, "chain_id": chain_id,
                "model_group_key": canonical([meta.get(k) if k != "chain_id" else chain_id for k in GROUP_FIELDS]),
                "candidates": {FAMILY_KEYS[f]: _candidate(raw, f, item.product_id, forecast, pairs) for f in FAMILY_KEYS},
                "bridge_horizons": bridge,
                "input_snapshot": {"source_context": source_context, "bridge_horizons": bridge,
                    "objective": "Pedido", "training_cutoff": "2026-09",
                    "source_references": [r["source_reference"] for r in rows if r["product_id"] == item.product_id]}}
            products.append(product)
        audits.append({"chain_id": chain_id, "model_audit": raw["model_audit"],
                       "selection": raw["selection"], "evaluation_origins": origins})
        if directory:
            (directory / f"Scope_{chain_id}.json").write_text(canonical(raw), encoding="utf-8")
            (directory / f"Pairs_{chain_id}.json").write_text(canonical(pairs), encoding="utf-8")
        if on_stage:
            on_stage({"chain_id": chain_id, "stage": "DONE"})
    # Catalogue-only identities still contribute to the expected colour coverage.
    covered = {p["product_id"] for p in products}
    empty_raw = {"model_audit": {"product_candidates": []}}
    for product_id, meta in by_id.items():
        if product_id in covered:
            continue
        products.append({**meta, "product_id": product_id,
            "model_group_key": canonical([meta.get(k) for k in GROUP_FIELDS]),
            "candidates": {FAMILY_KEYS[f]: _candidate(empty_raw, f, product_id, [], []) for f in FAMILY_KEYS},
            "bridge_horizons": [], "input_snapshot": {"source_context": source_context,
                "bridge_horizons": [], "objective": "Pedido", "training_cutoff": "2026-09", "source_references": []}})
    result = {"schema": "unified-planning-run-1", "run_id": run_id,
        "source_context": dict(source_context), "objective": "Pedido", "planning_year": 2027,
        "cutoff": "2026-09", "emitted_at": emitted, "engine_version": ENGINE_VERSION,
        "training_fingerprint": digest(rows), "evaluation_mode": "RETROSPECTIVE_TRAINING",
        "evaluation_origins": list(evaluation_origins()), "forecast_distances": list(DISTANCES),
        "planning_distances": list(PLAN_DISTANCES), "champion_published": False,
        "products": products, "models": aggregate_models(products, all_pairs),
        "audits": audits, "coverage": {"products": len(products), "scopes": len(scopes)},
        "restrictions": ["NOT_WAPE_LIVE", "NO_AUTOMATIC_CHAMPION", "THREE_COMMON_ORIGINS",
                         "ML_GLOBAL_WITHIN_EXISTING_CHAIN_SCOPE", "NO_COMMERCIAL_PLAN_2027_IN_TRAINING"]}
    if directory:
        (directory / "Paired_Evidence.json").write_text(canonical(all_pairs), encoding="utf-8")
        result["artifacts_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(directory.glob("*.json"))}
    result["content_digest"] = digest(result)
    if directory:
        (directory / "planning_run.json").write_text(canonical(result), encoding="utf-8")
    return result
