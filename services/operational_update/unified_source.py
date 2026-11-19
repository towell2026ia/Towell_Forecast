"""Read the single authorized unified workbook without external source fallback.

Values are parsed from OOXML numeric literals as Decimal text. Original books,
links and macros are never opened, refreshed or rewritten by this reader.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

MASTER_NAME = "FORECAST_Towell_Base_Unificada_2024_2025_2026.xlsx"
MASTER_SHA256 = "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d"
CONTRACT = "pedido-unificado-v1"
SHEETS = ("Resumen", "Base_2024_2026", "Metodos_2026", "Definitivas", "Catalogo", "Control", "Fuentes")
HISTORICAL_FIELDS = ("pedido_real_piezas", "venta_towell_real_piezas", "venta_cliente_real_piezas")
METHOD_FIELDS = ("movil_original", "suavizamiento_original", "regresion_original", "suavizacion_original", "propuesta_paquetes")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


class UnifiedSourceError(ValueError):
    """The authorized unified source or an explicit contract check failed."""


def boolean(value):
    if value is True or value in (1, "1", "True", "TRUE", "true"):
        return True
    if value is False or value in (0, "0", "False", "FALSE", "false"):
        return False
    raise UnifiedSourceError("UNIFIED_BOOLEAN_INVALID")


def quantity(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise UnifiedSourceError("UNIFIED_QUANTITY_INVALID")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise UnifiedSourceError("UNIFIED_QUANTITY_INVALID") from error
    if not number.is_finite() or number < 0:
        raise UnifiedSourceError("UNIFIED_QUANTITY_INVALID")
    return str(number)


def period(value, year=None, month=None, *, date1904=False):
    if isinstance(value, (date, datetime)):
        parsed = value
    elif isinstance(value, (int, float, Decimal)) or isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        parsed = datetime(1904, 1, 1) + timedelta(days=float(value)) if date1904 else datetime(1899, 12, 30) + timedelta(days=float(value))
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%Y-%m")
            except ValueError as error:
                raise UnifiedSourceError("UNIFIED_PERIOD_INVALID") from error
    else:
        raise UnifiedSourceError("UNIFIED_PERIOD_INVALID")
    if year is not None and int(year) != parsed.year or month is not None and int(month) != parsed.month:
        raise UnifiedSourceError("UNIFIED_PERIOD_CONTRADICTION")
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _read_bytes(path):
    try:
        return path.read_bytes()
    except PermissionError:
        import os
        if os.name != "nt":
            raise
        from .towell_sales_source import _read_windows_shared
        return _read_windows_shared(path)


def _cell(cell, strings):
    kind = cell.attrib.get("t", "n")
    node = cell.find("s:v", NS)
    raw = None if node is None else node.text
    if kind == "inlineStr":
        value = "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
    elif kind == "s":
        value = strings[int(raw)] if raw is not None else None
    elif kind == "b":
        value = raw == "1"
    elif kind == "e":
        raise UnifiedSourceError("UNIFIED_CELL_ERROR:" + cell.attrib["r"])
    else:
        value = raw
    formula = cell.find("s:f", NS)
    return {"value": value, "raw": raw, "type": kind,
            "formula": None if formula is None else formula.text, "has_formula": formula is not None}


def _rows(archive, target, strings):
    with archive.open(target) as stream:
        for _, node in ET.iterparse(stream, events=("end",)):
            if node.tag != "{" + NS["s"] + "}row":
                continue
            cells = {cell.attrib["r"]: _cell(cell, strings) for cell in node.findall("s:c", NS)}
            yield int(node.attrib["r"]), cells
            node.clear()


def _table(archive, target, strings, sheet):
    iterator = _rows(archive, target, strings)
    _, first = next(iterator)
    headers = {re.sub(r"[0-9]+$", "", address): data["value"] for address, data in first.items()}
    if len(set(headers.values())) != len(headers):
        raise UnifiedSourceError("UNIFIED_HEADERS_DUPLICATED:" + sheet)
    for row_number, cells in iterator:
        if not cells:
            continue
        values, lineage = {}, {}
        for column, name in headers.items():
            address = column + str(row_number)
            cell = cells.get(address, {"value": None, "raw": None, "type": "n", "formula": None, "has_formula": False})
            values[name] = cell["value"]
            lineage[name] = {"cell": sheet + "!" + address, **cell}
        yield row_number, values, lineage


def _identity(row):
    fields = [row.get(name) for name in ("cuenta", "formato", "upc")]
    if any(not isinstance(value, str) or not value for value in fields) or not re.fullmatch(r"[0-9]+", fields[2]):
        raise UnifiedSourceError("UNIFIED_IDENTITY_INVALID")
    key = "|".join(fields)
    if row.get("clave") != key:
        raise UnifiedSourceError("UNIFIED_IDENTITY_KEY_CONTRADICTION")
    return key


def _normalize(row, lineage, *, date1904=False):
    result = dict(row)
    result["clave"] = _identity(row)
    if "periodo" in row:
        result["periodo"] = period(row["periodo"], row.get("anio"), row.get("mes"), date1904=date1904)
    result["lineage"] = lineage
    return result


def read_unified_source(path, *, approved_sha256=MASTER_SHA256, last_closed_period="2026-09", reference_groups=None):
    """Return complete source-native rows, never a surrogate for a missing source.

    `approved_sha256` is mandatory exact content accreditation; tests supply the
    fingerprint of their own synthetic workbook. No basename/source fallback.
    """
    path = Path(path)
    if path.suffix.lower() != ".xlsx" or not re.fullmatch(r"[0-9a-f]{64}", approved_sha256):
        raise UnifiedSourceError("UNIFIED_MASTER_PATH_OR_SHA_INVALID")
    if last_closed_period != "2026-09":
        raise UnifiedSourceError("UNIFIED_CLOSURE_NOT_AUTHORIZED")
    raw = _read_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != approved_sha256:
        raise UnifiedSourceError("UNIFIED_MASTER_SHA_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.testzip() is not None:
            raise UnifiedSourceError("UNIFIED_ZIP_INVALID")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in relations}
        sheets = {}
        for node in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[node.attrib["{" + NS["r"] + "}id"]]
            sheets[node.attrib["name"]] = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if set(sheets) != set(SHEETS):
            raise UnifiedSourceError("UNIFIED_REQUIRED_SHEETS_MISMATCH")
        props = workbook.find("s:workbookPr", NS)
        date1904 = props is not None and props.attrib.get("date1904") in ("1", "true")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.text or "" for t in si.findall(".//s:t", NS))
                       for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        catalog, base, methods, definitives, metadata = [], [], [], [], {}
        for sheet, output in (("Catalogo", catalog), ("Base_2024_2026", base), ("Metodos_2026", methods), ("Definitivas", definitives)):
            for row_number, values, lineage in _table(archive, sheets[sheet], strings, sheet):
                record = _normalize(values, lineage, date1904=date1904)
                record["source_row"] = row_number
                record["source_sheet"] = sheet
                if sheet == "Catalogo":
                    record["tiene_historico"] = boolean(record["tiene_historico"])
                elif sheet == "Base_2024_2026":
                    for name in (*HISTORICAL_FIELDS, "propuesta_pedidos_piezas", "propuesta_towell_piezas", "propuesta_cliente_piezas", "definitiva_piezas", "referencia_comercial_piezas"):
                        record[name] = quantity(record[name])
                        if lineage[name]["has_formula"] and name in HISTORICAL_FIELDS:
                            raise UnifiedSourceError("UNIFIED_HISTORY_FORMULA_NOT_AUTHORIZED:" + lineage[name]["cell"])
                    for name in ("pedido_con_dato", "venta_towell_con_dato", "venta_cliente_con_dato", "referencia_con_dato", "definitiva_con_dato", "apto_entrenamiento_pedido", "champion_adoptado"):
                        record[name] = boolean(record[name])
                    expected = record["definitiva_piezas"] if record["definitiva_piezas"] is not None else record["propuesta_pedidos_piezas"] if record["periodo"].startswith("2026-") else None
                    if record["referencia_comercial_piezas"] is None and expected is not None and lineage["referencia_comercial_piezas"]["has_formula"]:
                        record["referencia_comercial_piezas"] = expected
                        record["reference_derived"] = True
                    if ((expected is None) != (record["referencia_comercial_piezas"] is None)
                            or expected is not None and Decimal(expected) != Decimal(record["referencia_comercial_piezas"])):
                        raise UnifiedSourceError("UNIFIED_COMMERCIAL_REFERENCE_CONTRADICTION:" + lineage["referencia_comercial_piezas"]["cell"])
                    for name, flag in zip((*HISTORICAL_FIELDS, "referencia_comercial_piezas", "definitiva_piezas"),
                                          ("pedido_con_dato", "venta_towell_con_dato", "venta_cliente_con_dato", "referencia_con_dato", "definitiva_con_dato"), strict=True):
                        if record[flag] is not (record[name] is not None):
                            raise UnifiedSourceError("UNIFIED_PRESENCE_FLAG_CONTRADICTION:" + lineage[name]["cell"])
                    if record["apto_entrenamiento_pedido"] and (record["periodo"] > last_closed_period or record["pedido_real_piezas"] is None):
                        raise UnifiedSourceError("UNIFIED_TRAINING_ELIGIBILITY_CONTRADICTION")
                elif sheet == "Metodos_2026":
                    for name in (*METHOD_FIELDS, "propuesta_piezas", "empaque"):
                        record[name] = quantity(record[name])
                else:
                    record["piezas"] = quantity(record["piezas"])
                    record["emision_conocida"] = boolean(record["emision_conocida"])
                output.append(record)
        for sheet in ("Resumen", "Control", "Fuentes"):
            metadata[sheet] = [{"row": row, "cells": {address: data for address, data in cells.items()}}
                               for row, cells in _rows(archive, sheets[sheet], strings)]
    catalog_keys = [r["clave"] for r in catalog]
    if len(set(catalog_keys)) != len(catalog_keys):
        raise UnifiedSourceError("UNIFIED_CATALOG_DUPLICATE")
    pairs = [(r["clave"], r["periodo"]) for r in base]
    if len(set(pairs)) != len(pairs) or {r["clave"] for r in base} != set(catalog_keys):
        raise UnifiedSourceError("UNIFIED_BASE_DUPLICATE_OR_UNKNOWN_IDENTITY")
    if any(r["clave"] not in set(catalog_keys) for r in methods + definitives):
        raise UnifiedSourceError("UNIFIED_DETAIL_UNKNOWN_IDENTITY")
    unchanged = hashlib.sha256(_read_bytes(path)).hexdigest() == digest
    if not unchanged:
        raise UnifiedSourceError("UNIFIED_MASTER_CHANGED_DURING_READ")
    result = {"schema": "unified-source-1", "contract": CONTRACT,
              "source": {"path": str(path.resolve()), "name": path.name, "sha256": digest, "bytes": len(raw), "unchanged_after_read": unchanged},
              "closure": {"last_closed_period": last_closed_period, "partial_from": "2026-10", "available_at": None,
                          "evidence": "Explicit project-owner instruction: Pedido September 2026 closed; October partial. Source workbook eligibility corroborated."},
              "catalog": catalog, "rows": base, "methods": methods, "definitives": definitives, "metadata": metadata}
    result["controls"] = controls(result, reference_groups=reference_groups)
    return result


def controls(document, *, reference_groups=None):
    rows = document["rows"]
    annual = {}
    with localcontext() as ctx:
        ctx.prec = 80
        for year in ("2024", "2025", "2026"):
            selected = [Decimal(r["pedido_real_piezas"]) for r in rows if r["periodo"].startswith(year) and r["pedido_real_piezas"] is not None]
            annual[year] = {"numeric": len(selected), "zeros": selected.count(Decimal(0)), "sum": str(sum(selected, Decimal(0)))}
        group_totals = {}
        for label, selector in (reference_groups or {}).items():
            selected = [r for r in rows if all(r.get(field) == value for field,value in selector.items())]
            group_totals[label] = {"identities": len({r["clave"] for r in selected}),
                "definitives_by_period": {p: str(sum((Decimal(r["definitiva_piezas"]) for r in selected
                    if r["periodo"] == p and r["definitiva_piezas"] is not None), Decimal(0)))
                    for p in sorted({r["periodo"] for r in selected if r["definitiva_piezas"] is not None})}}
    return {"identities": len(document["catalog"]), "with_history": sum(r["tiene_historico"] for r in document["catalog"]),
            "base_rows": len(rows), "historical_numeric": sum(r[name] is not None for r in rows for name in HISTORICAL_FIELDS),
            "method_rows": len(document["methods"]), "method_numeric": sum(r[name] is not None for r in document["methods"] for name in METHOD_FIELDS),
            "definitives": len(document["definitives"]), "definitives_by_period": dict(Counter(r["periodo"] for r in document["definitives"])),
            "references": sum(r["referencia_comercial_piezas"] is not None for r in rows), "order_by_year": annual,
            "order_2026_closed_numeric": sum(r["periodo"].startswith("2026-") and r["periodo"] <= "2026-09" and r["pedido_real_piezas"] is not None for r in rows),
            "order_october_partial": sum(r["periodo"] == "2026-10" and r["pedido_real_piezas"] is not None for r in rows),
            "training_numeric": sum(r["apto_entrenamiento_pedido"] for r in rows),
            "reference_groups": group_totals, "periods": sorted({r["periodo"] for r in rows})}


def write_extraction(document, directory):
    """Create an immutable private extraction; never overwrite an artifact."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "Extraction.json"
    if path.exists():
        raise FileExistsError(path)
    content = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(content)
    manifest = {"schema": "unified-source-extraction-1", "contract": CONTRACT,
                "created_at": datetime.now(timezone.utc).isoformat(), "source": document["source"],
                "controls": document["controls"], "outputs_sha256": {path.name: hashlib.sha256(content).hexdigest()}}
    (directory / "Extraction_Manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
