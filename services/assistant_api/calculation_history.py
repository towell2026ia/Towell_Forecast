"""Immutable E4 calculation payloads and deterministic LIVE arithmetic.

This module has no database, research, or Champion side effects. The database
transaction boundary is deliberately separate from mathematical validation.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from services.forecast_engine.engine import add_month
from .persistence import content_hash
from .supabase_access import PreviewError

CANDIDATES = ("STATISTICAL", "ML", "ENSEMBLE")
RECALCULATION_REASONS = frozenset({
    "Nuevo mes disponible", "Nueva información del cliente", "Cambio comercial",
    "Cambio importante de demanda", "Revisión gerencial", "Nueva evidencia externa",
    "Corrección de datos", "Otro",
})
DEVIATION_REASONS = frozenset({
    "Información directa del cliente", "Promoción comercial", "Cambio de precio",
    "Desabasto esperado", "Apertura/cierre de tiendas", "Cambio de distribución",
    "Pedido extraordinario", "Evento especial", "Experiencia comercial", "Otro",
})


def _amount(value: Any, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        raise PreviewError("CALCULATION_INVALID", 409) from None
    if not number.is_finite() or number < 0:
        raise PreviewError("CALCULATION_INVALID", 409)
    return float(number)


def _complete(rows: list[dict[str, Any]], issue: str) -> bool:
    return len(rows) == 12 and {row.get("horizon") for row in rows} == set(range(1, 13)) and all(
        row.get("target_period", "")[:7] == add_month(issue, row["horizon"]) for row in rows
    )


def snapshot_from_preview(preview: dict[str, Any], *, product_id: str, preview_id: str,
                          actor_id: str, git_sha: str, input_observation_ids: list[str] | None = None,
                          research_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Freeze exactly the displayed product, not a scope aggregate or re-run."""
    product = next((p for p in preview.get("products", []) if p.get("product_id") == product_id), None)
    issue = preview.get("issue_period", "")
    if preview.get("status") != "PREVIEW" or not product or not _complete(product.get("horizons") or [], issue):
        raise PreviewError("PREVIEW_NOT_READY", 409)
    candidates = [row for row in (preview.get("selection") or {}).get("product_candidates", [])
                  if row.get("product_id") == product_id]
    selected = (preview.get("selection") or {}).get("suggested_references", {}).get(product_id)
    if not selected:
        selected = (preview.get("selection") or {}).get("suggested_reference")
    if selected and selected.get("product_id") not in {None, product_id}:
        raise PreviewError("LINEAGE_MISMATCH", 409)
    metric_by_family = {str(row.get("family", "")).upper(): row for row in candidates}
    candidate_horizons = {}
    for family, candidate in metric_by_family.items():
        rows = candidate.get("horizons") or []
        if rows and not _complete(rows, issue):
            raise PreviewError("CALCULATION_INVALID", 409)
        if rows:
            candidate_horizons[family] = {row["horizon"]: row for row in rows}
    ensemble_weight = metric_by_family.get("ENSEMBLE", {}).get("statistical_weight")
    if ensemble_weight is not None:
        ensemble_weight = _amount(ensemble_weight)
        if ensemble_weight > 1:
            raise PreviewError("CALCULATION_INVALID", 409)
    horizons = []
    for source in sorted(product["horizons"], key=lambda row: row["horizon"]):
        statistical = _amount(source.get("statistical_value"))
        horizon = source["horizon"]
        # The scope preview ML curve can differ from the product ML winner.
        # Freeze each product candidate's own H1-H12, never borrow the scope curve.
        ml_row = candidate_horizons.get("ML", {}).get(horizon)
        ensemble_row = candidate_horizons.get("ENSEMBLE", {}).get(horizon)
        ml = _amount(ml_row.get("value"), nullable=True) if ml_row else None
        ensemble = _amount(ensemble_row.get("value"), nullable=True) if ensemble_row else None
        ensemble_ml = _amount(ensemble_row.get("ml_component"), nullable=True) if ensemble_row else None
        if ensemble is not None and (ensemble_weight is None or ensemble_ml is None or
                                     not metric_by_family.get("ENSEMBLE", {}).get("ensemble_ml_model") or
                                     abs(ensemble - (statistical * ensemble_weight + ensemble_ml * (1 - ensemble_weight))) > .02):
            raise PreviewError("CALCULATION_INVALID", 409)
        basis = source.get("band_basis") or "INSUFFICIENT"
        bands = [_amount(source.get(key), nullable=True) for key in ("p10", "p50", "p90", "p95")]
        # P50 is the literal provisional central curve, not a certified band.
        # Never derive it from another candidate or reconstruct it on readback.
        if bands[1] is None or bands[1] != _amount(source.get("forecast_towell")):
            raise PreviewError("CALCULATION_INVALID", 409)
        if basis == "INSUFFICIENT":
            bands = [None, bands[1], None, None]
        elif any(value is None for value in bands) or bands != sorted(bands):
            raise PreviewError("CALCULATION_INVALID", 409)
        count = int(source.get("band_observations") or 0)
        if basis not in {"PRODUCT", "CATEGORY", "CHAIN", "INSUFFICIENT"} or count < 0 or (basis == "INSUFFICIENT") != (bands[0] is None):
            raise PreviewError("CALCULATION_INVALID", 409)
        horizons.append({
            "horizon": source["horizon"], "target_period": source["target_period"][:7] + "-01",
            "statistical_value": statistical, "ml_value": ml, "ensemble_value": ensemble,
            "ensemble_ml_component": ensemble_ml if ensemble is not None else None,
            "ensemble_ml_model": metric_by_family.get("ENSEMBLE", {}).get("ensemble_ml_model") if ensemble is not None else None,
            "statistical_model": source.get("statistical_model"),
            "ml_model": metric_by_family.get("ML", {}).get("model") if ml is not None else None,
            "statistical_weight": ensemble_weight if ensemble is not None else None,
            "ml_weight": 1 - ensemble_weight if ensemble is not None else None,
            "p10": bands[0], "p50": bands[1], "p90": bands[2], "p95": bands[3],
            "band_basis": basis, "band_observations": count,
            "band_status": "AVAILABLE" if basis != "INSUFFICIENT" else "INSUFFICIENT_BAND_EVIDENCE",
        })
    if any(row["ml_value"] is None for row in horizons) and any(row["ml_value"] is not None for row in horizons):
        raise PreviewError("CALCULATION_INVALID", 409)
    if any(row["ensemble_value"] is None for row in horizons) and any(row["ensemble_value"] is not None for row in horizons):
        raise PreviewError("CALCULATION_INVALID", 409)
    payload = {
        "chain_id": preview["chain_id"], "product_id": product_id, "objective": preview["objective"],
        "issue_period": issue + "-01", "cutoff_at": preview["cutoff"],
        "preview_id": preview_id, "data_snapshot_hash": preview["dataset_hash"],
        "input_observation_ids": sorted(input_observation_ids or []),
        "product_snapshot": {key: product.get(key) for key in ("product_code", "description", "category_id", "forecast_status")},
        "candidates": {family: metric_by_family.get(family) for family in CANDIDATES},
        "suggested_reference": selected, "research_snapshot": research_snapshot,
        "scope_leader": (preview.get("selection") or {}).get("scope_leader"),
        "evidence_mode": preview.get("evidence_mode"), "engine_version": preview["engine_version"],
        "git_sha": git_sha, "actor_id": actor_id, "horizons": horizons,
    }
    payload["content_hash"] = content_hash(payload)
    return payload


