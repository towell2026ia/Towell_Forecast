"""Descriptive expanding-window evaluation. Never point-in-time certification.

The statistical functions/model factories are reused directly, without legacy
payload builders. Targets after each origin are excluded from EVERY fitted step.
"""
from __future__ import annotations

import math
from dataclasses import asdict

from services.statistical_engine import engine as stat
from services.ml_engine.engine import MODEL_FACTORIES, Preprocessor
from .engine import (HORIZONS, _bands, _feature, _metrics,
                     _ml_prediction, _training_samples, add_month, month_end)
from .selection_policy import SELECTION_POLICY, evaluation_signature, select_band_rows, suggest_reference

EVALUATION_MODE = "RETROSPECTIVE_EVALUATION"
MAX_FOLDS = 12
REQUIREMENTS = {"Naive": 1, "Naive estacional": 12, "Media móvil 3": 3,
                "Media móvil 6": 6, "Media móvil 12": 12,
                "Media móvil ponderada": 3, "SES": 1, "Holt": 3,
                "Holt-Winters": 24, "Tendencia lineal": 3,
                "Tendencia polinómica": 4, "Croston": 1, "SBA": 1, "TSB": 1}


def predict(name, history, horizon):
    output = stat.MODELS[name](history, horizon)
    value = float(output[-1])
    if not math.isfinite(value):
        raise ValueError("invalid_statistical_prediction")
    return max(0.0, value)


def identities_at(products, origin):
    return sorted({value for item in products if item.history(origin)
                   for value in (item.chain_id, item.category, item.key)})


def feature_names(identities):
    return (["mes objetivo", "trimestre", "seno mes", "coseno mes", "horizonte", "antigüedad"]
            + [f"lag {lag}" for lag in (1, 2, 3, 6, 12)]
            + [f"{statistic} {width}" for width in (3, 6, 12)
               for statistic in ("media", "desviación", "mínimo", "máximo")]
            + ["meses desde venta", "ceros", "proporción movimiento", "intervalo"]
            + [f"{kind}:{identity}" for identity in identities for kind in ("cadena", "categoría", "producto")])


def scored(rows, family, model):
    relevant = [row for row in rows if model in row[family]]
    metrics = _metrics([(row["actual"], row[family][model]) for row in relevant])
    return {**{f"retrospective_{key}": value for key, value in metrics.items()},
            "origins": len({row["origin"] for row in relevant}), "observations": len(relevant),
            "by_horizon": [{"horizon": h, **_metrics([(row["actual"], row[family][model])
                              for row in relevant if row["horizon"] == h]),
                            "observations": sum(row["horizon"] == h for row in relevant)}
                           for h in stat.HORIZONS]}


def order(candidate):
    return (candidate["retrospective_wape"], abs(candidate["retrospective_bias"]), candidate["model"])


