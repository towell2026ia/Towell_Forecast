"""Evidence-bounded launch forecast; never an official forecast or Champion."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any

import numpy as np

from services.forecast_engine.engine import add_month, normalize_retrospective_dataset
from services.ml_engine.engine import RandomForestGlobal

POLICY_VERSION = "COLD-START-1.0.0"
_GENERIC_WORDS = {"toalla", "towel", "mb", "de", "del", "para", "color", "pza", "pieza"}


@dataclass(frozen=True)
class ColdStartPolicy:
    minimum_comparables: int = 3
    maximum_comparables: int = 12
    minimum_comparable_months: int = 18
    maximum_target_months: int = 5
    maximum_bias_deterioration_pp: float = 5.0


def _terms(value: str) -> set[str]:
    return {part for part in re.findall(r"[a-z0-9]+", value.casefold()) if len(part) > 2 and part not in _GENERIC_WORDS}


def _level(values: list[float]) -> float:
    return max(1.0, statistics.mean(values[-min(3, len(values)):]))


def _features(prefix: list[float], horizon: int, age: int) -> list[float]:
    level = _level(prefix) if prefix else 1.0
    return [horizon / 12, age / 5, (prefix[-1] / level) if prefix else 0,
            (prefix[-1] - prefix[0]) / level if len(prefix) > 1 else 0]


def _predict(peers: list[dict[str, Any]], age: int, prefix: list[float], method: str) -> list[float]:
    if age:
        target_level = _level(prefix)
        ratios = [[peer["history"][age + horizon - 1] / _level(peer["history"][:age])
                   for horizon in range(1, 13)] for peer in peers]
        analog = [target_level * statistics.mean(row[h] for row in ratios) for h in range(12)]
        baseline = [target_level] * 12
    else:
        analog = [statistics.median(peer["history"][h] for peer in peers) for h in range(12)]
        baseline = [statistics.median(peer["history"][0] for peer in peers)] * 12
    if method == "baseline":
        return baseline
    if method == "analog":
        return analog
    x, y = [], []
    for peer in peers:
        values = peer["history"]
        scale = _level(values[:age]) if age else _level(values[:3])
        for horizon in range(1, 13):
            x.append(_features(values[:age], horizon, age))
            y.append(values[age + horizon - 1] / scale)
    model = RandomForestGlobal(trees=12)
    model.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    estimated = model.predict(np.asarray([_features(prefix, h, age) for h in range(1, 13)], dtype=float))
    target_scale = _level(prefix) if age else statistics.median(_level(peer["history"][:3]) for peer in peers)
    return [float(max(0, value * target_scale)) for value in estimated]


def _backtest(peers: list[dict[str, Any]], age: int, method: str) -> dict[str, Any]:
    actuals, estimates = [], []
    for held_out in peers:
        training = [peer for peer in peers if peer["product_id"] != held_out["product_id"]]
        if len(training) < 2:
            continue
        forecast = _predict(training, age, held_out["history"][:age], method)
        actuals.extend(held_out["history"][age:age + 12])
        estimates.extend(forecast)
    denominator = sum(actuals)
    if not denominator or len(actuals) != 12 * len(peers):
        raise ValueError("cold_start_backtest_incomplete")
    by_horizon = []
    for horizon in (1, 3, 6, 12):
        observed = actuals[horizon - 1::12]
        predicted = estimates[horizon - 1::12]
        by_horizon.append({"horizon": horizon, "wape": round(100 * sum(abs(a - p) for a, p in zip(observed, predicted)) / sum(observed), 2),
                           "bias": round(100 * sum(p - a for a, p in zip(observed, predicted)) / sum(observed), 2),
                           "observations": len(observed)})
    return {"wape": round(100 * sum(abs(a - p) for a, p in zip(actuals, estimates)) / denominator, 2),
            "bias": round(100 * sum(p - a for a, p in zip(actuals, estimates)) / denominator, 2),
            "observations": len(actuals), "products": len(peers), "by_horizon": by_horizon}


def launch_forecast(rows: list[dict[str, Any]], issue: str, target: dict[str, Any],
                    policy: ColdStartPolicy | None = None, *, objective: str | None = None,
                    observed_cutoff: str | None = None,
                    comparable_product_ids: set[str] | None = None) -> dict[str, Any] | None:
    """Return a distinct provisional result or None when comparable evidence is absent.

    Rows must already be limited to one authorized chain and to periods <= issue.
    Original availability remains unknown; no point-in-time claim is made.
    """
    objectives = {row.get("objective") for row in rows}
    objective = objective or target.get("objective") or (
        next(iter(objectives)) if len(objectives) == 1 else "Venta")
    if objective == "Pedido":
        return _launch_order_forecast(rows, issue, target, policy or ColdStartPolicy(),
            observed_cutoff=observed_cutoff or issue,
            comparable_product_ids=comparable_product_ids)
    if objective != "Venta":
        raise ValueError("cold_start_unsupported_objective")
    policy = policy or ColdStartPolicy()
    product_id, chain_id = target["id"], target["chain_id"]
    category = target.get("category_id")
    if not category or any(row["chain_id"] != chain_id or row["period"] > issue for row in rows):
        return None
    series = normalize_retrospective_dataset(rows, issue, chain_id=chain_id, objective=objective)
    own = next((item for item in series if item.product_id == product_id), None)
    observed = own.history(issue) if own else []
    age = len(observed)
    if age > policy.maximum_target_months:
        return None
    target_terms = _terms(target.get("description") or "")
    candidates = []
    for item in series:
        if item.product_id == product_id or item.chain_id != chain_id or item.category != category:
            continue
        history = item.history(issue)
        if len(history) < max(policy.minimum_comparable_months, age + 12) or any(
                not math.isfinite(value) or value < 0 for value in history):
            continue
        overlap = len(target_terms & _terms(item.description))
        candidates.append({"product_id": item.product_id, "description": item.description,
                           "history": history, "similarity": overlap})
    # A product description selects comparable peers; it never defines identity.
    related = [peer for peer in candidates if peer["similarity"] >= 1]
    peers = related if len(related) >= policy.minimum_comparables else candidates
    peers = sorted(peers, key=lambda peer: (-peer["similarity"], peer["product_id"]))[:policy.maximum_comparables]
    if len(peers) < policy.minimum_comparables:
        return None
    metrics = {}
    for method in ("baseline", "analog", "ml_random_forest"):
        metrics[method] = _backtest(peers, age, method)
    baseline = metrics["baseline"]
    allowed = [method for method in ("analog", "ml_random_forest") if
               abs(metrics[method]["bias"]) <= abs(baseline["bias"]) + policy.maximum_bias_deterioration_pp]
    winner = min(allowed, key=lambda method: metrics[method]["wape"]) if allowed else "baseline"
    if metrics[winner]["wape"] >= baseline["wape"]:
        winner = "baseline"
    values = _predict(peers, age, observed, winner)
    if len(values) != 12 or any(not math.isfinite(value) or value < 0 for value in values):
        return None
    return {"status": "PROVISIONAL_COLD_START", "policy_version": POLICY_VERSION,
            "product_id": product_id, "chain_id": chain_id, "category_id": category,
            "issue_period": issue, "observed_months": age,
            "model": winner, "confidence": "LOW" if age < 3 or metrics[winner]["wape"] > 40 else "LIMITED",
            "comparables": [{"product_id": peer["product_id"], "description": peer["description"]} for peer in peers],
            "retrospective_peer_metrics": metrics, "target_wape": None, "point_in_time_certified": False,
            "official_publication": False, "champion_eligible": False,
            "horizons": [{"horizon": h, "target_period": add_month(issue, h), "value": round(value, 2)}
                         for h, value in enumerate(values, 1)]}


def _numeric_tail(item, issue):
    """Preserve explicit zeroes. A gap ends the tail; it is never filled."""
    values = []
    period = issue
    while (point := item.observations.get(period)) is not None and point.value is not None:
        values.append(point.value)
        period = add_month(period, -1)
    return list(reversed(values))


def _calendar_analog(peers, issue, method):
    """Same-calendar seasonal analogue, not the first twelve launch months."""
    curves = []
    for peer in peers:
        history = _numeric_tail(peer, issue)
        if len(history) < 12:
            raise ValueError("cold_start_peer_tail_short")
        level = statistics.fmean(history[-3:])
        annual_level = statistics.fmean(history[-12:])
        curve = []
        for horizon in range(1, 13):
            if method == "peer_recent_level" or annual_level == 0:
                value = level
            else:
                target_month = add_month(issue, horizon)[-2:]
                seasonal = [point.value for period, point in peer.observations.items()
                    if period <= issue and period[-2:] == target_month and point.value is not None]
                value = level * statistics.fmean(seasonal) / annual_level
            curve.append(max(0.0, value))
        curves.append(curve)
    return [statistics.median(values) for values in zip(*curves)]


def _launch_order_forecast(rows, issue, target, policy, *, observed_cutoff,
                           comparable_product_ids):
    """Peer-only ORDER estimate; approval/effect remain explicit and separate.

    The fit can use an accredited commercial scenario. Held-out validation uses
    OBSERVED rows only, with peers frozen before each evaluation target. No
    target-product WAPE is claimed when that product has no observations.
    """
    from services.forecast_engine.engine import _metrics

    chain_id, product_id = target["chain_id"], target["id"]
    category = target.get("category_id")
    if (not category or observed_cutoff > issue or any(
        row.get("chain_id") != chain_id or row["period"] > issue
        or row.get("objective") != "Pedido" or row.get("metric_code", "ORDER") != "ORDER"
        for row in rows)):
        return None
    series = normalize_retrospective_dataset(rows, issue, chain_id=chain_id, objective="Pedido")
    own = next((item for item in series if item.product_id == product_id), None)
    own_history = _numeric_tail(own, issue) if own else []
    if len(own_history) > policy.maximum_target_months:
        return None
    allowed = comparable_product_ids
    candidates = [item for item in series if item.product_id != product_id
        and str(item.category).casefold() == str(category).casefold()
        and (allowed is None or item.product_id in allowed)
        and len(_numeric_tail(item, issue)) >= policy.minimum_comparable_months]
    terms = _terms(target.get("description") or "")
    candidates.sort(key=lambda item: (-len(terms & _terms(item.description)), item.product_id))
    peers = candidates[:policy.maximum_comparables]
    if len(peers) < policy.minimum_comparables:
        return None
    real_rows = [row for row in rows if row.get("basis", "OBSERVED") == "OBSERVED"
                 and row["period"] <= observed_cutoff]
    real = {item.product_id: item for item in normalize_retrospective_dataset(
        real_rows, observed_cutoff, chain_id=chain_id, objective="Pedido")}
    methods = ("peer_recent_level", "peer_calendar_seasonality")
    validation = {method: [] for method in methods}
    for origin in (add_month(observed_cutoff, -13), add_month(observed_cutoff, -12)):
        for held_out in peers:
            actual_peer = real.get(held_out.product_id)
            training = [real[p.product_id] for p in peers if p.product_id != held_out.product_id
                and p.product_id in real and len(_numeric_tail(real[p.product_id], origin)) >= 12]
            actual = [actual_peer.observations.get(add_month(origin, h)) if actual_peer else None
                      for h in range(1, 13)]
            if len(training) < max(2, policy.minimum_comparables - 1) or any(
                point is None or point.value is None for point in actual):
                continue
            for method in methods:
                predicted = _calendar_analog(training, origin, method)
                validation[method].extend({"product_id": held_out.product_id,
                    "origin": origin, "target_period": add_month(origin, h), "horizon": h,
                    "actual": point.value, "prediction": value, "basis": "OBSERVED"}
                    for h, (point, value) in enumerate(zip(actual, predicted), 1))
    metrics = {method: {**_metrics([(r["actual"], r["prediction"]) for r in pairs]),
        "observations": len(pairs), "products": len({r["product_id"] for r in pairs}),
        "windows": len({r["origin"] for r in pairs})} for method, pairs in validation.items()}
    baseline = metrics[methods[0]]
    valid = [name for name in methods if metrics[name]["wape"] is not None
        and (baseline["bias"] is None or abs(metrics[name]["bias"]) <=
             abs(baseline["bias"]) + policy.maximum_bias_deterioration_pp)]
    winner = min(valid, key=lambda name: (metrics[name]["wape"], methods.index(name))) if valid else methods[0]
    values = _calendar_analog(peers, issue, winner)
    return {"status": "PROVISIONAL_COLD_START", "policy_version": "ORDER-COLD-START-2.0.0",
        "objective": "Pedido", "metric_code": "ORDER", "product_id": product_id,
        "chain_id": chain_id, "category_id": category, "issue_period": issue,
        "observed_cutoff": observed_cutoff, "observed_months": len(own_history),
        "model": winner, "confidence": "LOW", "target_wape": None,
        "target_bias": None, "retrospective_peer_metrics": metrics,
        "peer_validation_pairs": validation[winner], "evaluation_target_basis": "OBSERVED_ONLY",
        "comparables": [{"product_id": peer.product_id, "chain_id": peer.chain_id,
            "description": peer.description, "first_period": min(peer.observations),
            "last_period": max(peer.observations), "current_tail_months": len(_numeric_tail(peer, issue)),
            "recent_level": statistics.fmean(_numeric_tail(peer, issue)[-3:]),
            "scale_basis": "MEDIAN_PEER_ABSOLUTE_PIECES"} for peer in peers],
        "incremental_effect": {"status": "UNDETERMINED", "incremental_pieces": None,
            "reason": "NO_ACCREDITED_LAUNCH_OR_SUBSTITUTION_EVIDENCE"},
        "admission": {"status": "REQUIRES_EXPLICIT_ACCEPTANCE", "accepted": False},
        "point_in_time_certified": False, "official_publication": False,
        "champion_eligible": False, "research": {"status": "DISABLED"},
        "horizons": [{"horizon": h, "target_period": add_month(issue, h), "value": round(value, 2)}
            for h, value in enumerate(values, 1)]}