def selection_curve(calculation: dict[str, Any], candidate: str) -> list[dict[str, Any]]:
    if calculation.get("status") not in {"READY_FOR_DECISION", "DECIDED", "SUPERSEDED"} or candidate not in CANDIDATES:
        raise PreviewError("SELECTION_NOT_READY", 409)
    key = {"STATISTICAL": "statistical_value", "ML": "ml_value", "ENSEMBLE": "ensemble_value"}[candidate]
    rows = calculation.get("horizons") or []
    if not _complete(rows, str(calculation.get("issue_period", ""))[:7]) or any(row.get(key) is None for row in rows):
        raise PreviewError("SELECTION_NOT_READY", 409)
    return [{"horizon": row["horizon"], "target_period": row["target_period"], "value": _amount(row[key])}
            for row in sorted(rows, key=lambda row: row["horizon"])]


def validate_selection(calculation: dict[str, Any], candidate: str, reason: str | None, comment: str | None) -> list[dict[str, Any]]:
    curve = selection_curve(calculation, candidate)
    suggested = ((calculation.get("suggested_reference") or {}).get("family") or "").upper()
    if candidate != suggested and (reason not in DEVIATION_REASONS):
        raise PreviewError("DECISION_REASON_REQUIRED", 422)
    if reason == "Otro" and not (comment or "").strip():
        raise PreviewError("DECISION_REASON_REQUIRED", 422)
    return curve


