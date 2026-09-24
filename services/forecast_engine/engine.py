"""Generic product-level, point-in-time forecast and independent certification.

This engine has no dependency on a customer, a spreadsheet or a storage backend.
It refuses unproven temporal availability rather than backfilling timestamps.
"""

from __future__ import annotations

import calendar
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable

import numpy as np

from services.ml_engine.engine import MODEL_FACTORIES, Preprocessor
from services.statistical_engine import engine as stat
from .identifiers import model_version

ENGINE_VERSION = "prd09.1b-1.0.0"
HORIZONS = tuple(range(1, 13))


def add_month(period: str, offset: int) -> str:
    return stat.add_month(period, offset)


def month_end(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def month_distance(start: str, end: str) -> int:
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    return (ey - sy) * 12 + em - sm


@dataclass(frozen=True)
class ForecastPolicy:
    min_train_observations: int = 18
    min_selection_origins: int = 6
    min_certification_origins: int = 3
    min_product_observations: int = 6
    minimum_improvement: float = 0.25
    max_bias_deterioration: float = 5.0
    max_stability_deterioration: float = 10.0
    max_recent_deterioration: float = 10.0

    def __post_init__(self) -> None:
        if min(self.min_train_observations, self.min_selection_origins,
               self.min_certification_origins, self.min_product_observations) < 1:
            raise ValueError("invalid_certification_policy")


@dataclass(frozen=True)
class Observation:
    period: str
    value: float | None
    available_at: str


@dataclass
class ProductSeries:
    chain_id: str
    product_id: str
    product_code: str
    description: str
    category: str
    variant: str
    objective: str
    observations: dict[str, Observation] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.chain_id}|{self.product_id}|{self.objective}"

    def history(self, issue_period: str) -> list[float]:
        eligible = [observation for observation in self.observations.values()
                    if observation.period <= issue_period
                    and observation.available_at <= month_end(issue_period)]
        eligible.sort(key=lambda item: item.period)
        first = next((index for index, item in enumerate(eligible)
                      if item.value is not None and item.value > 0), None)
        if first is None:
            return []
        # A missing month is not a zero. Forecast only from a contiguous observed tail.
        tail = eligible[first:]
        result: list[float] = []
        for item in tail:
            if result and item.period != add_month(tail[0].period, len(result)):
                break
            if item.value is None:
                break
            result.append(item.value)
        return result

    def lifecycle(self, cutoff: str, policy: ForecastPolicy) -> str:
        history = self.history(cutoff)
        if not history:
            return "PRE-LAUNCH"
        if len(history) < policy.min_train_observations:
            return "COLD_START"
        if all(value == 0 for value in history[-6:]):
            return "INACTIVE"
        return "ACTIVE"


def normalize_dataset(rows: Iterable[dict[str, Any]], cutoff: str,
                      *, chain_id: str | None = None, product_id: str | None = None,
                      objective: str = "Venta") -> list[ProductSeries]:
    """Validate identity and availability; never infer product IDs from descriptions."""
    if objective not in {"Venta", "Pedido", "Entrega"}:
        raise ValueError("unsupported_forecast_objective")
    date.fromisoformat(month_end(cutoff))
    grouped: dict[str, ProductSeries] = {}
    for row in rows:
        if str(row.get("objective", "")).casefold() != objective.casefold():
            continue  # Fcst Cliente remains an external benchmark, not a target.
        chain = str(row.get("chain_id") or row.get("chain_code") or row.get("chain") or "").strip()
        product = str(row.get("product_id") or row.get("canonical_product_id") or "").strip()
        if not chain or not product:
            raise ValueError("identity_missing")
        if chain_id is not None and chain != chain_id:
            continue
        if product_id is not None and product != product_id:
            continue
        period = str(row.get("period", ""))
        date.fromisoformat(month_end(period))
        if period > cutoff:
            raise ValueError("data_leakage_detected")
        availability = str(row.get("available_at") or "")[:10]
        if not availability:
            raise ValueError("availability_metadata_missing")
        date.fromisoformat(availability)
        if availability > month_end(cutoff):
            raise ValueError("data_leakage_detected")
        missing = str(row.get("is_missing", "false")).casefold() == "true"
        raw = row.get("value")
        value = None if missing or raw is None or str(raw).strip() == "" else float(raw)
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("invalid_observation")
        key = f"{chain}|{product}|{objective}"
        if key not in grouped:
            grouped[key] = ProductSeries(chain, product, str(row.get("product_code") or row.get("item") or product),
                                         str(row.get("description") or product),
                                         str(row.get("category") or "UNCLASSIFIED"),
                                         str(row.get("variant") or ""), objective)
        previous = grouped[key].observations.get(period)
        current = Observation(period, value, availability)
        if previous is not None and previous != current:
            raise ValueError("duplicate_observation_conflict")
        grouped[key].observations[period] = current
    return [grouped[key] for key in sorted(grouped)]