def forecast_chain(products, issue, policy, incumbent, on_stage=None):
    chain = products[0].chain_id
    if on_stage:
        on_stage("STATISTICAL")
    # All statistical candidate forecasts use only the contiguous tail at origin.
    # Like stat.backtest, H6/H12 require a 12-period train; H1/H3 require six.
    periods = sorted({period for item in products for period in item.observations if period < issue})
    origins = [period for period in periods if any(len(item.history(period)) >= 6 for item in products)][-MAX_FOLDS:]
    rows, fold_audit = [], []
    for origin in origins:
        for item in products:
            history = item.history(origin)
            allowed = stat.allowed_models(stat.classify(history)[0]) if history else []
            for h in stat.HORIZONS:
                target = add_month(origin, h)
                observed = item.observations.get(target)
                if target > issue or observed is None or observed.value is None or len(history) < (12 if h >= 6 else 6):
                    continue
                predictions = {}
                for name in allowed:
                    if len(history) < REQUIREMENTS.get(name, 1):
                        continue
                    try:
                        predictions[name] = predict(name, history, h)
                    except (ValueError, IndexError, ZeroDivisionError):
                        continue
                rows.append({"product_id": item.product_id, "category": item.category,
                             "origin": origin, "target_period": target, "horizon": h,
                             "actual": observed.value, "actual_observation_id": observed.observation_id,
                             "statistical": predictions, "ml": {}})
    stat_candidates, choices = [], {}
    for item in products:
        history = item.history(issue)
        classification = stat.classify(history)[0] if history else "Sin historial"
        allowed = stat.allowed_models(classification) if history else []
        relevant = [row for row in rows if row["product_id"] == item.product_id]
        evaluated = [name for name in stat.MODELS if name in allowed and any(name in r["statistical"] for r in relevant)]
        # Models compared for selection share exactly the same origin/target set.
        common = [row for row in relevant if all(name in row["statistical"] for name in evaluated)] if evaluated else []
        local = []
        for name in stat.MODELS:
            reason = ("CLASSIFICATION_UNSUPPORTED" if name not in allowed else
                      f"REQUIRES_{REQUIREMENTS.get(name, 1)}_PERIODS" if len(history) < REQUIREMENTS.get(name, 1)
                      else "NO_COMPARABLE_ROLLING_ORIGINS")
            metrics = scored(common if name in evaluated else [], "statistical", name)
            valid = metrics["retrospective_wape"] is not None
            if metrics["observations"] and not valid:
                reason = "ZERO_ACTUAL_DENOMINATOR"
            candidate = {"family": "statistical", "model": name, "product_id": item.product_id,
                         "classification": classification, "evaluation_mode": EVALUATION_MODE,
                         "status": "EVALUATED" if valid else "NOT_APPLICABLE", "reason": None if valid else reason,
                         "available": valid, **metrics}
            local.append(candidate)
        valid = sorted([c for c in local if c["status"] == "EVALUATED"], key=order)
        choices[item.product_id] = valid[0]["model"] if valid else "Naive"
        for candidate in local:
            candidate["selected"] = candidate["model"] == choices[item.product_id]
        stat_candidates.extend(local)

    if on_stage:
        on_stage("ML")
    # Each fold builds a vocabulary and fits a NEW preprocessor/model on TRAIN.
    # _training_samples excludes targets after origin and never fills missing actuals.
    ml_failures = []
    for origin in origins:
        test = [row for row in rows if row["origin"] == origin]
        if not test:
            continue
        identities = identities_at(products, origin)
        x, y = _training_samples(products, origin, identities)
        if len(x) < policy.min_train_observations:
            continue
        fold_audit.append({"origin": origin, "train_target_max": origin, "training_samples": len(x),
                           "test_target_min": min(row["target_period"] for row in test)})
        by_id = {item.product_id: item for item in products}
        vectors = [_feature(by_id[row["product_id"]], origin, row["target_period"], identities) for row in test]
        for name, factory in MODEL_FACTORIES.items():
            try:
                prep = Preprocessor().fit(x)
                model = factory().fit(prep.transform(x), y)
                values = model.predict(prep.transform(vectors))
                for row, value in zip(test, values):
                    if not math.isfinite(float(value)):
                        raise ValueError("invalid_ml_prediction")
                    row["ml"][name] = max(0.0, float(value))
            except Exception as exc:
                for row in test:
                    row["ml"].pop(name, None)
                ml_failures.append({"model": name, "error": type(exc).__name__})
    identities = identities_at(products, issue)
    current_x, current_y = _training_samples(products, issue, identities)
    trainable = len(current_x) >= policy.min_train_observations
    evaluated_ml = [name for name in MODEL_FACTORIES if any(name in row["ml"] for row in rows)]
    common_ml = [row for row in rows if all(name in row["ml"] for name in evaluated_ml)
                 and choices[row["product_id"]] in row["statistical"]] if evaluated_ml else []
    ml_candidates = []
    for name in MODEL_FACTORIES:
        metrics = scored(common_ml, "ml", name)
        validated = metrics["retrospective_wape"] is not None and metrics["origins"] >= 2
        ml_candidates.append({"family": "ml", "model": name, "evaluation_mode": EVALUATION_MODE,
                              "training_samples": len(current_x), "validation_observations": metrics["observations"],
                              "trainable": trainable, "validated": validated, "selected": False,
                              "available": validated, "status": "EVALUATED" if validated else "NOT_APPLICABLE",
                              "reason": None if validated else "MODEL_FAILED" if any(f["model"] == name for f in ml_failures)
                              else "INSUFFICIENT_TRAINING_SAMPLES" if not trainable else "INSUFFICIENT_TEMPORAL_FOLDS",
                              **metrics})
    valid_ml = sorted([c for c in ml_candidates if c["validated"]], key=order)
    ml_choice = valid_ml[0]["model"] if valid_ml else None
    product_ml_choices = {}
    for item in products:
        product_rows = [row for row in common_ml if row["product_id"] == item.product_id]
        valid = [{"model": name, **scored(product_rows, "ml", name)} for name in MODEL_FACTORIES]
        valid = [candidate for candidate in valid if candidate["retrospective_wape"] is not None and
                 candidate["observations"] >= policy.min_product_observations]
        product_ml_choices[item.product_id] = min(valid, key=order)["model"] if valid else None
    if ml_choice:
        next(c for c in ml_candidates if c["model"] == ml_choice)["selected"] = True
    operational_models = {}
    if trainable:
        for name, factory in MODEL_FACTORIES.items():
            try:
                prep = Preprocessor().fit(current_x)
                operational_models[name] = prep, factory().fit(prep.transform(current_x), current_y)
            except Exception as exc:
                ml_failures.append({"model": name, "error": type(exc).__name__})
    operational = operational_models.get(ml_choice)
    for candidate in ml_candidates:
        candidate["operational_ready"] = bool(candidate["selected"] and operational)
    if on_stage:
        on_stage("ENSEMBLE")
    common = [row for row in rows if choices[row["product_id"]] in row["statistical"]
              and (ml_choice is None or ml_choice in row["ml"])]
    def pairs(data, weight):
        return [(r["actual"], r["statistical"][choices[r["product_id"]]] * weight
                 + (r["ml"].get(ml_choice, 0.0) * (1 - weight))) for r in data]
    # Reuse ensemble search. There is NO promotion or relaxed No Degradation gate.
    ensemble_failures = []
    try:
        from .engine import _ensemble_weights
        weights = _ensemble_weights(common, choices, ml_choice, incumbent) if ml_choice else [1.0]
    except Exception:
        weights = [1.0]
        ensemble_failures.append("ENSEMBLE_FAILED")
    strategies = []
    for weight in weights:
        score = _metrics(pairs(common, weight))
        if score["wape"] is not None:
            strategies.append({"strategy": "statistical" if weight == 1 else "ml" if weight == 0 else "ensemble",
                               "statistical_weight": weight, **{f"retrospective_{k}": v for k, v in score.items()},
                               "observations": len(common), "evaluation_mode": EVALUATION_MODE,
                               "by_horizon": [{"horizon": h, **_metrics(pairs([r for r in common if r["horizon"] == h], weight))}
                                              for h in HORIZONS]})
    strategies.sort(key=lambda c: (c["retrospective_wape"], abs(c["retrospective_bias"]), -c["statistical_weight"]))
    leader = strategies[0] if strategies else {"strategy": "statistical", "statistical_weight": 1.0, "retrospective_wape": None}
    # Descriptive leader never replaces an existing published model without PIT evidence.
    applied = leader if incumbent is None else {"strategy": "statistical_fallback", "statistical_weight": 1.0}
    weight = applied["statistical_weight"]
    forecast = []
    product_ml_forecasts = {}
    for item in products:
        history = item.history(issue)
        for h in HORIZONS:
            target = add_month(issue, h)
            statistical = predict(choices[item.product_id], history, h) if history else 0.0
            ml = None
            if operational and history:
                try:
                    ml = _ml_prediction(operational[1], operational[0], _feature(item, issue, target, identities))
                except Exception as exc:
                    ml_failures.append({"model": ml_choice, "error": type(exc).__name__})
            own_ml_name = product_ml_choices[item.product_id]
            if own_ml_name and history and own_ml_name in operational_models:
                try:
                    own_prep, own_model = operational_models[own_ml_name]
                    product_ml_forecasts[(item.product_id, h)] = _ml_prediction(
                        own_model, own_prep, _feature(item, issue, target, identities))
                except Exception as exc:
                    ml_failures.append({"model": own_ml_name, "error": type(exc).__name__})
            value = statistical * weight + ml * (1 - weight) if ml is not None else statistical
            band_basis, band_rows = select_band_rows(common, product_id=item.product_id,
                category=item.category, horizon=h)
            residuals = [a - p for a, p in pairs(band_rows, weight)]
            forecast.append({"chain_id": chain, "product_id": item.product_id, "product_code": item.product_code,
                             "category": item.category, "variant": item.variant, "objective": item.objective,
                             "issue_period": issue, "target_period": target, "horizon": h,
                             "statistical": round(statistical, 2), "ml": round(ml, 2) if ml is not None else None,
                             "forecast_towell": round(value, 2), "statistical_model": choices[item.product_id],
                             "ml_model": ml_choice if ml is not None else None, "model_strategy": applied["strategy"] if ml is not None or weight == 1 else "statistical_fallback",
                             "classification": stat.classify(history)[0] if history else "Sin historial",
                             "forecast_status": item.lifecycle(issue, policy), "confidence": "Baja",
                             "certification_status": "PROVISIONAL_TEMPORAL_UNKNOWN",
                             "probability": _bands(value, residuals) if band_rows else None,
                             "band_basis": band_basis, "band_observations": len(residuals),
                             "band_status": "AVAILABLE" if band_rows else "INSUFFICIENT_BAND_EVIDENCE"})
    aggregates = []
    for level in ("category", "chain"):
        for h in HORIZONS:
            for key in sorted({r["category"] if level == "category" else chain for r in forecast}):
                members = [r for r in forecast if r["horizon"] == h and (level == "chain" or r["category"] == key)]
                aggregates.append({"level": level, "key": key, "horizon": h, "target_period": add_month(issue, h),
                                   "forecast_towell": round(sum(r["forecast_towell"] for r in members), 2),
                                   "statistical_value": round(sum(r["statistical"] for r in members), 2),
                                   "ml_value": round(sum(r["ml"] for r in members), 2) if all(r["ml"] is not None for r in members) else None})
    # Scope model scores aggregate actual/predicted pairs, NOT product WAPE means.
    scope_candidates = []
    for name in stat.MODELS:
        metrics = scored(rows, "statistical", name)
        reasons = sorted({c["reason"] for c in stat_candidates if c["model"] == name and c["reason"]})
        scope_candidates.append({"family": "statistical", "model": name, **metrics,
                                 "status": "EVALUATED" if metrics["retrospective_wape"] is not None else "NOT_APPLICABLE",
                                 "reason": None if metrics["retrospective_wape"] is not None else reasons[0] if len(reasons) == 1 else "NO_APPLICABLE_PRODUCTS_OR_ORIGINS",
                                 "products_evaluated": sum(c["model"] == name and c["status"] == "EVALUATED" for c in stat_candidates)})
    for candidate in ml_candidates:
        candidate["by_product"] = [{"product_id": p.product_id, **scored([r for r in common_ml if r["product_id"] == p.product_id], "ml", candidate["model"])} for p in products]
    stat_pairs = [(r["actual"], r["statistical"][choices[r["product_id"]]]) for r in rows if choices[r["product_id"]] in r["statistical"]]
    selected_stat_rows = [{**r, "selected_statistical": {"selected": r["statistical"][choices[r["product_id"]]]}}
                          for r in rows if choices[r["product_id"]] in r["statistical"]]
    selected_stat_metrics = {"model": "Selección por producto", "evaluation_mode": EVALUATION_MODE,
                             **scored(selected_stat_rows, "selected_statistical", "selected")}
    ml_pairs = [(r["actual"], r["ml"][ml_choice]) for r in common_ml if ml_choice and ml_choice in r["ml"]]
    for strategy in strategies:
        strategy["by_product"] = [{"product_id": p.product_id,
                                    **{f"retrospective_{k}": v for k, v in _metrics(pairs([r for r in common if r["product_id"] == p.product_id], strategy["statistical_weight"])).items()}}
                                   for p in products]
    # Product recommendations are evaluated on the identical paired rows.
    # Scope scores remain descriptive and cannot win an individual product.
    product_candidates, suggested_references = [], {}
    for item in products:
        own_forecast = [row for row in forecast if row["product_id"] == item.product_id]
        comparable = [row for row in (common_ml if product_ml_choices[item.product_id] else common)
                      if row["product_id"] == item.product_id]
        signature = evaluation_signature(chain_id=chain, category_id=item.category,
            product_id=item.product_id, objective=item.objective, issue_period=issue,
            evaluation_mode=EVALUATION_MODE, rows=comparable) if comparable else None
        for family, model, weight_value in (("statistical", choices[item.product_id], 1.0),
                                             ("ml", product_ml_choices[item.product_id], 0.0),
                                             ("ensemble", "weighted", next((s["statistical_weight"] for s in strategies
                                                if s["strategy"] == "ensemble"), None))):
            if model is None or weight_value is None or family == "ensemble" and any(row["ml"] is None for row in own_forecast) or family == "ml" and any((item.product_id, h) not in product_ml_forecasts for h in HORIZONS):
                continue
            def candidate_pairs(data):
                if family == "ml":
                    return [(row["actual"], row["ml"][model]) for row in data if model in row["ml"]]
                return pairs(data, weight_value)
            values = candidate_pairs(comparable)
            metrics = _metrics(values)
            by_horizon = [{"horizon": h, **_metrics(candidate_pairs([r for r in comparable if r["horizon"] == h])),
                           "observations": sum(r["horizon"] == h for r in comparable)} for h in HORIZONS]
            product_candidates.append({"family": family, "model": model,
                "strategy": family, "product_id": item.product_id,
                **metrics, "observations": len(comparable),
                "windows": len({row["origin"] for row in comparable}),
                "by_horizon": by_horizon, "evaluation_signature": signature,
                "evidence_mode": "RETROSPECTIVE_TRAINING",
                "horizons": [{"horizon": row["horizon"], "target_period": row["target_period"],
                    "value": round(product_ml_forecasts[(item.product_id, row["horizon"])] if family == "ml" else
                                   row["statistical"] * weight_value + (row["ml"] or 0) * (1 - weight_value), 2)}
                    for row in own_forecast]})
        suggested_references[item.product_id] = suggest_reference(
            [candidate for candidate in product_candidates if candidate["product_id"] == item.product_id])
    return {"chain_id": chain, "objective": products[0].objective, "policy": asdict(policy),
            "evaluation_mode": EVALUATION_MODE, "certification_status": "PROVISIONAL_TEMPORAL_UNKNOWN",
            "certified_metrics": {"certified_wape": None, "certified_bias": None},
            "forecast_towell": forecast, "aggregates": aggregates, "by_horizon": [], "by_product": [],
            "selection": {"preview_leader": leader, "preview_challenger": strategies[1] if len(strategies) > 1 else None,
                          "scope_leader": leader, "suggested_references": suggested_references,
                          "product_ml_winners": product_ml_choices,
                          "scope_comparable_baseline": {"retrospective_wape": _metrics(pairs(common, 1.0))["wape"],
                              "retrospective_bias": _metrics(pairs(common, 1.0))["bias"],
                              "by_horizon": [{"horizon": h, **_metrics(pairs([r for r in common if r["horizon"] == h], 1.0))}
                                             for h in HORIZONS], "observations": len(common)},
                          "comparison_status": "PRODUCT_GRAIN", "comparison_reason": None,
                          "applied_strategy": applied, "status": "PREVIEW", "official": incumbent,
                          "no_degradation": False if incumbent else None, "automatic_promotion": False,
                          "promotion_allowed": False, "initial_champion_candidate": None, "challenger": None},
            "model_audit": {"candidate_models": stat_candidates + ml_candidates, "product_candidates": product_candidates,
                            "selection_policy_version": SELECTION_POLICY.version,
                            "scope_statistical_candidates": scope_candidates,
                            "evaluation_mode": EVALUATION_MODE, "dataset_cutoff": month_end(issue), "temporal_certification": False,
                            "certified_wape": None, "certified_bias": None, "certification_status": "PROVISIONAL_TEMPORAL_UNKNOWN",
                            "origins": {"selection": len(origins), "certification": 0}, "folds": fold_audit,
                            "evaluation_horizons": list(stat.HORIZONS), "max_folds": MAX_FOLDS,
                            "training_samples": len(current_x), "training_products": sum(bool(p.history(issue)) for p in products),
                            "feature_names": feature_names(identities), "ml_candidates": ml_candidates,
                            "available_statistical_candidates": list(stat.MODELS), "available_ml_candidates": list(MODEL_FACTORIES),
                            "retrospective_statistical_metrics": _metrics(stat_pairs), "retrospective_ml_metrics": _metrics(ml_pairs),
                            "selected_statistical_metrics": selected_stat_metrics,
                            "ml_failures": ml_failures, "ensemble_failures": ensemble_failures}}
