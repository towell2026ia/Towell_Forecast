"""Accredited BAASE W/X pairs for fulfillment only, never engine input."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
from uuid import NAMESPACE_URL, uuid5
import zipfile

from services.assistant_api.commercial_catalog import key
from .official_source import exact_quantity
from .towell_source_runtime import _shared_bytes
from .unification import catalog_index, identity, source_key

CONTRACT = "estadistica-fulfillment-v1"
BOOK = "Estadistica Todas las Cadenas Cierre de Octubre 2026.xlsx"
MONTH = re.compile(r"20\d{2}-(?:0[1-9]|1[0-2])\Z")


def _quantity(value, literal, formula):
    if formula:
        raise ValueError("FORMULA_FULFILLMENT_REVIEW")
    if value is None or not str(value).strip() or str(value).strip() == "-":
        return None
    if literal is None:
        raise ValueError("FULFILLMENT_NUMERIC_LITERAL_REQUIRED")
    return exact_quantity(literal)


def read_fulfillment_source(path, approved_sha256, snapshot, *, partial_from="2026-10", observed_through="2026-10"):
    """Return rows, combined stock/fulfillment evidence, and isolated exceptions.

    Calendar month must agree in Q and R/S. W=Pedido and X=Entrega are read
    literally; no column T/U/V/Y quantity enters this complementary dataset.
    `partial_from` labels observations; it never accredits a training close.
    """
    path = Path(path)
    if path.name != BOOK or not MONTH.fullmatch(partial_from) or not MONTH.fullmatch(observed_through):
        raise ValueError("fulfillment_source_contract_invalid")
    raw = _shared_bytes(path)
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("credited_fulfillment_book_changed")
    source = {"id": str(uuid5(NAMESPACE_URL, CONTRACT + ":" + approved_sha256)),
        "source_hash": approved_sha256, "source_name": path.name,
        "reference": "BAASE!W Pedido + X Entrega: FILL RATE ONLY; Y Inventario: STOCK ONLY; NOT FORECAST INPUT"}
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    wanted = {"B", "C", "D", "G", "H", "Q", "R", "S", "W", "X"}
    months = {key(name): i for i, name in enumerate("Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split(), 1)}
    rows, headers_seen = [], False
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        shared = ["".join(e.itertext()) for e in ET.fromstring(book.read("xl/sharedStrings.xml"))] if "xl/sharedStrings.xml" in book.namelist() else []
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        prop = workbook.find("s:workbookPr", ns)
        date_1904 = prop is not None and prop.attrib.get("date1904") in {"1", "true"}
        rels = {e.attrib["Id"]: e.attrib["Target"] for e in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        sheet = next((e for e in workbook.findall("s:sheets/s:sheet", ns) if e.attrib["name"] == "BAASE"), None)
        if sheet is None:
            raise ValueError("fulfillment_baase_missing")
        member = rels[sheet.attrib["{" + ns["r"] + "}id"]]
        member = member.lstrip("/") if member.startswith("/") else posixpath.normpath("xl/" + member)
        if not member.startswith("xl/worksheets/"):
            raise ValueError("fulfillment_sheet_relation_invalid")
        with book.open(member) as stream:
            for _, node in ET.iterparse(stream, events=("end",)):
                if node.tag != "{" + ns["s"] + "}row":
                    continue
                rowno, values, literals, formulas = int(node.attrib["r"]), {}, {}, set()
                for cell in node:
                    column = re.sub(r"\d+$", "", cell.attrib.get("r", ""))
                    if column not in wanted:
                        continue
                    element = cell.find("s:v", ns)
                    literal = element.text if element is not None else None
                    value = literal
                    if cell.find("s:f", ns) is not None:
                        formulas.add(column)
                    if cell.attrib.get("t") == "s" and value is not None:
                        value = shared[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(cell.find("s:is", ns).itertext())
                    literals[column] = literal if cell.attrib.get("t", "n") == "n" else None
                    values[column] = value
                if rowno == 17:
                    expected = {"B": "CADENA", "C": "TIPO COMPRA", "D": "FORMATO", "H": "UPC", "Q": "FECHA", "R": "ANO", "S": "MES", "W": "PEDIDO", "X": "ENTREGA"}
                    if any(key(values.get(k)) != v for k, v in expected.items()):
                        raise ValueError("fulfillment_headers_changed")
                    headers_seen = True
                elif rowno >= 18 and key(values.get("C")) == "RS" and any(values.get(k) for k in ("B", "D", "H")):
                    period_date, period_label = "", ""
                    try:
                        serial = Decimal(values.get("Q") or "")
                        epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
                        period_date = (epoch + timedelta(days=int(serial))).strftime("%Y-%m")
                        period_label = f"{int(Decimal(values.get('R') or '')):04d}-{months[key(values.get('S'))]:02d}"
                    except (ValueError, ArithmeticError, KeyError):
                        pass
                    rows.append({"fila": str(rowno), "cadena": values.get("B") or "", "tipo_compra": "RS", "formato": values.get("D") or "",
                        "item": values.get("G") or "", "upc": values.get("H") or "", "periodo_fecha_q": period_date,
                        "periodo_etiqueta_rs": period_label, "values": values, "literals": literals, "formulas": formulas})
                node.clear()
    if not headers_seen:
        raise ValueError("fulfillment_headers_missing")
    scopes, identifiers = catalog_index(snapshot)
    origin_counts = Counter(source_key(r, r["periodo_etiqueta_rs"]) for r in rows)
    mapped = [(row, *identity(row, scopes, identifiers)) for row in rows]
    target_counts = Counter((*ids, row["periodo_etiqueta_rs"]) for row, ids, _ in mapped if ids)
    accepted, exceptions = [], []
    for row, ids, bad_identity in mapped:
        period, reasons = row["periodo_etiqueta_rs"], []
        if bad_identity:
            reasons.append(bad_identity)
        if not MONTH.fullmatch(period) or period != row["periodo_fecha_q"] or period > observed_through:
            reasons.append("FULFILLMENT_PERIOD_CONFLICT_OR_OUTSIDE_SOURCE")
        if origin_counts[source_key(row, period)] > 1 or ids and target_counts[(*ids, period)] > 1:
            reasons.append("FULFILLMENT_DUPLICATE_SCOPE_PRODUCT_MONTH")
        values = {}
        for field, column in (("order_value", "W"), ("delivery_value", "X")):
            try:
                values[field] = _quantity(row["values"].get(column), row["literals"].get(column), column in row["formulas"])
            except ValueError as error:
                reasons.append(str(error) + ":" + column)
        reference = "BAASE!W" + row["fila"] + ":X" + row["fila"]
        if reasons:
            exceptions.append({"period": period, "upc": row["upc"], "source_reference": reference, "reasons": reasons})
            continue
        accepted.append({"chain_id": ids[0], "product_id": ids[1], "period": period, "format": row["formato"], "upc": row["upc"],
            "purchase_type": "RS", "source_id": source["id"], "source_sha256": approved_sha256, "source_reference": reference,
            "order_source_reference": "BAASE!W" + row["fila"], "delivery_source_reference": "BAASE!X" + row["fila"],
            "partial_period": period >= partial_from, **values})
    if hashlib.sha256(_shared_bytes(path)).hexdigest() != approved_sha256:
        raise ValueError("fulfillment_book_changed_during_read")
    return accepted, source, exceptions