def _stat_predict(name: str, values: list[float], steps: int, parameters: dict[str, float]) -> float:
    if not values:
        raise ValueError("no_history")
    if name == "SES":
        output = stat.ses(values, steps, alpha=parameters["alpha"])
    elif name == "Holt":
        output = stat.holt(values, steps, alpha=parameters["alpha"], beta=parameters["beta"])
    elif name == "Holt-Winters":
        output = stat.holt_winters(values, steps, alpha=parameters["alpha"],
                                   beta=parameters["beta"], gamma=parameters["gamma"])
    elif name in {"Croston", "SBA", "TSB"}:
        output = stat.intermittent(values, steps, name.lower(),
                                   alpha=parameters["alpha"], beta=parameters["beta"])
    else:
        output = stat.MODELS[name](values, steps)
    return max(0.0, float(output[-1]))


def _stat_options(values: list[float]) -> dict[str, dict[str, float]]:
    """Choose smoothing parameters using only an inner slice of TRAIN."""
    options: dict[str, dict[str, float]] = {}
    for name in stat.MODELS:
        grid = ([{"alpha": alpha} for alpha in (0.15, 0.35, 0.65, 0.85)] if name == "SES"
                else [{"alpha": alpha, "beta": beta} for alpha in (0.2, 0.5, 0.8)
                      for beta in (0.1, 0.3)] if name in {"Holt", "Croston", "SBA", "TSB"}
                else [{"alpha": alpha, "beta": beta, "gamma": gamma}
                      for alpha in (0.2, 0.5) for beta in (0.1, 0.3) for gamma in (0.1, 0.3)]
                if name == "Holt-Winters" else [{}])
        scored = []
        for parameters in grid:
            errors = []
            for origin in range(max(6, len(values) - 5), len(values)):
                try:
                    errors.append(abs(values[origin] - _stat_predict(name, values[:origin], 1, parameters)))
                except (ValueError, IndexError, ZeroDivisionError):
                    continue
            if errors:
                scored.append((statistics.fmean(errors), parameters))
        if scored:
            options[name] = min(scored, key=lambda item: item[0])[1]
    return options


def _feature(series: ProductSeries, issue: str, target: str, identities: list[str]) -> list[float | None]:
    history = series.history(issue)
    month = int(target[-2:])
    result: list[float | None] = [month, (month - 1) // 3 + 1,
                                  math.sin(2 * math.pi * month / 12), math.cos(2 * math.pi * month / 12),
                                  month_distance(issue, target), len(history)]
    result.extend(history[-lag] if len(history) >= lag else None for lag in (1, 2, 3, 6, 12))
    for width in (3, 6, 12):
        recent = history[-width:]
        result.extend((statistics.fmean(recent) if recent else None,
                       statistics.pstdev(recent) if len(recent) > 1 else 0.0 if recent else None,
                       min(recent) if recent else None, max(recent) if recent else None))
    recent = [index for index, value in enumerate(history) if value > 0]
    intervals = [b - a for a, b in zip(recent, recent[1:])]
    result.extend((len(history) - 1 - recent[-1] if recent else len(history),
                   sum(value == 0 for value in history),
                   len(recent) / len(history) if history else 0.0,
                   statistics.fmean(intervals) if intervals else None))
    result.extend(1.0 if value == identity else 0.0 for identity in identities
                  for value in (series.chain_id, series.category, series.key))
    return result


