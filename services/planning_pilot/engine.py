"""Versioned offline adapter; source quantities never inherit proposal pack units."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import io
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from services.statistical_engine import engine as stat

SCHEMA = "towell-planning-1"
MONTHS = "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split()
SOURCES = ("Towell", "Pedidos", "Cliente")
UNKNOWN_UNIT = "Unidad de origen sin equivalencia acreditada"
LEGACY_TRAINING = ("Towell", "Venta")
ORDER_TRAINING = ("Pedidos", "ORDER")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Cantidad no numérica, negativa o no finita")
    return value


def identity(account, barcode):
    return f"{str(account).strip()}|{str(barcode).strip()}"


def read_source(path: Path):
    source_bytes = path.read_bytes()
    book = load_workbook(io.BytesIO(source_bytes), read_only=True, data_only=True, keep_links=False)
    products, audit = {}, {"source_file": path.name, "source_sha256": hashlib.sha256(source_bytes).hexdigest(), "sheets": book.sheetnames, "counts": {}}
    # Inspect the stored XML, including formulas, without evaluating any external link.
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        audit["sheet_xml"] = [{"part": name, "sha256": hashlib.sha256(archive.read(name)).hexdigest(),
            "formulas": len(ET.fromstring(archive.read(name)).findall(".//{*}f"))}
            for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        audit["external_link_parts"] = len([n for n in archive.namelist() if n.startswith("xl/externalLinks/externalLink") and n.endswith(".xml")])
    try:
        for source in SOURCES:
            seen, count = set(), 0
            for row_no, r in enumerate(book[source].iter_rows(values_only=True), 1):
                if row_no < 6 or not r[0] or not r[4] or r[9] not in MONTHS:
                    continue
                key = identity(r[0], r[4])
                product = products.setdefault(key, {
                    "id": key, "account": str(r[0]), "chain": str(r[1]), "format": str(r[3]),
                    "barcode": str(r[4]), "description": str(r[5]), "model": str(r[30] or r[5]),
                    "color": str(r[33] or "Sin identificar"), "size": str(r[31] or "Sin identificar"),
                    "commercial_code": None, "history": {s: {} for s in SOURCES}, "issues": [],
                    "proposal": None, "champion": None, "champion_reason": "Sin Champion: no se aportó publicación acreditada para variante y corte",
                })
                if (key, r[9]) in seen:
                    product["issues"].append(f"Duplicado {source}!A{row_no}:J{row_no}")
                    continue
                seen.add((key, r[9])); count += 1
                if product["format"] != str(r[3]) or product["chain"] != str(r[1]):
                    product["issues"].append(f"Identidad de cuenta/formato conflictiva: {source}!D{row_no}")
                for col, year in enumerate((2024, 2025, 2026), 10):
                    period = f"{year}-{MONTHS.index(r[9]) + 1:02d}"
                    try:
                        value = number(r[col])
                    except ValueError as exc:
                        value = None
                        product["issues"].append(f"{source}!{chr(65+col)}{row_no}: {exc}")
                    product["history"][source][period] = {"value": value, "original": r[col],
                        "cell": f"{source}!{chr(65+col)}{row_no}", "pack_factor": r[39],
                        "unit": UNKNOWN_UNIT, "pieces": None, "unit_evidence": None}
            audit["counts"][source] = {"product_months": count, "products": len({k for k, m in seen})}
        definitive = []
        for row_no, r in enumerate(book["Propuesta RS Dic Def"].iter_rows(values_only=True), 1):
            if row_no < 6 or not r[0] or not r[4] or r[11] not in MONTHS:
                continue
            key = identity(r[0], r[4])
            product = products.setdefault(key, {"id": key, "account": str(r[0]), "chain": str(r[1]), "format": str(r[3]),
                "barcode": str(r[4]), "description": str(r[5]), "model": str(r[21] or r[5]), "color": str(r[24] or "Sin identificar"),
                "size": str(r[22] or "Sin identificar"), "history": {s: {} for s in SOURCES}, "issues": [], "proposal": None,
                "champion": None, "champion_reason": "Sin Champion: no se aportó publicación acreditada para variante y corte"})
            if product["proposal"] is not None:
                product["issues"].append(f"Propuesta duplicada: fila {row_no}")
            if product["format"] != str(r[3]):
                product["issues"].append(f"Formato incompatible: propuesta fila {row_no}")
            product["commercial_code"] = str(r[41]) if r[41] else None
            product["model"] = str(r[21] or product["model"])
            product["color"] = str(r[24] or product["color"])
            product["proposal"] = {"period": f"2026-{MONTHS.index(r[11])+1:02d}", "value": r[18], "unit": "piezas",
                "cell": f"Propuesta RS Dic Def!S{row_no}", "code_cell": f"Propuesta RS Dic Def!AP{row_no}",
                "code_evidence": "Valor guardado; depende de vínculo externo no actualizado"}
            definitive.append(product["proposal"])
        audit["proposal_total"] = sum(x["value"] or 0 for x in definitive)
        audit["proposal_by_month"] = {p: sum(x["value"] or 0 for x in definitive if x["period"] == p) for p in sorted({x["period"] for x in definitive})}
        audit["history_only"] = sum(p["proposal"] is None for p in products.values())
        audit["proposal_only"] = sum(not p["history"]["Towell"] for p in products.values())
        audit["hoja1"] = {str(r[0]): r[1] for r in book["Hoja1"].iter_rows(values_only=True) if r[0]}
        audit["stored_sheets"] = {sheet.title: {"rows": sheet.max_row, "columns": sheet.max_column,
            "cached_nonempty_cells": sum(v is not None for row in sheet.iter_rows(values_only=True) for v in row)} for sheet in book}
        conflicts = []
        for product in products.values():
            factors = {s: sorted({str(o["pack_factor"]) for o in product["history"][s].values() if o["pack_factor"] is not None}) for s in SOURCES}
            product["pack_conflict"] = bool(factors["Towell"] and factors["Cliente"] and factors["Towell"] != factors["Cliente"])
            if product["pack_conflict"]:
                conflicts.append({"id": product["id"], "factors": factors})
        audit["pack_conflicts"] = conflicts
        audit["pack_conflict_count"] = len(conflicts)
    finally:
        book.close()
    return list(products.values()), audit


def apply_units(products, evidence):
    """Explicit source + account/barcode + period evidence; never infer from packaging."""
    for product in products:
        for source in SOURCES:
            rule = evidence.get(source, {}).get(product["id"])
            if not rule:
                continue
            if not rule.get("evidence") or not rule.get("unit") or not rule.get("from") or not rule.get("through"):
                raise ValueError("Regla de unidad sin evidencia o vigencia")
            factor = rule.get("pieces_per_unit")
            if not isinstance(factor, (int, float)) or isinstance(factor, bool) or not math.isfinite(factor) or factor <= 0:
                raise ValueError("Factor de conversión inválido")
            for period, obs in product["history"][source].items():
                if rule["from"] <= period <= rule["through"]:
                    obs.update(unit=rule["unit"], pieces=None if obs["value"] is None else obs["value"] * factor,
                               unit_evidence=rule["evidence"], pieces_per_unit=factor)


def tail(history, cutoff):
    periods, values = [], []
    period = cutoff
    while period in history and history[period]["value"] is not None:
        periods.append(period); values.append(history[period]["value"])
        period = stat.add_month(period, -1)
    periods.reverse(); values.reverse()
    return periods, values


def wape(pairs):
    valid = [(a, f) for a, f in pairs if a is not None and f is not None]
    denominator = sum(abs(a) for a, f in valid)
    return {"value": sum(abs(a-f) for a, f in valid) / denominator * 100 if denominator else None,
            "absolute_error": sum(abs(a-f) for a, f in valid), "signed_error": sum(f-a for a,f in valid),
            "bias": sum(f-a for a, f in valid) / denominator * 100 if denominator else None,
            "pairs": len(valid), "denominator": denominator}


def evaluate_live(*, forecast, actual, emitted_at, target, actual_closed, comparable):
    if not actual_closed or not comparable or not emitted_at or actual is None or forecast is None:
        return None
    stamp = datetime.fromisoformat(emitted_at.replace("Z", "+00:00"))
    start = datetime.fromisoformat(target + "-01T00:00:00+00:00")
    if stamp.tzinfo is None or stamp >= start:
        return None
    return wape([(actual, forecast)])["value"]


def extension(method, values, first12):
    """Isolated H13/H14; no change to any production engine constant or contract."""
    try:
        future = stat.clamp(stat.MODELS[method](values, 14))
        if len(future) != 14 or future[:12] != first12 or not all(math.isfinite(v) for v in future):
            raise ValueError("H1–H12 no preservados")
        result = []
        for h in (13, 14):
            pairs, baseline_pairs = [], []
            for origin in range(12, len(values)-h+1):
                try:
                    f = stat.MODELS[method](values[:origin], h)[-1]
                    if math.isfinite(f):
                        pairs.append((values[origin+h-1], max(0, f)))
                        baseline_pairs.append((values[origin+h-1], values[origin-1]))
                except (ValueError, IndexError, ZeroDivisionError, OverflowError):
                    continue
            metrics = wape(pairs)
            baseline = wape(baseline_pairs)
            scale = max(sum(values[-12:])/len(values[-12:]), 1e-12)
            shifted = max(0, stat.MODELS[method](values[:-1], h+1)[-1])
            sensitivity = abs(future[h-1] - shifted) / scale
            stable = math.isfinite(sensitivity) and sensitivity <= 0.5 and future[h-1] <= 3 * max(values[-12:])
            validated = metrics["value"] is not None and baseline["value"] is not None and metrics["value"] <= max(baseline["value"] * 1.25, 1e-8)
            valid = metrics["pairs"] >= 3 and stable and validated
            result.append({"horizon": h, "value": future[h-1] if valid else None,
                "reason": "Extensión aislada validada; retrospectiva, no LIVE" if valid else "Sin cobertura: evidencia o estabilidad H13/H14 insuficiente",
                "backtest": metrics, "baseline": baseline, "sensitivity": sensitivity, "stable": stable})
        return result
    except (ValueError, IndexError, ZeroDivisionError, OverflowError) as exc:
        return [{"horizon": h, "value": None, "reason": f"Sin cobertura: {exc}", "backtest": wape([])} for h in (13, 14)]


def forecast_product(product, cutoff, *, training_source="Towell", objective="Venta"):
    product = copy.deepcopy(product)
    configuration = (training_source, objective)
    if configuration not in (LEGACY_TRAINING, ORDER_TRAINING):
        raise ValueError("Fuente y objetivo de entrenamiento incompatibles")
    order = configuration == ORDER_TRAINING
    if order:
        for history in product["history"].values():
            for obs in history.values():
                obs.update(unit="PIEZAS", pieces=obs["value"], unit_evidence="Confirmación comercial PRD 3A.9")
    periods, values = tail(product["history"][training_source], cutoff)
    product.update(method=None, projection=[], status="PENDING", reason=None, retrospective={}, candidates=[])
    if product["issues"]:
        product["reason"] = "; ".join(product["issues"])
        return product
    if not values or not any(values):
        product["reason"] = "Sin serie continua con movimiento hasta el cierre"
        return product
    try:
        first = next(i for i, value in enumerate(values) if value > 0)
        train = values[first:]
        if order:
            product["training_periods"] = periods[first:]
        calculated = stat.run_series(product["id"], product["description"], periods, values, objective)
        if calculated["status"] == "insufficient":
            product["reason"] = calculated["reason"]
            return product
        method = calculated["winner"]
        curve = [x["forecast"] for x in calculated["forecast"]]
        if len(curve) != 12 or not all(math.isfinite(v) for v in curve):
            raise ValueError("Curva H1–H12 inválida")
        product.update(method=method, status="PROVISIONAL", reason="Estadístico provisional; no publicado ni adoptado", candidates=calculated["comparisons"])
        product["alerts"] = calculated["alerts"]
        product["training_start"] = periods[first]
        training_obs = [product["history"][training_source][p] for p in periods[first:]]
        factors = {o.get("pieces_per_unit") for o in training_obs}
        factor = 1 if order else next(iter(factors)) if len(factors) == 1 and None not in factors else None
        product["unit"] = training_obs[-1]["unit"] if factor else UNKNOWN_UNIT
        product["pieces_factor"] = factor
        ext = extension(method, train, curve)
        for h, value in enumerate(curve + [r["value"] for r in ext], 1):
            reason = "H1–H12 motor existente" if h <= 12 else ext[h-13]["reason"]
            product["projection"].append({"period": stat.add_month(cutoff, h), "horizon": h, "value": value,
                "pieces": value * factor if value is not None and factor else None, "reason": reason})
        product["extension"] = ext
        predictions = stat.one_step_backtest(periods[first:], train, stat.MODELS[method])
        if order:
            product["evaluated_periods"] = list(predictions)
            product["retrospective"]["Pedidos"] = {
                **wape((product["history"]["Pedidos"][period]["value"], prediction)
                       for period, prediction in predictions.items()),
                "basis": "PIEZAS",
                "reason": "H1 de origen rodante contra Pedido real; método elegido en la misma muestra, sin validación independiente ni LIVE",
                "temporal_certification": False,
            }
            return product
        for source in SOURCES:
            pairs, basis = [], "piezas"
            for period, prediction in predictions.items():
                obs = product["history"][source].get(period)
                training = product["history"]["Towell"][period]
                if not obs:
                    continue
                if factor and obs["pieces"] is not None and training.get("pieces_per_unit") == factor:
                    pairs.append((obs["pieces"], prediction * factor))
                elif source == "Towell":
                    pairs.append((obs["value"], prediction)); basis = UNKNOWN_UNIT
            product["retrospective"][source] = {**wape(pairs), "basis": basis,
                "reason": "Retrospectivo H1 del método elegido con la muestra completa; no evaluación independiente ni LIVE" if pairs else "No evaluable: unidades comparables no acreditadas o sin pares",
                "temporal_certification": False}
    except (ValueError, IndexError, ZeroDivisionError, OverflowError) as exc:
        product.update(status="PENDING", projection=[], reason=str(exc))
    return product


def make_version(products, audit, *, cutoff, issued_at, version_id, code_sha, previous=None,
                 closure_confirmed_on=None, closure_confirmed_by="Usuario solicitante",
                 training_source="Towell", objective="Venta"):
    configuration = (training_source, objective)
    if configuration not in (LEGACY_TRAINING, ORDER_TRAINING):
        raise ValueError("Fuente y objetivo de entrenamiento incompatibles")
    order = configuration == ORDER_TRAINING
    if order and previous:
        raise ValueError("El forecast de Pedido requiere un paquete independiente")
    previous = copy.deepcopy(previous or {"schema": SCHEMA, "versions": []})
    if previous.get("schema") != SCHEMA or any(v["id"] == version_id for v in previous["versions"]):
        raise ValueError("Versión duplicada o esquema incompatible")
    if previous["versions"]:
        for index, saved in enumerate(previous["versions"]):
            content = {k: v for k, v in saved.items() if k != "hash"}
            expected_parent = digest(previous["versions"][index-1]) if index else None
            if saved.get("hash") != digest(content) or saved.get("previous_hash") != expected_parent:
                raise ValueError("Integridad del plan anterior inválida; no generar revisión")
        prior = previous["versions"][-1]
        if cutoff < stat.add_month(prior["cutoff"], 2) or issued_at <= prior["issued_at"]:
            raise ValueError("La revisión bimestral requiere un nuevo corte y emisión posterior")
    projected = []
    for product in products:
        try:
            projected.append(forecast_product(product, cutoff, training_source=training_source, objective=objective))
        except Exception as exc:
            # A local model/data exception must not discard other identities.
            failed = copy.deepcopy(product)
            failed.update(method=None, projection=[], status="PENDING", retrospective={}, candidates=[],
                          reason=f"Error aislado {type(exc).__name__}: {exc}")
            projected.append(failed)
    version = {"id": version_id, "issued_at": issued_at, "cutoff": cutoff, "last_closed": cutoff,
        "closure_evidence": "Cierre confirmado expresamente por el usuario",
        "closure_confirmed_on": closure_confirmed_on, "closure_confirmed_by": closure_confirmed_by,
        "last_quantity": max((m for p in products for s in SOURCES for m,o in p["history"][s].items() if o["value"] is not None), default=None),
        "code_sha": code_sha, "engine": stat.ENGINE_VERSION, "policy": "offline-statistical-2; extension: identical H1-H12, >=3 origins, WAPE<=1.25*Naive, cutoff sensitivity<=0.5*recent mean, value<=3*recent maximum; retrospective only",
        "original_id": previous["versions"][0]["id"] if previous["versions"] else version_id,
        "previous_hash": digest(previous["versions"][-1]) if previous["versions"] else None,
        "audit": audit, "products": projected,
        "summary": dict(Counter(p["status"] for p in projected)), "live": "No evaluable: no hay pronóstico previo acreditado para estos reales"}
    if order:
        version.update(training_source=training_source, objective=objective,
                       last_quantity=max((m for p in products for m, o in p["history"][training_source].items()
                                          if o["value"] is not None), default=None),
                       live="No evaluado. La emisión de noviembre no acredita un pronóstico previo a noviembre.")
    version["hash"] = digest(version)
    previous["versions"].append(version)
    if order:
        previous["pieces_confirmation"] = {
            "prd": "3A.9", "unit": "PIEZAS", "sources": ["Towell", "Cliente", "Pedidos"],
            "statement": "Confirmación comercial PRD 3A.9: series independientes en piezas sin conversión de empaque.",
            "confirmed_by": closure_confirmed_by, "recorded_at": issued_at, "presentation_version": "V04",
            "base_versions": [{"id": version_id, "hash": version["hash"]}],
            "source_file_sha256": audit["source_sha256"],
        }
    return previous


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--units", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--training-source", choices=("Towell", "Pedidos"), default="Towell")
    parser.add_argument("--objective", choices=("Venta", "ORDER"), default="Venta")
    parser.add_argument("--closure-confirmed-on", required=True)
    args = parser.parse_args()
    if (args.training_source, args.objective) == ORDER_TRAINING and args.units:
        parser.error("Pedido se calcula directamente en piezas sin reglas de conversión")
    if args.output.exists():
        parser.error("La salida ya existe; usa un identificador y archivo nuevos")
    products, audit = read_source(args.source)
    apply_units(products, json.loads(args.units.read_text(encoding="utf-8-sig")) if args.units else {})
    bundle = make_version(products, audit, cutoff=args.cutoff, issued_at=datetime.now(timezone.utc).isoformat(), version_id=args.version,
        code_sha=args.code_sha, previous=json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None,
        closure_confirmed_on=args.closure_confirmed_on, training_source=args.training_source, objective=args.objective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(bundle, handle, ensure_ascii=False, allow_nan=False)
    print(json.dumps(bundle["versions"][-1]["summary"]))


if __name__ == "__main__":
    main()
