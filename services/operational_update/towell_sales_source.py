"""Strict read-only Towell K:M SALES source for FT-FIX-FUENTES-01.

This new source interpretation is explicit business authorization. It never
rewrites earlier pilot files, reads other commercial sheets, binds catalogue
UUIDs, converts packs or runs a forecast.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

SCHEMA = "towell-sales-source-1"
CONTRACT = "FT-FIX-FUENTES-01"
MASTER_NAME = "Copia de Pronostico RS Dic Def V1.xlsx"
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[3]
SHEET = "Towell"
CUTOFF = "2026-10"
YEARS = {"K": 2024, "L": 2025, "M": 2026}
MONTHS = {name: n for n, name in enumerate(
    "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split(), 1)}
ATTRIBUTES = {"account": 0, "chain_name_original": 1, "format": 3, "upc": 4, "description": 5,
              "source_article_code": 29, "article_size_label": 30, "presentation": 31, "color": 33}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


class TowellSourceError(ValueError):
    """A source-level failure; individual row problems stay in exceptions."""


def _read_windows_shared(path):
    """GENERIC_READ only, permitting an existing Excel reader/writer handle.

    The same file is read again for the final SHA check. There is no write
    access, lock override, changed file permission or alternative source.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x00000007, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def read_master_bytes(path: Path) -> bytes:
    """Read only the exact master basename directly under datos_privados."""
    path = Path(path)
    if path.name != MASTER_NAME or path.parent.name != "datos_privados":
        raise TowellSourceError("TOWELL_MASTER_PATH_INVALID")
    try:
        return path.read_bytes()
    except PermissionError:
        if os.name != "nt":
            raise
        return _read_windows_shared(path)


def _text(value):
    return None if value is None or value == "" else str(value)


def _identity_key(attributes):
    return json.dumps([attributes["account"], attributes["format"], attributes["upc"]],
                      ensure_ascii=False, separators=(",", ":"))


def _historical_xml(source):
    """Only Towell K:M raw literals/formula flags, never evaluate a formula."""
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in relations}
        selected = next((node for node in workbook.findall("s:sheets/s:sheet", NS)
                         if node.attrib["name"] == SHEET), None)
        if selected is None:
            raise TowellSourceError("TOWELL_SHEET_MISSING")
        target = targets[selected.attrib["{" + NS["r"] + "}id"]]
        target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if not target.startswith("xl/worksheets/"):
            raise TowellSourceError("TOWELL_SHEET_RELATION_INVALID")
        output = {}
        with archive.open(target) as stream:
            for _, cell in ET.iterparse(stream, events=("end",)):
                if cell.tag != "{" + NS["s"] + "}c":
                    continue
                coordinate = cell.attrib["r"]
                if re.fullmatch(r"[KLM]\d+", coordinate):
                    raw = cell.find("s:v", NS)
                    formula = cell.find("s:f", NS)
                    output[coordinate] = {"xml_type": cell.attrib.get("t", "n"),
                        "source_value_literal": None if raw is None else raw.text,
                        "has_formula": formula is not None,
                        "formula_text": None if formula is None else formula.text,
                        "formula_attributes": None if formula is None else dict(formula.attrib)}
                cell.clear()
        return output


def _quantity(parsed, raw):
    if raw.get("has_formula"):
        state = "FORMULA_HISTORY_REVIEW"
    elif parsed is None or parsed == "":
        return None, "EMPTY"
    else:
        state = "OBSERVED"
    if parsed is None or parsed == "":
        return None, state
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)) or raw.get("xml_type", "n") != "n":
        return None, "NON_NUMERIC_HISTORY_REVIEW"
    try:
        number = Decimal(raw["source_value_literal"])
    except (KeyError, InvalidOperation, TypeError):
        return None, "INVALID_NUMERIC_LITERAL_REVIEW"
    if not number.is_finite() or number < 0:
        return None, "INVALID_NUMERIC_LITERAL_REVIEW"
    return number, state