def _training_samples(series: list[ProductSeries], train_end: str,
                      identities: list[str]) -> tuple[list[list[float | None]], np.ndarray]:
    features, targets = [], []
    first = min(min(item.observations) for item in series)
    for item in series:
        for target, observation in sorted(item.observations.items()):
            if target > train_end or observation.value is None or observation.available_at > month_end(train_end):
                continue
            for horizon in HORIZONS:
                issue = add_month(target, -horizon)
                if issue < first or issue >= target or not item.history(issue):
                    continue
                features.append(_feature(item, issue, target, identities))
                targets.append(observation.value)
    return features, np.asarray(targets, dtype=float)


def _metrics(pairs: list[tuple[float, float]]) -> dict[str, float | None]:
    if not pairs:
        return {key: None for key in ("wape", "bias", "mae", "rmse", "stability")}
    actual = [pair[0] for pair in pairs]
    errors = [a - p for a, p in pairs]
    absolute = [abs(error) for error in errors]
    denominator = sum(actual)
    return {"wape": round(100 * sum(absolute) / denominator, 4) if denominator > 0 else None,
            "bias": round(100 * sum(errors) / denominator, 4) if denominator > 0 else None,
            "mae": round(statistics.fmean(absolute), 4),
            "rmse": round(math.sqrt(statistics.fmean(error * error for error in errors)), 4),
            "stability": round(statistics.pstdev(absolute), 4) if len(absolute) > 1 else 0.0}


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bands(value: float, residuals: list[float]) -> dict[str, float] | None:
    if not residuals:
        return None
    p10 = min(value, max(0.0, value + _quantile(residuals, 0.1)))
    p90 = max(value, value + _quantile(residuals, 0.9))
    p95 = max(p90, value + _quantile(residuals, 0.95))
    return {"p10": round(p10, 2), "p50": round(value, 2),
            "p90": round(p90, 2), "p95": round(p95, 2)}


def forecast_dataset(rows: Iterable[dict[str, Any]], issue_period: str,
                     *, chain_id: str | None = None, product_id: str | None = None,
                     objective: str = "Venta", policy: ForecastPolicy | None = None,
                     incumbent: dict[str, Any] | None = None,
                     research: dict[str, Any] | None = None,
                     version_sequence: int = 1) -> dict[str, Any]:
    """Produce product H1-H12 and reconciled aggregates; never publish a Champion."""
    policy = policy or ForecastPolicy()
    products = normalize_dataset(rows, issue_period, chain_id=chain_id, product_id=product_id,
                                 objective=objective)
    cutoff_date = month_end(issue_period)
    if research:
        if research.get("cutoff_date", "") > cutoff_date or any(
            not source.get("published_at") or str(source["published_at"])[:10] > cutoff_date
            for source in research.get("sources", []) + research.get("signals", [])
        ):
            raise ValueError("research_leakage_detected")
    if not products:
        raise ValueError("no_eligible_products")
    return {"engine_version": ENGINE_VERSION, "issue_period": issue_period,
            "objective": objective, "policy": asdict(policy),
            "chains": [_forecast_chain(group, issue_period, policy, incumbent, version_sequence)
                       for chain in sorted({item.chain_id for item in products})
                       if (group := [item for item in products if item.chain_id == chain])]}


