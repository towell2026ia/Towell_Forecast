"""FT-SELECTION-2.0: deterministic, product-grain preview recommendation.

This module never publishes a forecast or writes to champion_registry.  A
retrospective comparison is advisory even when it has many observations.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SelectionPolicy:
    version: str = "FT-SELECTION-2.0"
    minimum_history_months: int = 18
    selection_origins: int = 6
    certification_origins: int = 3
    product_observations: int = 6
    ensemble_observations: int = 24
    ensemble_windows: int = 3
    minimum_improvement_points: float = 0.25
    maximum_champion_absolute_bias: float = 20.0
    maximum_bias_deterioration: float = 5.0
    maximum_stability_deterioration: float = 10.0
    maximum_recent_deterioration: float = 10.0
    critical_horizons: tuple[int, ...] = (1, 2, 3)
    maximum_critical_deterioration: float = 2.0
    minimum_band_residuals: int = 3
    warning_continuity: float = 0.85
    ready_continuity: float = 0.95


SELECTION_POLICY = SelectionPolicy()


def select_band_rows(rows: list[dict[str, Any]], *, product_id: str, category: str,
                     horizon: int, policy: SelectionPolicy = SELECTION_POLICY) -> tuple[str, list[dict[str, Any]]]:
    """Select enough residual pairs at one literal horizon, never pool horizons."""
    same_horizon = [row for row in rows if row["horizon"] == horizon]
    for basis, eligible in (("PRODUCT", [row for row in same_horizon if row["product_id"] == product_id]),
                            ("CATEGORY", [row for row in same_horizon if row["category"] == category]),
                            ("CHAIN", same_horizon)):
        if len(eligible) >= policy.minimum_band_residuals:
            return basis, eligible
    return "INSUFFICIENT", []


def evaluation_signature(*, chain_id: str, category_id: str, product_id: str,
                         objective: str, issue_period: str, evaluation_mode: str,
                         rows: list[dict[str, Any]]) -> str:
    """Hash the exact paired evidence, never a scope aggregate or a row count.

    If a provider has no physical observation id, the explicit logical key
    identifies the same actual at the same product/period; it is not a claim
    about source availability or a fabricated temporal timestamp.
    """
    evidence = sorted([{"origin": row["origin"], "target": row["target_period"],
                        "horizon": row["horizon"],
                        "actual_observation_id": row.get("actual_observation_id") or
                        f"logical:{chain_id}:{product_id}:{row['target_period']}:{objective}"}
                       for row in rows], key=lambda row: (row["origin"], row["target"], row["horizon"]))
    payload = {"chain_id": chain_id, "category_id": category_id, "product_id": product_id,
               "objective": objective, "issue_period": issue_period,
               "evaluation_mode": evaluation_mode,
               "evaluation_targets": [row["target"] for row in evidence],
               "evaluation_origins": [row["origin"] for row in evidence],
               "actual_observation_ids": [row["actual_observation_id"] for row in evidence],
               "horizons": [row["horizon"] for row in evidence]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _finite_nonnegative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _eligible(candidate: dict[str, Any], policy: SelectionPolicy) -> tuple[bool, str | None]:
    if candidate.get("observations", 0) < policy.product_observations:
        return False, "INSUFFICIENT_PRODUCT_OBSERVATIONS"
    if not all(_finite_nonnegative(candidate.get(key)) for key in ("wape", "mae", "rmse")) or not isinstance(candidate.get("bias"), (int, float)) or not math.isfinite(candidate["bias"]):
        return False, "INVALID_METRICS"
    horizons = candidate.get("horizons") or []
    if len(horizons) != 12 or {row.get("horizon") for row in horizons} != set(range(1, 13)) or any(
            not _finite_nonnegative(row.get("value")) for row in horizons):
        return False, "INCOMPLETE_H1_H12"
    if candidate.get("family") == "ensemble" and (candidate["observations"] < policy.ensemble_observations or
            candidate.get("windows", 0) < policy.ensemble_windows):
        return False, "ENSEMBLE_NOT_COMPARABLE"
    if not candidate.get("evaluation_signature"):
        return False, "NOT_COMPARABLE"
    return True, None


def suggest_reference(candidates: list[dict[str, Any]], *,
                      policy: SelectionPolicy = SELECTION_POLICY) -> dict[str, Any]:
    """Rank only candidates with one identical product-level evidence signature."""
    validated = [(candidate, *_eligible(candidate, policy)) for candidate in candidates]
    eligible = [candidate for candidate, ok, _ in validated if ok]
    if not eligible:
        return {"status": "INSUFFICIENT_EVIDENCE", "official": False,
                "comparison_status": "NOT_COMPARABLE", "comparison_reason": "INSUFFICIENT_PRODUCT_OBSERVATIONS",
                "reason_codes": sorted({reason for _, _, reason in validated if reason})}
    baseline = next((candidate for candidate in eligible if candidate["family"] == "statistical"), None)
    reference_signature = baseline["evaluation_signature"] if baseline else eligible[0]["evaluation_signature"]
    comparable = [candidate for candidate in eligible if candidate["evaluation_signature"] == reference_signature]
    reasons: list[str] = [reason for _, ok, reason in validated if not ok and reason]
    if len(comparable) < len(eligible):
        reasons.append("ENSEMBLE_NOT_COMPARABLE" if any(candidate["family"] == "ensemble" and candidate not in comparable for candidate in eligible) else "SCOPE_ONLY_EVIDENCE")
    baseline = baseline if baseline in comparable else next((candidate for candidate in comparable if candidate["family"] == "statistical"), None)

    def safe(candidate: dict[str, Any]) -> bool:
        if baseline is None or candidate is baseline:
            return True
        if abs(candidate["bias"]) > abs(baseline["bias"]) + policy.maximum_bias_deterioration:
            reasons.append("BIAS_CONTROL_FAIL")
            return False
        for horizon in policy.critical_horizons:
            current = next((row.get("wape") for row in candidate.get("by_horizon", []) if row.get("horizon") == horizon), None)
            prior = next((row.get("wape") for row in baseline.get("by_horizon", []) if row.get("horizon") == horizon), None)
            if _finite_nonnegative(current) and _finite_nonnegative(prior) and current > prior + policy.maximum_critical_deterioration:
                reasons.append("CRITICAL_HORIZON_FAIL")
                return False
        if candidate.get("stability") is not None and baseline.get("stability") is not None and candidate["stability"] > baseline["stability"] + policy.maximum_stability_deterioration:
            reasons.append("STABILITY_CONTROL_FAIL")
            return False
        if candidate.get("recent_deterioration") is not None and baseline.get("recent_deterioration") is not None and candidate["recent_deterioration"] > baseline["recent_deterioration"] + policy.maximum_recent_deterioration:
            reasons.append("RECENT_CONTROL_FAIL")
            return False
        return True

    safe_candidates = [candidate for candidate in comparable if safe(candidate)]
    if not safe_candidates:
        return {"status": "NOT_COMPARABLE", "official": False,
                "comparison_status": "NOT_COMPARABLE", "comparison_reason": "BIAS_CONTROL_FAIL",
                "reason_codes": sorted(set(reasons))}
    order = {"statistical": 0, "ensemble": 1, "ml": 2}
    ranked = sorted(safe_candidates, key=lambda c: (c["wape"], abs(c["bias"]), c.get("stability", math.inf), c.get("recent_deterioration", math.inf), order.get(c["family"], 9)))
    winner = ranked[0]
    statistical = next((candidate for candidate in safe_candidates if candidate["family"] == "statistical"), None)
    if statistical and winner is not statistical and statistical["wape"] - winner["wape"] < policy.minimum_improvement_points:
        winner = statistical
        reasons.append("PRACTICAL_TIE_BASELINE_PREFERRED")
    observations = winner["observations"]
    windows = winner.get("windows", 0)
    confidence = "Alta" if observations >= policy.ensemble_observations and windows >= policy.ensemble_windows and len(comparable) > 1 else "Media" if observations >= 12 and len(comparable) > 1 else "Baja"
    status = "SUGGESTED_ONLY_ELIGIBLE" if len(safe_candidates) == 1 else "SUGGESTED_RETROSPECTIVE" if winner.get("evidence_mode") == "RETROSPECTIVE_TRAINING" else "SUGGESTED_PROVISIONAL"
    if not any(candidate.get("family") == "ml" for candidate in eligible):
        reasons.append("ML_NOT_ELIGIBLE")
    if winner.get("evidence_mode") != "POINT_IN_TIME":
        reasons.append("TEMPORAL_CERTIFICATION_MISSING")
    return {key: winner.get(key) for key in ("family", "model", "strategy", "product_id", "wape", "bias", "mae", "rmse", "observations", "windows", "evidence_mode", "evaluation_signature", "horizons")} | {
        "status": status, "confidence": confidence, "official": False,
        "comparison_status": "COMPARABLE" if len(comparable) > 1 else "ONLY_ELIGIBLE_CANDIDATE",
        "comparison_reason": None if len(comparable) > 1 else "ONLY_ELIGIBLE_CANDIDATE",
        "reason_codes": sorted(set(reasons + (["ONLY_ELIGIBLE_CANDIDATE"] if len(safe_candidates) == 1 else ["LOWEST_COMPARABLE_WAPE", "BIAS_CONTROL_PASS"]))),
    }