def serializable(value):
    """JSON boundary keeps Decimal exact; an absent amount remains null."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def read_towell_sales_source(workspace: Path | None = None, *, confirmed_closed_period: str = CUTOFF):
    """Read the exact authorized file, with explicit per-cell eligibility.

    `workspace` is only an injectable workspace root (also useful in synthetic
    tests); callers cannot substitute another workbook or choose a fallback.
    Product UUID binding is deliberately left to the existing reconciliation.
    """
    from openpyxl import load_workbook

    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", confirmed_closed_period):
        raise TowellSourceError("TOWELL_CLOSURE_PERIOD_INVALID")
    root = DEFAULT_WORKSPACE if workspace is None else Path(workspace)
    path = root / "datos_privados" / MASTER_NAME
    if not path.is_file():
        raise TowellSourceError("TOWELL_MASTER_NOT_FOUND: " + str(path))
    try:
        source = read_master_bytes(path)
    except OSError as error:
        raise TowellSourceError("TOWELL_MASTER_UNREADABLE: " + str(path)) from error
    digest = hashlib.sha256(source).hexdigest()
    raw = _historical_xml(source)
    book = load_workbook(io.BytesIO(source), read_only=True, data_only=True, keep_links=False)
    identities, records, exceptions = {}, [], []
    variants = defaultdict(set)
    seen = defaultdict(list)
    rows = 0
    try:
        sheet = book[SHEET]
        headers = next(sheet.iter_rows(min_row=5, max_row=5, min_col=11, max_col=13, values_only=True))
        if tuple(headers) != ("Venta 2024", "Venta 2025", "Venta 2026"):
            raise TowellSourceError("TOWELL_HISTORY_HEADERS_MISMATCH")
        for row_number, row in enumerate(sheet.iter_rows(min_row=6, max_col=34, values_only=True), 6):
            if not any(row[index] is not None for index in (0, 4, 9, 10, 11, 12)):
                continue
            rows += 1
            attributes = {key: _text(row[index]) for key, index in ATTRIBUTES.items()}
            month = MONTHS.get(row[9])
            row_reason = None
            if not month:
                row_reason = "MONTH_UNRECOGNIZED"
            elif any(attributes[key] is None for key in ("account", "format", "upc")):
                row_reason = "ACCOUNT_FORMAT_UPC_MISSING"
            identity = _identity_key(attributes) if not row_reason else None
            if identity:
                identities.setdefault(identity, {"identity_key": identity, **attributes})
                variants[identity].add(json.dumps(attributes, sort_keys=True, ensure_ascii=False))
            if row_reason:
                exceptions.append({"reason": row_reason, "row": row_number, "identity_key": identity,
                    "source_reference": f"{SHEET}!A{row_number}:M{row_number}", "attributes": attributes,
                    "month_original": _text(row[9])})
            for column, year in YEARS.items():
                coordinate = column + str(row_number)
                original = row[ord(column) - ord("A")]
                trace = raw.get(coordinate, {})
                amount, state = _quantity(original, trace)
                period = f"{year}-{month:02d}" if month else None
                record = {"identity_key": identity, "period": period, "metric_code": "SALES", "unit": "PIEZAS",
                    "value": amount, "value_text": None if amount is None else format(amount, "f"),
                    "source_value_original": original, "source_value_literal": trace.get("source_value_literal"),
                    "source_file": MASTER_NAME, "source_sha256": digest, "sheet": SHEET, "row": row_number,
                    "column": column, "source_reference": f"{SHEET}!{coordinate}", "available_at": None,
                    "has_formula": trace.get("has_formula", False), "formula_text": trace.get("formula_text"),
                    "state": row_reason or state, "eligible": bool(identity and state == "OBSERVED" and period <= confirmed_closed_period)}
                record["quantity_state"] = ("EXPLICIT_ZERO" if amount == 0 else "POSITIVE_OBSERVED") if amount is not None else (
                    "EMPTY" if state == "EMPTY" else "UNUSABLE_SOURCE_VALUE")
                if period and period > confirmed_closed_period and state == "OBSERVED":
                    record.update(state="NOT_CLOSED", eligible=False)
                records.append(record)
                if identity and period:
                    seen[(identity, period)].append(record)
                if state not in {"EMPTY", "OBSERVED"}:
                    exceptions.append({"reason": state, "identity_key": identity, "source_reference": record["source_reference"]})
    finally:
        book.close()
    for (identity, period), entries in seen.items():
        if len(entries) > 1:
            for record in entries:
                record.update(state="DUPLICATE_SOURCE_KEY", eligible=False)
            exceptions.append({"reason": "DUPLICATE_SOURCE_KEY", "identity_key": identity, "period": period,
                "source_references": [record["source_reference"] for record in entries]})
    for identity, attributes in variants.items():
        if len(attributes) > 1:
            for record in records:
                if record["identity_key"] == identity:
                    record.update(state="IDENTITY_ATTRIBUTES_CONFLICT", eligible=False)
            exceptions.append({"reason": "IDENTITY_ATTRIBUTES_CONFLICT", "identity_key": identity,
                "attributes": [json.loads(item) for item in sorted(attributes)]})
    activity = defaultdict(list)
    for record in records:
        if record["identity_key"]:
            activity[record["identity_key"]].append(record)
    for entries in activity.values():
        observed = sorted(r["period"] for r in entries if r["eligible"])
        positives = sorted(r["period"] for r in entries if r["eligible"] and r["value"] > 0)
        for record in entries:
            period = record["period"]
            if record["quantity_state"] in {"EXPLICIT_ZERO", "POSITIVE_OBSERVED"}:
                evidence = "OBSERVED_QUANTITY_ONLY"
            elif record["quantity_state"] != "EMPTY" or not period:
                evidence = "UNKNOWN_ACTIVITY"
            elif period > confirmed_closed_period:
                evidence = "FUTURE_NO_REAL"
            elif positives and period < positives[0]:
                evidence = "BEFORE_FIRST_POSITIVE_OBSERVED"
            elif any(p < period for p in observed) and any(p > period for p in observed):
                evidence = "CAPTURE_GAP"
            else:
                evidence = "UNKNOWN_ACTIVITY"
            record["activity_evidence"] = evidence
    for record in records:
        record.setdefault("activity_evidence", "UNKNOWN_ACTIVITY")
    if hashlib.sha256(read_master_bytes(path)).hexdigest() != digest:
        raise TowellSourceError("TOWELL_MASTER_CHANGED_DURING_READ")
    known = [record["period"] for record in records if record["value"] is not None and record["period"]]
    eligible = [record["period"] for record in records if record["eligible"]]
    return {"schema": SCHEMA, "contract": CONTRACT,
        "source": {"path": str(path.resolve()), "name": MASTER_NAME, "sha256": digest, "sheet": SHEET,
            "metric_code": "SALES", "unit": "PIEZAS", "interpretation": "PROJECT_OWNER_AUTHORIZED_TOWELL_OBSERVED_SALES",
            "historical_columns": dict(YEARS), "unchanged_after_read": True,
            "product_opening_dates": "NOT_ACCREDITED",
            "activity_evidence_limit": "Blank cells never establish a product opening date, PRE-LAUNCH or inactivity."},
        "closure": {"last_closed_period": confirmed_closed_period,
            "explicitly_confirmed_periods": ["2026-09", "2026-10"] if confirmed_closed_period == CUTOFF else [confirmed_closed_period],
            "confirmed_by": "PROJECT_OWNER_USER" if confirmed_closed_period == CUTOFF else "EXPLICIT_CALLER_CONFIGURATION", "confirmed_at": None,
            "evidence": "Conversation: September and October2026 are closed in this same Towell master; FT-FIX-FUENTES-01 authorizes K:M as observed SALES."
                if confirmed_closed_period == CUTOFF else "Explicit caller-supplied month closure; no confirmation timestamp supplied.",
            "available_at": None, "point_in_time_certified": False},
        "identities": list(identities.values()), "records": records, "exceptions": exceptions,
        "summary": {"identities": len(identities), "physical_month_rows": rows, "historical_cells": len(records),
            "states": dict(Counter(record["state"] for record in records)), "eligible_cells": len(eligible),
            "first_period_with_quantity": min(known) if known else None, "last_period_with_quantity": max(known) if known else None,
            "first_eligible_period": min(eligible) if eligible else None, "last_eligible_period": max(eligible) if eligible else None}}