def live_rows(calculations: list[dict[str, Any]], *, period: str, sale: dict[str, Any],
              order: dict[str, Any] | None = None, delivery: dict[str, Any] | None = None,
              selections: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Evaluate every mature issue at its ORIGINAL horizon against one sale version."""
    actual = _amount(sale["value"])
    order_value = _amount(order["value"], nullable=True) if order else None
    delivery_value = _amount(delivery["value"], nullable=True) if delivery else None
    result = []
    for calculation in calculations:
        if calculation.get("objective") != "Venta":
            continue
        matching = [row for row in calculation.get("horizons", []) if row["target_period"][:7] == period[:7]]
        if len(matching) != 1:
            continue
        horizon = matching[0]
        selection = (selections or {}).get(calculation["id"])
        selected = selection.get("selected_candidate") if selection else None
        suggested = ((calculation.get("suggested_reference") or {}).get("family") or "").upper()
        forecast_values = {"STATISTICAL": horizon.get("statistical_value"), "ML": horizon.get("ml_value"),
                           "ENSEMBLE": horizon.get("ensemble_value")}
        if selected in forecast_values and forecast_values[selected] is not None:
            forecast_values["TOWELL_SELECTED"] = forecast_values[selected]
        for candidate, value in forecast_values.items():
            if value is None:
                continue
            forecast = _amount(value)
            result.append({"calculation_id": calculation["id"], "chain_id": calculation["chain_id"],
                "product_id": calculation["product_id"], "horizon": horizon["horizon"],
                "target_period": horizon["target_period"], "candidate": candidate,
                "forecast_value": forecast, "actual_sale": actual, "sale_observation_id": sale["id"],
                "sale_version_no": int(sale.get("version_no") or 1),
                "absolute_error": abs(actual - forecast), "signed_error": actual - forecast,
                "ape": abs(actual - forecast) / actual * 100 if actual > 0 else None,
                "order_value": order_value, "delivery_value": delivery_value,
                "order_observation_id": order.get("id") if order else None,
                "delivery_observation_id": delivery.get("id") if delivery else None,
                "suggested_candidate": suggested or None, "selected_candidate": selected})
    return result


def live_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ratio of sums, never an arithmetic average of APE/individual rates."""
    latest = {}
    for row in rows:
        identity = (row.get("calculation_id"), row["candidate"], int(row["horizon"]), row.get("target_period"))
        if identity not in latest or int(row.get("sale_version_no") or 0) > int(latest[identity].get("sale_version_no") or 0):
            latest[identity] = row
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in latest.values():
        groups[(row["candidate"], int(row["horizon"]))].append(row)
    output = {}
    for (candidate, horizon), group in groups.items():
        denominator = sum(float(row["actual_sale"]) for row in group)
        absolute = sum(float(row["absolute_error"]) for row in group)
        signed = sum(float(row["signed_error"]) for row in group)
        orders = sum(float(row["order_value"]) for row in group if row.get("order_value") is not None and row.get("delivery_value") is not None)
        delivered = sum(float(row["delivery_value"]) for row in group if row.get("order_value") is not None and row.get("delivery_value") is not None)
        output[f"{candidate}:H{horizon}"] = {"candidate": candidate, "horizon": horizon,
            "evaluations": len(group), "wape": absolute / denominator * 100 if denominator else None,
            "bias": signed / denominator * 100 if denominator else None,
            "fill_rate": delivered / orders * 100 if orders else None}
    return output


def learning_event(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise PreviewError("LIVE_EVIDENCE_MISSING", 409)
    by_candidate = {row["candidate"]: row for row in rows}
    mathematical = {name: row for name, row in by_candidate.items() if name in CANDIDATES}
    best = min(mathematical, key=lambda name: (mathematical[name]["absolute_error"], name))
    exemplar = rows[0]
    selected = by_candidate.get("TOWELL_SELECTED")
    suggested = mathematical.get(exemplar.get("suggested_candidate"))
    regret = (selected["absolute_error"] - mathematical[best]["absolute_error"]) if selected else None
    value_added = (suggested["absolute_error"] - selected["absolute_error"]) if selected and suggested and selected["selected_candidate"] != exemplar.get("suggested_candidate") else None
    signals = []
    if selected and selected["absolute_error"] > 0:
        signals.append("DEMAND_FORECAST_ERROR")
    order, delivery = exemplar.get("order_value"), exemplar.get("delivery_value")
    if order is not None and delivery is not None and order > 0 and delivery / order < .95:
        signals.extend(["SUPPLY_SHORTFALL", "LOW_FILL_RATE"])
    if value_added is not None:
        signals.append("DECISION_OUTPERFORMED_RECOMMENDATION" if value_added > 0 else "DECISION_UNDERPERFORMED_RECOMMENDATION")
    return {"calculation_id": exemplar["calculation_id"], "chain_id": exemplar["chain_id"],
        "product_id": exemplar["product_id"], "horizon": exemplar["horizon"],
        "target_period": exemplar["target_period"], "sale_observation_id": exemplar["sale_observation_id"],
        "suggested_candidate": exemplar.get("suggested_candidate"), "selected_candidate": exemplar.get("selected_candidate"),
        "best_candidate_actual": best, "order_value": order, "sale_value": exemplar["actual_sale"],
        "delivery_value": delivery, "decision_regret_absolute": regret,
        "decision_value_added_absolute": value_added, "signals": signals,
        "metrics": live_metrics(rows)}