def _forecast_chain(products: list[ProductSeries], issue: str, policy: ForecastPolicy,
                    incumbent: dict[str, Any] | None, version_sequence: int) -> dict[str, Any]:
    chain = products[0].chain_id
    start = min(min(item.observations) for item in products)
    months = month_distance(start, issue) + 1
    train_length = min(months, max(policy.min_train_observations,
                       min(30, months - 12 - policy.min_selection_origins - policy.min_certification_origins)))
    train_end = add_month(start, train_length - 1)
    # Split by target period globally. Splitting by issue would let one actual
    # month enter validation at H12 and certification at H1.
    target_periods = []
    current = add_month(train_end, 13)
    while current <= issue:
        target_periods.append(current)
        current = add_month(current, 1)
    cert_targets = target_periods[-policy.min_certification_origins:]
    selection_targets = target_periods[:-policy.min_certification_origins]
    certified_range = set(cert_targets)
    identities = sorted({value for item in products if item.history(train_end)
                         for value in (item.chain_id, item.category, item.key)})
    features, targets = _training_samples(products, train_end, identities)
    ml_models: dict[str, tuple[Preprocessor, Any]] = {}
    ml_failures: list[dict[str, str]] = []
    if len(features) >= policy.min_train_observations:
        for name, factory in MODEL_FACTORIES.items():
            try:
                prep = Preprocessor().fit(features)
                ml_models[name] = (prep, factory().fit(prep.transform(features), targets))
            except Exception as exc:
                ml_failures.append({"model": name, "error": type(exc).__name__})
                continue
    options = {item.key: _stat_options(item.history(train_end)) for item in products}
    rows: list[dict[str, Any]] = []
    for target in target_periods:
        for item in products:
            for horizon in HORIZONS:
                origin = add_month(target, -horizon)
                observed = item.observations.get(target)
                if observed is None or observed.value is None or observed.available_at > month_end(issue):
                    continue
                # Certification is a sealed holdout: all predictions use the
                # last pre-certification target as their feature/history cutoff.
                feature_issue = min(origin, add_month(cert_targets[0], -1)) if target in certified_range else origin
                history = item.history(feature_issue)
                if not history:
                    continue
                steps = month_distance(feature_issue, target)
                statistical = {}
                for name, parameters in options[item.key].items():
                    try:
                        statistical[name] = _stat_predict(name, history, steps, parameters)
                    except (ValueError, IndexError, ZeroDivisionError):
                        continue
                vector = _feature(item, feature_issue, target, identities)
                ml = {}
                for name, (prep, model) in ml_models.items():
                    try:
                        ml[name] = float(max(0.0, model.predict(prep.transform([vector]))[0]))
                    except Exception as exc:
                        ml_failures.append({"model": name, "error": type(exc).__name__})
                rows.append({"product_id": item.product_id, "category": item.category,
                             "origin": origin, "target_period": target, "horizon": horizon,
                             "actual": observed.value, "statistical": statistical, "ml": ml,
                             "split": "certification" if target in certified_range else "validation"})
    selection = [row for row in rows if row["split"] == "validation"]
    certification = [row for row in rows if row["split"] == "certification"]
    # Each product selects its statistical family; ML is a global model. Within
    # each comparison, candidates must cover the identical origin/target/horizon set.
    evaluations = []
    statistical_choices = {}
    for item in products:
        baseline = [row for row in selection if row["product_id"] == item.product_id
                    and "Naive" in row["statistical"] and (not ml_models or row["ml"])]
        local = []
        for name in stat.MODELS:
            if not baseline or any(name not in row["statistical"] for row in baseline):
                continue
            score = _metrics([(row["actual"], row["statistical"][name]) for row in baseline])
            if score["wape"] is not None:
                by_horizon = [{"horizon": horizon,
                               "wape": _metrics([(row["actual"], row["statistical"][name])
                                                 for row in baseline if row["horizon"] == horizon])["wape"]}
                              for horizon in HORIZONS]
                local.append({"family": "statistical", "product_id": item.product_id,
                              "model": name, "validation_wape": score["wape"],
                              "validation_bias": score["bias"], "observations": len(baseline),
                              "validation_by_horizon": by_horizon})
        local.sort(key=lambda row: (row["validation_wape"], abs(row["validation_bias"]), row["model"]))
        statistical_choices[item.product_id] = local[0]["model"] if local else "Naive"
        evaluations.extend(local)
    ml_baseline = [row for row in selection if statistical_choices[row["product_id"]] in row["statistical"]
                   and row["ml"]]
    for name in ml_models:
        if not ml_baseline or any(name not in row["ml"] for row in ml_baseline):
            continue
        score = _metrics([(row["actual"], row["ml"][name]) for row in ml_baseline])
        if score["wape"] is not None:
            by_horizon = [{"horizon": horizon,
                           "wape": _metrics([(row["actual"], row["ml"][name])
                                             for row in ml_baseline if row["horizon"] == horizon])["wape"]}
                          for horizon in HORIZONS]
            evaluations.append({"family": "ml", "model": name, "validation_wape": score["wape"],
                                "validation_bias": score["bias"], "observations": len(ml_baseline),
                                "validation_by_horizon": by_horizon})
    ml_choices = sorted((row for row in evaluations if row["family"] == "ml"),
                        key=lambda row: (row["validation_wape"], abs(row["validation_bias"]), row["model"]))
    ml_choice = ml_choices[0]["model"] if ml_choices else None
    common = [row for row in selection if statistical_choices[row["product_id"]] in row["statistical"]
              and ml_choice is not None and ml_choice in row["ml"]]
    weights = [round(1 - index / 10, 2) for index in range(11)] if ml_choice else [1.0]
    if ml_choice and incumbent and incumbent.get("statistical_weight") is not None:
        weights.append(float(incumbent["statistical_weight"]))
    if common:
        coarse = sorted(((_metrics([(row["actual"], row["statistical"][statistical_choices[row["product_id"]]] * weight
                                     + row["ml"][ml_choice] * (1 - weight)) for row in common])["wape"], weight)
                         for weight in weights))
        center = coarse[0][1]
        weights = sorted(set(weights + [round(max(0.0, min(1.0, center + offset * 0.025)), 3)
                                         for offset in range(-4, 5)]))
    def candidates_on(data: list[dict[str, Any]], weight: float) -> list[tuple[float, float]]:
        return [(row["actual"], row["statistical"][statistical_choices[row["product_id"]]] * weight
                 + (row["ml"][ml_choice] if ml_choice else 0.0) * (1 - weight))
                for row in data if statistical_choices[row["product_id"]] in row["statistical"]
                and (ml_choice is None or ml_choice in row["ml"])]
    candidates = []
    for weight in weights:
        pair = candidates_on(selection, weight)
        score = _metrics(pair)
        if score["wape"] is not None:
            candidates.append({"strategy": "statistical" if weight == 1 else "ml" if weight == 0 else "ensemble",
                               "statistical_weight": weight, "validation_wape": score["wape"],
                               "validation_bias": score["bias"], "validation_stability": score["stability"],
                               "observations": len(pair)})
    candidates.sort(key=lambda row: (row["validation_wape"], abs(row["validation_bias"]), -row["statistical_weight"]))
    best = candidates[0] if candidates else {"strategy": "statistical", "statistical_weight": 1.0,
                                              "validation_wape": None}
    incumbent_strategy = incumbent.get("strategy") if incumbent else None
    incumbent_weight = (float(incumbent["statistical_weight"])
                        if ml_choice and incumbent and incumbent.get("statistical_weight") is not None else
                        next((row["statistical_weight"] for row in candidates
                              if row["strategy"] == incumbent_strategy), None))
    official_weight = incumbent_weight if incumbent_weight is not None else 1.0
    official = next((row for row in candidates if row["statistical_weight"] == official_weight), best)
    def certified(weight: float) -> dict[str, Any]:
        pair = candidates_on(certification, weight)
        score = _metrics(pair)
        return {f"certified_{key}": value for key, value in score.items()}
    best_cert = certified(best["statistical_weight"])
    incumbent_cert = certified(official_weight)
    def deterioration(weight: float) -> float | None:
        by_origin = [_metrics(candidates_on([row for row in certification if row["target_period"] == target],
                                            weight))["wape"] for target in cert_targets]
        usable = [value for value in by_origin if value is not None]
        return round(max(0.0, usable[-1] - usable[0]), 4) if len(usable) >= 2 else None
    best_cert["certified_deterioration"] = deterioration(best["statistical_weight"])
    incumbent_cert["certified_deterioration"] = deterioration(official_weight)
    enough = (bool(selection_targets) and len(selection_targets) >= policy.min_selection_origins
              and len(cert_targets) >= policy.min_certification_origins
              and train_end < add_month(selection_targets[0], -12))
    status = "CERTIFIED" if enough and best_cert["certified_wape"] is not None else "PROVISIONAL"
    if not selection:
        status = "INSUFFICIENT"
    improvement = ((incumbent_cert["certified_wape"] - best_cert["certified_wape"])
                   if best_cert["certified_wape"] is not None and incumbent_cert["certified_wape"] is not None
                   else -math.inf)
    eligible = (status == "CERTIFIED" and best["strategy"] != official["strategy"]
                and improvement >= policy.minimum_improvement
                and abs(best_cert["certified_bias"] or 0) <= abs(incumbent_cert["certified_bias"] or 0) + policy.max_bias_deterioration
                and (best_cert["certified_stability"] or 0) <= (incumbent_cert["certified_stability"] or 0) + policy.max_stability_deterioration
                and (best_cert["certified_deterioration"] or 0) <=
                (incumbent_cert["certified_deterioration"] or 0) + policy.max_recent_deterioration)
    version = model_version("FT", chain, issue, version_sequence)
    candidate_payload = {**best, **best_cert, "version": version, "certification_status": status}
    operational_models: dict[str, tuple[Preprocessor, Any]] = {}
    current_features, current_targets = _training_samples(products, issue, identities)
    if ml_choice and current_features:
        try:
            current_prep = Preprocessor().fit(current_features)
            current_model = MODEL_FACTORIES[ml_choice]().fit(current_prep.transform(current_features), current_targets)
            operational_models[ml_choice] = current_prep, current_model
        except Exception as exc:
            ml_failures.append({"model": ml_choice, "error": type(exc).__name__})
    forecast = []
    for item in products:
        lifecycle = item.lifecycle(issue, policy)
        history = item.history(issue)
        selected_model = statistical_choices[item.product_id]
        for horizon in HORIZONS:
            target = add_month(issue, horizon)
            statistical_value = (_stat_predict(selected_model, history, horizon, options[item.key].get(selected_model, {}))
                                 if history else 0.0)
            ml_value = None
            if ml_choice and ml_choice in operational_models:
                prep, model = operational_models[ml_choice]
                # Fit a fresh operational model on all currently known history,
                # never on a future target; certification used a separate frozen fit.
                try:
                    ml_value = float(max(0.0, model.predict(prep.transform([_feature(item, issue, target, identities)]))[0]))
                except Exception as exc:
                    ml_failures.append({"model": ml_choice, "error": type(exc).__name__})
            value = statistical_value * official_weight + (ml_value or 0.0) * (1 - official_weight) if ml_value is not None else statistical_value
            applied_strategy = official["strategy"] if ml_value is not None or official_weight == 1 else "statistical_fallback"
            def comparable_rows(level: str) -> list[dict[str, Any]]:
                return [row for row in certification if row["horizon"] == horizon
                        and (level == "chain" or level == "category" and row["category"] == item.category
                             or level == "product" and row["product_id"] == item.product_id)
                        and statistical_choices[row["product_id"]] in row["statistical"]
                        and (official_weight == 1 or ml_choice in row["ml"])]
            band_basis = "product"
            comparable = comparable_rows(band_basis)
            if not comparable:
                band_basis = "category"
                comparable = comparable_rows(band_basis)
            if not comparable:
                band_basis = "chain"
                comparable = comparable_rows(band_basis)
            residuals = [row["actual"] -
                         (row["statistical"][statistical_choices[row["product_id"]]] * official_weight
                          + row["ml"].get(ml_choice, 0.0) * (1 - official_weight)) for row in comparable]
            forecast.append({"chain_id": chain, "product_id": item.product_id,
                             "product_code": item.product_code, "category": item.category,
                             "variant": item.variant, "objective": item.objective,
                             "issue_period": issue, "target_period": target, "horizon": horizon,
                             "statistical": round(statistical_value, 2),
                             "ml": round(ml_value, 2) if ml_value is not None else None,
                             "forecast_towell": round(value, 2), "model_strategy": applied_strategy,
                             "confidence": "Media" if lifecycle == "ACTIVE" and status == "CERTIFIED" else "Baja",
                             "forecast_status": lifecycle,
                             "classification": stat.classify(history)[0] if history else "Sin historial",
                             "certification_status": status if lifecycle == "ACTIVE" and
                             sum(row["product_id"] == item.product_id for row in certification) >=
                             policy.min_product_observations else "PROVISIONAL",
                             "probability": _bands(value, residuals),
                             "band_basis": band_basis if residuals else "insufficient",
                             "band_observations": len(residuals)})
    aggregates = []
    for level in ("category", "chain"):
        for horizon in HORIZONS:
            for key in sorted({row["category"] if level == "category" else chain
                               for row in forecast if row["horizon"] == horizon}):
                members = [row for row in forecast if row["horizon"] == horizon and
                           (row["category"] if level == "category" else chain) == key]
                aggregates.append({"level": level, "key": key, "horizon": horizon,
                                   "target_period": members[0]["target_period"],
                                   "forecast_towell": round(sum(row["forecast_towell"] for row in members), 2)})
    horizon_metrics = [{"horizon": horizon,
                        **{f"certified_{key}": value for key, value in _metrics(
                            candidates_on([row for row in certification if row["horizon"] == horizon],
                                          official_weight)).items()},
                        "observations": len(candidates_on([row for row in certification if row["horizon"] == horizon], official_weight))}
                       for horizon in HORIZONS]
    product_metrics = []
    for item in products:
        relevant = [row for row in certification if row["product_id"] == item.product_id]
        pairs = candidates_on(relevant, official_weight)
        if len(pairs) >= policy.min_product_observations and sum(actual for actual, _ in pairs) > 0:
            product_metrics.append({"product_id": item.product_id, **_metrics(pairs)})
    audit = {"engine_version": ENGINE_VERSION, "dataset_cutoff": month_end(issue),
             "model_versions": {"statistical": model_version("ST", chain, issue, version_sequence),
                                "ml": model_version("ML", chain, issue, version_sequence) if ml_choice else None,
                                "forecast_towell": version},
             "training_range": [start, train_end],
             "validation_range": [selection_targets[0], selection_targets[-1]] if selection_targets else None,
             "certification_range": [cert_targets[0], cert_targets[-1]] if cert_targets else None,
             "validation_targets": selection_targets, "certification_targets": cert_targets,
             "candidate_models": evaluations, "statistical_parameters": options,
             "ml_failures": ml_failures,
             "selected_candidate": candidate_payload,
             "incumbent": incumbent, "challenger": candidate_payload if eligible else None,
             **best_cert, "origins": {"selection": len(selection_targets), "certification": len(cert_targets)},
             "observations": {"selection": len(selection), "certification": len(certification),
                              "common_selection": len(candidates_on(selection, 1.0)),
                              "common_certification": len(candidates_on(certification, 1.0))},
             "training_wape": None, "validation_wape": best["validation_wape"],
             "live_wape": None, "certification_status": status}
    return {"chain_id": chain, "objective": products[0].objective, "version": version,
            "selection": {"official": incumbent, "applied_strategy": official,
                          "initial_champion_candidate": candidate_payload if incumbent is None else None,
                          "challenger": candidate_payload if eligible else None,
                          "status": "INITIAL_CHAMPION_CANDIDATE" if incumbent is None else "CHALLENGER" if eligible else "REJECTED",
                          "automatic_promotion": False, "incumbent": incumbent},
            "certification_status": status, "certified_metrics": incumbent_cert,
            "by_horizon": horizon_metrics, "by_product": product_metrics,
            "forecast_towell": forecast, "aggregates": aggregates, "model_audit": audit}
