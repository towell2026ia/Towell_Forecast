"""Versioned E3 quality and observed-service policies; no forecast mutation."""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from services.forecast_engine.selection_policy import SELECTION_POLICY

POLICY_VERSION = "E3-GATES-1.0.0"
POLICY_VERSION_2 = "E3-GATES-2.0.0"
TARGET_FILL_RATE = 95.0


@dataclass(frozen=True)
class QualityPolicy:
    version: str = POLICY_VERSION
    minimum_history_months: int = 18
    warning_continuity_rate: float = 0.85
    ready_continuity_rate: float = 0.95
    minimum_improvement_points: float = 0.0
    maximum_bias_deterioration: float = 5.0
    maximum_recent_deterioration: float = 5.0
    critical_horizon_degradation: float = 10.0
    target_fill_rate: float = TARGET_FILL_RATE

    @classmethod
    def selection_v2(cls) -> "QualityPolicy":
        return cls(version=POLICY_VERSION_2,
            minimum_history_months=SELECTION_POLICY.minimum_history_months,
            warning_continuity_rate=SELECTION_POLICY.warning_continuity,
            ready_continuity_rate=SELECTION_POLICY.ready_continuity,
            minimum_improvement_points=SELECTION_POLICY.minimum_improvement_points,
            maximum_bias_deterioration=SELECTION_POLICY.maximum_bias_deterioration,
            maximum_recent_deterioration=SELECTION_POLICY.maximum_recent_deterioration,
            critical_horizon_degradation=SELECTION_POLICY.maximum_critical_deterioration)


def _month_index(value: str) -> int:
    year, month = map(int, value[:7].split("-"))
    if not 1 <= month <= 12:
        raise ValueError("invalid_period")
    return year * 12 + month


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def data_quality(rows: list[dict[str, Any]], preview: dict[str, Any],
                 policy: QualityPolicy = QualityPolicy()) -> dict[str, Any]:
    """Assess the scope, while allowing individual cold-start products."""
    issue = preview["issue_period"][:7]
    issue_index = _month_index(issue)
    valid = [r for r in rows if _month_index(r["period"]) <= issue_index]
    identities: set[tuple[str, str, str]] = set()
    duplicate = False
    invalid = False
    unknown_availability = 0
    by_product: dict[str, set[str]] = {}
    periods: set[str] = set()
    for row in valid:
        key = (row.get("chain_id", ""), row.get("product_id", ""), row["period"][:7])
        if key in identities:
            duplicate = True
        identities.add(key)
        if row.get("chain_id") != preview["chain_id"] or not row.get("product_id"):
            invalid = True
        value = _number(row.get("value"))
        if value is None or value < 0:
            invalid = True
        if row.get("available_at") is None:
            unknown_availability += 1
        by_product.setdefault(row.get("product_id", ""), set()).add(row["period"][:7])
        periods.add(row["period"][:7])
    first = min(periods) if periods else None
    expected = issue_index - _month_index(first) + 1 if first else 0
    observed = len(periods)
    continuity = observed / expected if expected else 0.0
    missing = max(0, expected - observed)
    eligibility = preview.get("eligibility") or {}
    products = preview.get("products") or []
    product_ids = [product.get("product_id") for product in products]
    product_identities = [(product.get("product_code"), product.get("variant_code") or "") for product in products]
    if len(set(product_ids)) != len(product_ids) or len(set(product_identities)) != len(product_identities):
        duplicate = True
    products_total = int(eligibility.get("visible_products", len(products)))
    products_with_history = len(by_product)
    stat_eligible = int(eligibility.get("stat_eligible", 0))
    ml_eligible = int(eligibility.get("ml_eligible", 0))
    if duplicate:
        status = "IDENTITY_CONFLICT"
    elif invalid or not valid:
        status = "BLOCKED"
    elif missing and continuity < policy.warning_continuity_rate:
        status = "HISTORICAL_INCOMPLETE"
    elif observed < policy.minimum_history_months:
        status = "INSUFFICIENT_HISTORY"
    elif missing or continuity < policy.ready_continuity_rate or unknown_availability:
        status = "DATA_QUALITY_WARNING"
    else:
        status = "DATA_QUALITY_READY"
    return {"gate_type": "DATA_QUALITY", "status": status, "policy_version": policy.version,
        "history_months": observed, "expected_months": expected, "observed_months": observed,
        "missing_months": missing, "continuity_rate": round(continuity, 6), "gap_count": missing,
        "latest_period": max(periods) if periods else None, "earliest_period": first,
        "products_total": products_total, "products_with_history": products_with_history,
        "products_stat_eligible": stat_eligible, "products_ml_eligible": ml_eligible,
        "unknown_available_at": unknown_availability, "duplicate_identities": duplicate,
        "negative_or_invalid_values": invalid, "publication_allowed": status == "DATA_QUALITY_READY"}


