"""FORECAST Towell statistical engine (PRD 03).

The engine only consumes normalized monthly records. Excel parsing deliberately lives
outside this module. It is dependency-free so the same deterministic calculation can
run in a worker, batch container, or CI job.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

ENGINE_VERSION = "prd03-1.0.0"
HORIZONS = (1, 3, 6, 12)


def add_month(period: str, offset: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def clamp(values: Iterable[float]) -> list[float]:
    return [round(max(0.0, float(value)), 2) for value in values]


def naive(values: list[float], horizon: int) -> list[float]:
    return [values[-1]] * horizon


def seasonal_naive(values: list[float], horizon: int) -> list[float]:
    if len(values) < 12:
        raise ValueError("requiere 12 periodos")
    return [values[-12 + (i % 12)] for i in range(horizon)]


def moving_average(window: int) -> Callable[[list[float], int], list[float]]:
    def forecast(values: list[float], horizon: int) -> list[float]:
        if len(values) < window:
            raise ValueError(f"requiere {window} periodos")
        history = list(values)
        result: list[float] = []
        for _ in range(horizon):
            value = statistics.fmean(history[-window:])
            result.append(value)
            history.append(value)
        return result
    return forecast


def weighted_moving_average(values: list[float], horizon: int) -> list[float]:
    if len(values) < 3:
        raise ValueError("requiere 3 periodos")
    history = list(values)
    result: list[float] = []
    for _ in range(horizon):
        value = sum(v * w for v, w in zip(history[-3:], (0.2, 0.3, 0.5)))
        result.append(value)
        history.append(value)
    return result


def ses(values: list[float], horizon: int, alpha: float = 0.35) -> list[float]:
    level = values[0]
    for value in values[1:]:
        level = alpha * value + (1 - alpha) * level
    return [level] * horizon


def holt(values: list[float], horizon: int, alpha: float = 0.35, beta: float = 0.15) -> list[float]:
    if len(values) < 3:
        raise ValueError("requiere 3 periodos")
    level, trend = values[0], values[1] - values[0]
    for value in values[1:]:
        previous = level
        level = alpha * value + (1 - alpha) * (level + trend)
        trend = beta * (level - previous) + (1 - beta) * trend
    return [level + trend * step for step in range(1, horizon + 1)]


def holt_winters(values: list[float], horizon: int, season: int = 12,
                 alpha: float = 0.30, beta: float = 0.10, gamma: float = 0.20) -> list[float]:
    if len(values) < season * 2:
        raise ValueError("requiere 24 periodos")
    first, second = values[:season], values[season:season * 2]
    level = statistics.fmean(first)
    trend = (statistics.fmean(second) - level) / season
    seasonal = [value - level for value in first]
    for index, value in enumerate(values):
        slot = index % season
        previous = level
        level = alpha * (value - seasonal[slot]) + (1 - alpha) * (level + trend)
        trend = beta * (level - previous) + (1 - beta) * trend
        seasonal[slot] = gamma * (value - level) + (1 - gamma) * seasonal[slot]
    return [level + trend * step + seasonal[(len(values) + step - 1) % season] for step in range(1, horizon + 1)]


def regression(values: list[float], horizon: int, degree: int = 1) -> list[float]:
    n = len(values)
    if n < degree + 2:
        raise ValueError("muestra insuficiente")
    xs = list(range(n))
    if degree == 1:
        mean_x, mean_y = statistics.fmean(xs), statistics.fmean(values)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator
        intercept = mean_y - slope * mean_x
        return [intercept + slope * (n + step) for step in range(horizon)]
    # Normal equations for y = a + bx + cx²; solved with Gaussian elimination.
    matrix = [
        [n, sum(xs), sum(x*x for x in xs), sum(values)],
        [sum(xs), sum(x*x for x in xs), sum(x**3 for x in xs), sum(x*y for x, y in zip(xs, values))],
        [sum(x*x for x in xs), sum(x**3 for x in xs), sum(x**4 for x in xs), sum(x*x*y for x, y in zip(xs, values))],
    ]
    for pivot in range(3):
        row = max(range(pivot, 3), key=lambda r: abs(matrix[r][pivot]))
        matrix[pivot], matrix[row] = matrix[row], matrix[pivot]
        divisor = matrix[pivot][pivot]
        if abs(divisor) < 1e-12:
            raise ValueError("regresión singular")
        matrix[pivot] = [value / divisor for value in matrix[pivot]]
        for r in range(3):
            if r != pivot:
                factor = matrix[r][pivot]
                matrix[r] = [value - factor * base for value, base in zip(matrix[r], matrix[pivot])]
    a, b, c = (matrix[i][3] for i in range(3))
    return [a + b * (n + step) + c * (n + step) ** 2 for step in range(horizon)]


def intermittent(values: list[float], horizon: int, variant: str,
                 alpha: float = 0.1, beta: float = 0.1) -> list[float]:
    first = next((index for index, value in enumerate(values) if value > 0), None)
    if first is None:
        return [0.0] * horizon
    size, interval, gap = values[first], float(first + 1), 1
    probability = 1 / max(interval, 1)
    for value in values[first + 1:]:
        if variant == "tsb":
            probability = beta * (1.0 if value > 0 else 0.0) + (1 - beta) * probability
            if value > 0:
                size = alpha * value + (1 - alpha) * size
        elif value > 0:
            size = alpha * value + (1 - alpha) * size
            interval = beta * gap + (1 - beta) * interval
            gap = 1
        else:
            gap += 1
    if variant == "tsb":
        estimate = probability * size
    else:
        estimate = size / max(interval, 1e-9)
        if variant == "sba":
            estimate *= 1 - beta / 2
    return [estimate] * horizon


MODELS: dict[str, Callable[[list[float], int], list[float]]] = {
    "Naive": naive,
    "Naive estacional": seasonal_naive,
    "Media móvil 3": moving_average(3),
    "Media móvil 6": moving_average(6),
    "Media móvil 12": moving_average(12),
    "Media móvil ponderada": weighted_moving_average,
    "SES": ses,
    "Holt": holt,
    "Holt-Winters": holt_winters,
    "Tendencia lineal": lambda v, h: regression(v, h, 1),
    "Tendencia polinómica": lambda v, h: regression(v, h, 2),
    "Croston": lambda v, h: intermittent(v, h, "croston"),
    "SBA": lambda v, h: intermittent(v, h, "sba"),
    "TSB": lambda v, h: intermittent(v, h, "tsb"),
}


def classify(values: list[float]) -> tuple[str, dict[str, float]]:
    nonzero = [value for value in values if value > 0]
    adi = len(values) / len(nonzero) if nonzero else float("inf")
    cv2 = (statistics.pstdev(nonzero) / statistics.fmean(nonzero)) ** 2 if len(nonzero) > 1 and statistics.fmean(nonzero) else 0.0
    if len(values) > 4:
        xs = list(range(len(values)))
        mean_x, mean_y = statistics.fmean(xs), statistics.fmean(values)
        linear = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / sum((x - mean_x) ** 2 for x in xs)
    else:
        linear = 0.0
    mean = statistics.fmean(values) if values else 0.0
    trend_strength = abs(linear) / mean if mean else 0.0
    seasonal_corr = 0.0
    if len(values) >= 24:
        a, b = values[12:], values[:-12]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        den = math.sqrt(sum((x-ma)**2 for x in a) * sum((x-mb)**2 for x in b))
        seasonal_corr = sum((x-ma)*(y-mb) for x, y in zip(a, b)) / den if den else 0.0
    if not nonzero or adi >= 3.0:
        label = "Altamente intermitente"
    elif adi >= 1.32:
        label = "Intermitente"
    elif seasonal_corr >= 0.55 and trend_strength < 0.03:
        label = "Estacional"
    elif trend_strength >= 0.035:
        label = "Tendencia"
    elif cv2 >= 0.49:
        label = "Irregular"
    else:
        label = "Regular"
    return label, {"adi": round(adi, 3) if math.isfinite(adi) else 999.0, "cv2": round(cv2, 3), "trend_strength": round(trend_strength, 3), "seasonal_correlation": round(seasonal_corr, 3)}


def metrics(actual: list[float], predicted: list[float]) -> tuple[float, float, float]:
    errors = [a - p for a, p in zip(actual, predicted)]
    denominator = sum(actual)
    wape = 100 * sum(abs(error) for error in errors) / denominator if denominator else (0.0 if sum(abs(x) for x in predicted) == 0 else 999.0)
    bias = 100 * sum(errors) / denominator if denominator else 0.0
    absolute = [abs(error) for error in errors]
    stability = 100 * statistics.pstdev(absolute) / (statistics.fmean(actual) or 1) if len(absolute) > 1 else 0.0
    return round(wape, 2), round(bias, 2), round(stability, 2)


def backtest(values: list[float], model: Callable[[list[float], int], list[float]]) -> dict:
    rows = []
    for horizon in HORIZONS:
        origin_min = max(6, 12 if horizon >= 6 else 6)
        actual, predicted = [], []
        origins = []
        for origin in range(origin_min, len(values) - horizon + 1):
            try:
                forecast = clamp(model(values[:origin], horizon))
            except ValueError:
                continue
            actual.extend(values[origin:origin+horizon])
            predicted.extend(forecast)
            origins.append(origin)
        if actual:
            wape, bias, stability = metrics(actual, predicted)
            rows.append({"horizon": horizon, "origins": len(origins), "wape": wape, "bias": bias, "stability": stability})
    if not rows:
        raise ValueError("sin ventanas válidas")
    weight = sum(row["origins"] for row in rows)
    aggregate = {key: round(sum(row[key] * row["origins"] for row in rows) / weight, 2) for key in ("wape", "bias", "stability")}
    return {**aggregate, "windows": rows}


def allowed_models(classification: str) -> list[str]:
    if "intermitente" in classification.lower():
        return ["Naive", "Media móvil 3", "SES", "Croston", "SBA", "TSB"]
    return list(MODELS)


def active_lifecycle(periods: list[str], values: list[float]) -> tuple[list[str], list[float], int]:
    """Exclude structural pre-launch zeroes but preserve zero demand after launch."""
    first_active = next((index for index, value in enumerate(values) if value > 0), 0)
    return periods[first_active:], values[first_active:], first_active


def one_step_backtest(periods: list[str], values: list[float], model: Callable[[list[float], int], list[float]]) -> dict[str, float]:
    """Return leak-free one-step-ahead predictions for historical comparisons."""
    predictions: dict[str, float] = {}
    for origin in range(6, len(values)):
        try:
            predictions[periods[origin]] = clamp(model(values[:origin], 1))[0]
        except ValueError:
            continue
    return predictions


def run_series(series_id: str, label: str, periods: list[str], values: list[float], target: str) -> dict:
    model_periods, model_values, lifecycle_offset = active_lifecycle(periods, values)
    classification, diagnostics = classify(model_values)
    diagnostics["model_start_period"] = model_periods[0] if model_periods else periods[0]
    diagnostics["prelaunch_periods_excluded"] = lifecycle_offset
    comparisons = []
    for name in allowed_models(classification):
        try:
            result = backtest(model_values, MODELS[name])
            score = result["wape"] + abs(result["bias"]) * 0.25 + result["stability"] * 0.15
            comparisons.append({"model": name, **result, "score": round(score, 2), "applicable": True})
        except ValueError as error:
            comparisons.append({"model": name, "applicable": False, "reason": str(error)})
    applicable = [row for row in comparisons if row["applicable"]]
    if not applicable:
        return {"series_id": series_id, "label": label, "target": target, "status": "insufficient", "reason": "No hay muestra suficiente para una ventana de evaluación sin fuga."}
    winner = min(applicable, key=lambda row: row["score"])
    future = clamp(MODELS[winner["model"]](model_values, 12))
    historical_predictions = one_step_backtest(model_periods, model_values, MODELS[winner["model"]])
    history = [{"period": period, "actual": round(value, 2), "forecast": historical_predictions.get(period)} for period, value in zip(periods, values)]
    forecast = [{"period": add_month(periods[-1], step), "actual": None, "forecast": value} for step, value in enumerate(future, 1)]
    alerts = []
    if winner["wape"] >= 50:
        alerts.append({"type": "Deterioro de precisión", "severity": "alta", "message": f"WAPE de validación {winner['wape']:.1f}%; revisar antes de usar."})
    if abs(winner["bias"]) >= 25:
        alerts.append({"type": "Sesgo excesivo", "severity": "media", "message": f"Sesgo {winner['bias']:.1f}%; el modelo se conserva visible, no se corrige automáticamente."})
    if model_values[-3:] == [0, 0, 0]:
        alerts.append({"type": "Pérdida de movimiento", "severity": "media", "message": "Tres periodos cerrados consecutivos en cero."})
    lifecycle_note = f" La evaluación inicia en {model_periods[0]} para no tratar los meses estructurales previos al arranque como demanda intermitente." if lifecycle_offset else ""
    explanation = f"{winner['model']} minimizó el score controlado (WAPE, sesgo y estabilidad) en backtesting de origen rodante. La serie se clasificó como {classification.lower()}.{lifecycle_note}"
    return {
        "series_id": series_id, "label": label, "target": target, "status": "completed_with_alerts" if alerts else "completed",
        "classification": classification, "diagnostics": diagnostics, "winner": winner["model"], "wape": winner["wape"],
        "bias": winner["bias"], "stability": winner["stability"], "last_closed_period": periods[-1],
        "history": history, "forecast": forecast, "comparisons": sorted(applicable, key=lambda row: row["score"]),
        "alerts": alerts, "explanation": explanation,
    }


def load_normalized_csv(path: Path) -> tuple[dict[str, tuple[str, dict[str, float]]], str]:
    series: dict[str, tuple[str, dict[str, float]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["is_missing"].lower() == "true" or not row["value"].strip():
                continue
            target = row["objective"].capitalize()
            key = f"{target}:{row['canonical_product_id']}"
            label = row["description"].replace("TOALLA MB FENDI ", "").title()
            if key not in series:
                series[key] = (label, {})
            series[key][1][row["period"]] = float(row["value"])
    return series, "Walmart"


def build_payload(path: Path) -> dict:
    series, chain = load_normalized_csv(path)
    results = []
    for key, (label, points) in series.items():
        target, product = key.split(":", 1)
        periods = sorted(points)
        results.append(run_series(product, label, periods, [points[p] for p in periods], target))
    # Consolidated totals always include every observed FENDI component, even when
    # a recently launched product does not yet have enough history for its own model.
    totals_by_target = []
    targets = sorted({key.split(":", 1)[0] for key in series})
    for target in targets:
        components = [points for key, (_, points) in series.items() if key.startswith(f"{target}:")]
        all_periods = sorted({period for points in components for period in points})
        if not all_periods:
            continue
        totals = [sum(points.get(period, 0) or 0 for points in components) for period in all_periods]
        totals_by_target.append(run_series("total-fendi-bd", "Total FENDI BD", all_periods, totals, target))
    results = totals_by_target + results
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "run": {"version": f"FENDI-BD-{date.today().isoformat()}-01", "engine_version": ENGINE_VERSION, "status": "completed_with_alerts", "generated_at": generated_at, "frozen": True, "source": "normalized_platform_records", "cutoff": max((row.get("last_closed_period", "") for row in results), default="")},
        "chain": chain,
        "data_quality": [{"code": "D10", "severity": "blocking", "message": "2023 no se incorporó: falta una equivalencia FENDI reproducible aprobada."}, {"code": "SCOPE", "severity": "info", "message": "Universo auditado: Walmart y familia FENDI únicamente; Venta y Pedido permanecen separados."}],
        "series": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_payload(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
