"""Isolate a diagnosed product-method history failure at the engine boundary."""
from __future__ import annotations

from services.forecast_engine import retrospective
from services.forecast_engine.engine import (
    ENGINE_VERSION, add_month, forecast_dataset, normalize_retrospective_dataset,
)


def _unsupported_sample(error, rows, cutoff):
    selected, prediction = None, None
    trace = error.__traceback__
    while trace:
        frame = trace.tb_frame
        if frame.f_code is retrospective.forecast_chain.__code__:
            selected = frame.f_locals
        elif frame.f_code is retrospective.predict.__code__:
            prediction = frame.f_locals
        trace = trace.tb_next
    if selected is None or prediction is None:
        return None
    product, history = selected.get("item"), selected.get("history")
    name = prediction.get("name")
    required = retrospective.REQUIREMENTS.get(name)
    if (product is None or not isinstance(history, list) or not isinstance(required, int)
            or str(error) != f"requiere {required} periodos"
            or selected.get("choices", {}).get(product.product_id) != name
            or prediction.get("history") != history or not 0 < len(history) < required
            or prediction.get("horizon") not in range(1, 13)):
        return None
    own = [row for row in rows if row.get("chain_id") == product.chain_id and row.get("product_id") == product.product_id]
    normalized = normalize_retrospective_dataset(own, cutoff)
    if len(normalized) != 1 or normalized[0].history(cutoff) != history:
        return None
    periods = {add_month(cutoff, -offset) for offset in range(len(history))}
    source = sorted((row for row in own if row["period"] in periods), key=lambda row: row["period"])
    return {
        "chain_id": product.chain_id, "product_id": product.product_id,
        "reason": "MODEL_CURRENT_HISTORY_INSUFFICIENT", "model": name,
        "available_history": len(history), "required_history": required,
        "horizon": prediction["horizon"], "cutoff": cutoff,
        "history_periods": [row["period"] for row in source],
        "history_values": [str(row["value"]) for row in source],
        "source_ids": [row.get("source_id") for row in source],
    }


def execute_forecast(records, cutoff, *, compute=forecast_dataset, **kwargs):
    """Retry the unchanged engine after removing only a proven failing product."""
    remaining = list(records)
    exclusions = []
    maximum = len({(row.get("chain_id"), row.get("product_id")) for row in remaining})
    for _ in range(maximum + 1):
        try:
            return compute(remaining, cutoff, **kwargs), exclusions
        except ValueError as error:
            if kwargs.get("evidence_mode") != "RETROSPECTIVE_TRAINING":
                raise
            excluded = _unsupported_sample(error, remaining, cutoff)
            if excluded is None:
                raise
            exclusions.append(excluded)
            remaining = [row for row in remaining
                         if (row["chain_id"], row["product_id"]) != (excluded["chain_id"], excluded["product_id"])]
            if not remaining:
                return {"engine_version": ENGINE_VERSION, "issue_period": cutoff,
                        "objective": kwargs.get("objective", "Venta"), "chains": []}, exclusions
    raise ValueError("forecast_recovery_did_not_progress")