def forecast_quality(preview: dict[str, Any], policy: QualityPolicy = QualityPolicy()) -> dict[str, Any]:
    """Compare with this scope's statistical baseline; never use a global WAPE cap."""
    if policy.version == POLICY_VERSION_2:
        return _forecast_quality_v2(preview, policy)
    statistical = preview.get("statistical") or {}
    ml = preview.get("ml") or {}
    leader = (preview.get("selection") or {}).get("preview_leader") or {}
    baseline = _number(statistical.get("retrospective_wape"))
    candidate = _number(leader.get("retrospective_wape"))
    if candidate is None:
        candidate = _number(leader.get("validation_wape"))
    bias = _number(leader.get("retrospective_bias"))
    if bias is None:
        bias = _number(leader.get("validation_bias"))
    baseline_bias = _number(statistical.get("retrospective_bias"))
    observations = leader.get("observations") or leader.get("validation_observations")
    horizons = leader.get("retrospective_validation_by_horizon") or leader.get("validation_by_horizon") or []
    degradation = max((_number(row.get("wape")) - baseline for row in horizons
        if baseline is not None and _number(row.get("wape")) is not None), default=None)
    if baseline is None:
        status = "NO_REFERENCE"
    elif candidate is None:
        status = "INSUFFICIENT_EVIDENCE"
    elif candidate > baseline - policy.minimum_improvement_points or (
            bias is not None and baseline_bias is not None and
            abs(bias) - abs(baseline_bias) > policy.maximum_bias_deterioration):
        status = "FORECAST_QUALITY_BLOCKED"
    elif degradation is not None and degradation > policy.critical_horizon_degradation:
        status = "FORECAST_QUALITY_WARNING"
    else:
        status = "FORECAST_QUALITY_READY"
    return {"gate_type": "FORECAST_QUALITY", "status": status, "policy_version": policy.version,
        "baseline_scope": preview.get("chain_id"), "baseline_model": "SELECTED_STATISTICAL",
        "baseline_wape": baseline, "candidate_wape": candidate, "candidate_bias": bias,
        "stat_wape": baseline, "stat_bias": baseline_bias,
        "ml_wape": _number(ml.get("retrospective_wape")),
        "ml_bias": _number(ml.get("retrospective_bias")),
        "observations": observations, "maximum_horizon_degradation": degradation,
        "no_degradation": status == "FORECAST_QUALITY_READY",
        "certified_point_in_time": bool(preview.get("temporal_certification"))}


def _forecast_quality_v2(preview: dict[str, Any], policy: QualityPolicy) -> dict[str, Any]:
    """New scope gate only: matched scope pairs, never product-vs-scope scores."""
    selection = preview.get("selection") or {}
    baseline_row = selection.get("scope_comparable_baseline") or {}
    leader = selection.get("scope_leader") or {}
    baseline = _number(baseline_row.get("retrospective_wape"))
    candidate = _number(leader.get("retrospective_wape"))
    bias = _number(leader.get("retrospective_bias"))
    baseline_bias = _number(baseline_row.get("retrospective_bias"))
    by_horizon = {row.get("horizon"): _number(row.get("wape")) for row in baseline_row.get("by_horizon", [])}
    critical = [(_number(row.get("wape")), by_horizon.get(row.get("horizon")))
                for row in leader.get("by_horizon", []) if row.get("horizon") in SELECTION_POLICY.critical_horizons]
    degradation = max((current - previous for current, previous in critical
                       if current is not None and previous is not None), default=None)
    if baseline is None or candidate is None or bias is None or baseline_bias is None:
        status = "INSUFFICIENT_EVIDENCE"
    elif abs(bias) > policy.maximum_bias_deterioration + abs(baseline_bias) or (
            leader.get("strategy") != "statistical" and
            candidate > baseline - policy.minimum_improvement_points):
        status = "FORECAST_QUALITY_BLOCKED"
    elif degradation is not None and degradation > policy.critical_horizon_degradation:
        status = "FORECAST_QUALITY_WARNING"
    else:
        status = "FORECAST_QUALITY_READY"
    return {"gate_type": "FORECAST_QUALITY", "status": status, "policy_version": policy.version,
        "baseline_scope": preview.get("chain_id"), "baseline_model": "MATCHED_SCOPE_STATISTICAL",
        "baseline_wape": baseline, "candidate_wape": candidate, "candidate_bias": bias,
        "stat_wape": baseline, "stat_bias": baseline_bias, "ml_wape": None, "ml_bias": None,
        "observations": leader.get("observations"), "maximum_horizon_degradation": degradation,
        "no_degradation": status == "FORECAST_QUALITY_READY",
        "certified_point_in_time": bool(preview.get("temporal_certification"))}


def observed_service(rows: list[dict[str, Any]],
                     policy: QualityPolicy = QualityPolicy()) -> dict[str, Any]:
    """Ratio of matched realized totals, not an average of per-product rates."""
    facts: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row in rows:
        metric = row.get("metric_code")
        if metric not in {"ORDER", "DELIVERY"}:
            continue
        try:
            value = Decimal(str(row["value"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        if value < 0 or not value.is_finite():
            continue
        key = (str(row.get("product_id")), str(row.get("period"))[:7])
        facts.setdefault(key, {})[metric] = value
    matched = [fact for fact in facts.values() if "ORDER" in fact and "DELIVERY" in fact]
    incomplete_pairs = len(facts) - len(matched)
    undefined_pairs = sum(fact["ORDER"] == 0 and fact["DELIVERY"] > 0 for fact in matched)
    order = sum((fact["ORDER"] for fact in matched), Decimal(0))
    delivery = sum((fact["DELIVERY"] for fact in matched), Decimal(0))
    if not matched or incomplete_pairs or undefined_pairs:
        rate = None
        status = "NOT_MEASURABLE"
    elif order == 0:
        rate = 100.0
        status = "TARGET_MET"
    else:
        rate = float(delivery / order * 100)
        status = "TARGET_MET" if rate >= policy.target_fill_rate else "TARGET_NOT_MET"
    return {"gate_type": "SERVICE_LEVEL", "status": status, "policy_version": policy.version,
        "observed_fill_rate": rate, "target_fill_rate": policy.target_fill_rate,
        "gap_pp": None if rate is None else rate - policy.target_fill_rate,
        "matched_product_periods": len(matched), "incomplete_product_periods": incomplete_pairs,
        "undefined_product_periods": undefined_pairs, "order_total": float(order),
        "delivery_total": float(delivery), "service_simulation_status": "SIMULATION_PENDING",
        "operational_recommendation": None}
