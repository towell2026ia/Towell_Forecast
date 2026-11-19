"""Read the monthly inventory and reported fill-rate edition of BAASE."""
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
from pathlib import Path
import posixpath
from uuid import NAMESPACE_URL, uuid5
import xml.etree.ElementTree as ET
import zipfile

from services.assistant_api.commercial_catalog import key
from .fulfillment_source import BOOK
from .official_source import exact_quantity
from .unification import catalog_index, read_rows
from .unified_source import NS, _read_bytes

HEADERS = {"B": "CADENA", "C": "TIPO COMPRA", "D": "FORMATO", "E": "LINEA", "F": "FAMILIA", "G": "ITEM",
           "H": "UPC", "I": "DESCRIPCION", "J": "COLOR", "K": "TAMANO", "O": "FECHA", "P": "ANO",
           "Q": "MES", "R": "INVENTARIO", "S": "% FR"}
MONTHS = {key(month): i for i, month in enumerate(
    "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split(), 1)}


def _reported(cell, *, percentage=False):
    value, kind, formula, percent_style = cell
    if formula:
        raise ValueError("reported_formula_not_verifiable")
    if value in (None, "", "-"):
        return None
    if kind != "n":
        raise ValueError("reported_literal_required")
    if percentage and not percent_style:
        raise ValueError("percentage_format_required")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("invalid_reported_value")
    # BAASE defines zero as unreported independently for each field.
    if number == 0:
        return None
    return format(number * 100, "f") if percentage else exact_quantity(value)


def read_reported_source(path, digest, snapshot, *, cutoff):
    raw = _read_bytes(Path(path))
    if Path(path).name != BOOK or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("reported_baase_source_mismatch")
    source = {"id": str(uuid5(NAMESPACE_URL, "baase-reported-stock-fr:" + digest)), "source_hash": digest,
              "source_name": BOOK, "reference": "BAASE!R Inventario; S % FR reportado por producto y mes"}
    rows = []
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        shared = ["".join(e.itertext()) for e in ET.fromstring(book.read("xl/sharedStrings.xml"))] if "xl/sharedStrings.xml" in book.namelist() else []
        wb = ET.fromstring(book.read("xl/workbook.xml"))
        prop = wb.find("s:workbookPr", NS)
        epoch = datetime(1904, 1, 1) if prop is not None and prop.get("date1904") in {"true", "1"} else datetime(1899, 12, 30)
        sheet = next(e for e in wb.findall("s:sheets/s:sheet", NS) if e.get("name") == "BAASE")
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        target = rels[sheet.attrib["{" + NS["r"] + "}id"]]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if not member.startswith("xl/worksheets/"):
            raise ValueError("invalid_baase_sheet")
        styles = ET.fromstring(book.read("xl/styles.xml"))
        percent_ids = {9, 10} | {int(fmt.attrib["numFmtId"]) for fmt in styles.findall("s:numFmts/s:numFmt", NS)
                                 if fmt.get("formatCode") in {"0%", "0.0%", "0.00%"}}
        percent_styles = {i for i, xf in enumerate(styles.findall("s:cellXfs/s:xf", NS)) if int(xf.get("numFmtId", 0)) in percent_ids}
        headers_ok = False
        with book.open(member) as stream:
            for _, row in ET.iterparse(stream, events=("end",)):
                if row.tag != "{" + NS["s"] + "}row":
                    continue
                index = row.attrib["r"]
                cells = {}
                for c in row.findall("s:c", NS):
                    col = c.attrib["r"].rstrip("0123456789")
                    if col not in HEADERS:
                        continue
                    kind = c.get("t", "n")
                    value = c.findtext("s:v", None, NS)
                    if kind == "s" and value is not None:
                        value = shared[int(value)]
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.findall("s:is//s:t", NS))
                    cells[col] = (value, kind, c.find("s:f", NS) is not None, int(c.get("s", 0)) in percent_styles)
                row.clear()
                values = {col: cell[0] for col, cell in cells.items()}
                if index == "17":
                    headers_ok = all(key(values.get(col)) == label for col, label in HEADERS.items())
                if int(index) < 18 or key(values.get("C")) != "RS":
                    continue
                if not headers_ok:
                    raise ValueError("reported_baase_headers_changed")
                date_month = label_month = ""
                try:
                    date_month = (epoch + timedelta(days=int(Decimal(values["O"])))).strftime("%Y-%m")
                    label_month = f"{int(values['P']):04d}-{MONTHS[key(values['Q'])]:02d}"
                except (InvalidOperation, ValueError, TypeError, KeyError, OverflowError):
                    pass
                rows.append({"fila": index, "cadena": values.get("B") or "", "tipo_compra": "RS", "formato": values.get("D") or "",
                             "item": values.get("G") or "", "upc": values.get("H") or "", "periodo_fecha_q": date_month,
                             "periodo_etiqueta_rs": label_month, "cells": cells, "values": values})
        if not headers_ok:
            raise ValueError("reported_baase_headers_missing")
    inventory, rates, exceptions, missing, catalog = [], [], [], Counter(), {}
    for row, period, ids, errors in read_rows(rows, *catalog_index(snapshot)):
        index = row["fila"]
        if errors or not period or period > cutoff:
            exceptions.append({"row": index, "period": period, "upc": row["upc"], "reasons": errors or ["outside_cutoff"]})
            continue
        common = {"chain_id": ids[0], "product_id": ids[1], "purchase_type": "RS", "format": row["formato"], "upc": row["upc"],
                  "period": period, "source_id": source["id"], "source_sha256": digest}
        catalog.setdefault(ids, {**common, "chain": row["cadena"], "item": row["item"], "source_reference": "BAASE!B" + index + ":K" + index})
        for col, output in (("R", inventory), ("S", rates)):
            cell = row["cells"].get(col, (None, "n", False, False))
            reference = "BAASE!" + col + index
            try:
                value = _reported(cell, percentage=col == "S")
            except (ValueError, InvalidOperation) as error:
                exceptions.append({"row": index, "source_reference": reference, "reasons": [str(error)]})
                continue
            if value is None:
                missing[col] += 1
                continue
            record = {**common, "source_reference": reference}
            if col == "R":
                v = row["values"]
                record.update(chain=row["cadena"], item=row["item"], account=None, commercial_code=None,
                              product_type=v.get("E"), model=v.get("F"), presentation=v.get("K"), color=v.get("J"),
                              metric_code="INVENTORY_AGGREGATE", value=value, quantity_text=value, quantity=value,
                              source_value=cell[0], source_file=BOOK, source_sheet="BAASE", source_row=index, source_cell=col + index)
            else:
                record.update(reported_fill_rate_pct=value)
            output.append(record)
    return inventory, rates, source, exceptions, dict(missing), list(catalog.values())
