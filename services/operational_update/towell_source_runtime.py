"""FT-FIX-FUENTES-01 orchestration: Towell SALES and isolated stock provenance.

Preparation is offline. SQL activation is an explicit, separately guarded local
operation reusing the existing current-source writer; no migrations or engines
are executed by preparation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import hashlib
import os
import io
import posixpath
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
from uuid import NAMESPACE_URL, uuid5

from services.assistant_api.commercial_catalog import (
    homologate_size, is_apparel, key, prepare, product_type_evidence,
)
from .official_source import FACT_FIELDS, _master_bindings, exact_quantity
from .official_forecast import run as run_existing_forecast
from .preparation import REPO, file_hash, fingerprint
from .towell_sales_source import read_towell_sales_source, serializable
from .unification import catalog_index, json_read, write_csv, write_json, read_rows

CONTRACT = "towell-sales-v1"
POLICY = "FT-FIX-FUENTES-01"
EXPECTED_GENERATION = 2
IDENTITY_SHA = "63441a524ad38d88ce668f368f9c8b6c368179cd02e2253d8e152a651364eb48"
EXTRA_FIELDS = ("source_native_attributes", "source_raw_value_literal", "classification_reasons", "canonical_attribute_source")


def _shared_bytes(path):
    try:
        return Path(path).read_bytes()
    except PermissionError:
        if os.name != "nt":
            raise
        from .towell_sales_source import _read_windows_shared
        return _read_windows_shared(Path(path))


def read_inventory_source(path, approved_sha256, snapshot, *, cutoff):
    """Decode BAASE Y and identity/month cells only; T:X are never read as data."""
    path = Path(path)
    if path.name != "Estadistica Todas las Cadenas Cierre de Octubre 2026.xlsx":
        raise ValueError("inventory_workbook_name_invalid")
    raw = _shared_bytes(path)
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("credited_inventory_book_changed")
    source = {"id": str(uuid5(NAMESPACE_URL, "towell-inventory-contract:estadistica-stock-v1:" + approved_sha256)),
              "source_hash": approved_sha256, "source_name": path.name, "reference": "BAASE!Y INVENTORY ONLY; " + POLICY}
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    wanted = {"B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "Q", "R", "S", "Y"}
    months = {key(name): i for i, name in enumerate(
        "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split(), 1)}
    rows, exceptions = [], []
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ["".join(e.itertext()) for e in ET.fromstring(book.read("xl/sharedStrings.xml"))]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        date_1904 = workbook.find("s:workbookPr", ns)
        date_1904 = date_1904 is not None and date_1904.attrib.get("date1904") in {"1", "true"}
        rels = {e.attrib["Id"]: e.attrib["Target"] for e in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        sheet = next((e for e in workbook.findall("s:sheets/s:sheet", ns) if e.attrib["name"] == "BAASE"), None)
        if sheet is None:
            raise ValueError("inventory_baase_sheet_missing")
        member = rels[sheet.attrib["{" + ns["r"] + "}id"]]
        member = member.lstrip("/") if member.startswith("/") else posixpath.normpath("xl/" + member)
        if not member.startswith("xl/worksheets/"):
            raise ValueError("inventory_sheet_relation_invalid")
        with book.open(member) as stream:
            for _, node in ET.iterparse(stream, events=("end",)):
                if node.tag != "{" + ns["s"] + "}row":
                    continue
                rowno = int(node.attrib["r"])
                cells, formulas, literals = {}, set(), {}
                for cell in node:
                    ref = cell.attrib.get("r", "")
                    column = re.sub(r"\d+$", "", ref)
                    if column not in wanted:
                        continue
                    element = cell.find("s:v", ns)
                    value = element.text if element is not None else None
                    literals[column] = value
                    if cell.find("s:f", ns) is not None:
                        formulas.add(column)
                    if cell.attrib.get("t") == "s" and value is not None:
                        value = shared[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(cell.find("s:is", ns).itertext())
                    cells[column] = value
                if rowno == 17:
                    expected = {"B": "CADENA", "C": "TIPO COMPRA", "D": "FORMATO", "H": "UPC",
                                "Q": "FECHA", "R": "ANO", "S": "MES", "Y": "INVENTARIO"}
                    if any(key(cells.get(column)) != label for column, label in expected.items()):
                        raise ValueError("inventory_headers_changed")
                elif rowno >= 18 and any(cells.get(k) for k in ("B", "D", "H")):
                    if key(cells.get("C")) != "RS":
                        node.clear()
                        continue
                    period_date, period_label = "", ""
                    try:
                        serial = Decimal(cells.get("Q") or "")
                        epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
                        period_date = (epoch + timedelta(days=int(serial))).strftime("%Y-%m")
                        month = months[key(cells.get("S"))]
                        period_label = f"{int(Decimal(cells.get('R') or '')):04d}-{month:02d}"
                    except (ValueError, ArithmeticError, KeyError):
                        pass
                    rows.append({"fila": str(rowno), "cadena": cells.get("B") or "", "tipo_compra": "RS",
                        "formato": cells.get("D") or "", "linea": cells.get("E") or "", "modelo": cells.get("F") or "",
                        "item": cells.get("G") or "", "upc": cells.get("H") or "", "description": cells.get("I"),
                        "color": cells.get("J"), "tamano": cells.get("K"),
                        "periodo_fecha_q": period_date, "periodo_etiqueta_rs": period_label,
                        "stock_literal": literals.get("Y"), "stock_value": cells.get("Y"), "formula": "Y" in formulas})
                node.clear()
    scopes, identifiers = catalog_index(snapshot)
    inventory = []
    for row, period, ids, reasons in read_rows(rows, scopes, identifiers):
        if not row["stock_value"] or row["formula"]:
            reasons = [*reasons, "EMPTY_STOCK" if not row["stock_value"] else "FORMULA_STOCK_REVIEW"]
        try:
            value = exact_quantity(row["stock_literal"]) if row["stock_literal"] is not None else None
        except (ValueError, AttributeError):
            value, reasons = None, [*reasons, "INVALID_EXACT_STOCK_QUANTITY"]
        if period and period > cutoff:
            reasons = [*reasons, "STOCK_NOT_CLOSED"]
        if reasons:
            exceptions.append({"reason": ";".join(reasons), "source_reference": "BAASE!Y" + row["fila"],
                               "upc": row["upc"], "period": period, "source_value_literal": row["stock_literal"]})
            continue
        inventory.append({"chain_id": ids[0], "product_id": ids[1], "purchase_type": "RS", "chain": row["cadena"],
            "format": row["formato"], "upc": row["upc"], "item": row["item"], "account": None, "commercial_code": None,
            "product_type": row["linea"], "model": row["modelo"], "presentation": row["tamano"], "color": row["color"],
            "period": period, "metric_code": "INVENTORY_AGGREGATE", "value": value, "source_value": row["stock_literal"],
            "source_id": source["id"], "source_file": source["source_name"], "source_sha256": approved_sha256,
            "source_sheet": "BAASE", "source_row": row["fila"], "source_cell": "Y" + row["fila"],
            "source_reference": "BAASE!Y" + row["fila"], "source_product_type_cell": "E" + row["fila"],
            "source_model_cell": "F" + row["fila"], "source_color_cell": "J" + row["fila"], "source_presentation_cell": "K" + row["fila"],
            "available_at": "", "availability_source": "UNKNOWN"})
    if hashlib.sha256(_shared_bytes(path)).hexdigest() != approved_sha256:
        raise ValueError("inventory_book_changed_during_read")
    return inventory, source, exceptions


def _identity_key(row):
    return (str(row.get("account") or "").strip(), str(row.get("format") or "").strip(),
            str(row.get("upc", row.get("barcode")) or "").strip())


def _attributes(identity, rules):
    label = identity.get("article_size_label") or identity.get("description") or ""
    parsed = prepare(label, rules)
    original_size = identity.get("presentation")
    size = homologate_size(original_size, rules, apparel=is_apparel(label)) or original_size
    product_type = product_type_evidence(label, None, rules)
    return {"account": identity["account"], "format": identity["format"], "upc": identity["upc"],
            "commercial_code": identity.get("source_article_code"),
            "model": parsed["cleaned"] or None, "presentation": size,
            "color": identity.get("color"), "product_type": product_type["normalized"],
            "classification_reasons": parsed["problems"],
            "source_native_attributes": {k: identity.get(k) for k in (
                "description", "article_size_label", "presentation", "color", "source_article_code")}}


def prepare_packet(document, bindings, snapshot, stock_rows, stock_source, *, rules):
    """Normalize already accredited identity links; exceptions never become sales."""
    source, closure = document["source"], document["closure"]
    if (document.get("schema") != "towell-sales-source-1" or document.get("contract") != POLICY
            or source.get("sheet") != "Towell" or source.get("metric_code") != "SALES"
            or source.get("unit") != "PIEZAS" or not source.get("unchanged_after_read")
            or closure.get("last_closed_period") != "2026-10"):
        raise ValueError("towell_contract_required")
    sources = [
        {"id": str(uuid5(NAMESPACE_URL, "towell-source-contract:" + CONTRACT + ":" + source["sha256"])),
         "source_hash": source["sha256"], "source_name": source["name"], "reference": "Towell!K:M SALES; " + POLICY},
        dict(stock_source),
    ]
    if sources[0]["source_hash"] == sources[1]["source_hash"]:
        raise ValueError("independent_inventory_book_required")
    bound = defaultdict(list)
    for row in bindings:
        bound[_identity_key(row)].append(row)
    identities, exceptions, raw_references = {}, list(document.get("exceptions", [])), []
    by_target = defaultdict(list)
    for original in document["identities"]:
        rows = bound[_identity_key(original)]
        selected = rows[0] if (len(rows) == 1 and rows[0]["state"] == "COINCIDENCIA_VERIFICADA"
            and key(rows[0]["chain"]) == key(original.get("chain_name_original"))) else None
        result = {"identity_key": original["identity_key"], "raw": original,
                  "state": "COINCIDENCIA_VERIFICADA" if selected else "REVISION_REQUERIDA",
                  "reason": None if selected else "NO_UNIQUE_ACCREDITED_ACCOUNT_FORMAT_UPC_BINDING"}
        if selected:
            result.update(chain_id=selected["chain_id"], product_id=selected["product_id"],
                          attributes=_attributes(original, rules), binding_reference=selected["id"])
            by_target[selected["chain_id"], selected["product_id"]].append(result)
        identities[original["identity_key"]] = result
    for rows in by_target.values():
        if len(rows) > 1:
            for row in rows:
                row.update(state="AMBIGUO", reason="PRIVATE_IDENTITIES_SHARE_TARGET")
    facts = []
    for row in document["records"]:
        identity = identities.get(row["identity_key"])
        if not row["eligible"] or not identity or identity["state"] != "COINCIDENCIA_VERIFICADA":
            raw_references.append({**row, "binding_state": identity["state"] if identity else "UNRESOLVED"})
            continue
        if (row["metric_code"] != "SALES" or row["sheet"] != "Towell"
                or not re.fullmatch(r"Towell![KLM]\d+", row["source_reference"])
                or row["period"] > closure["last_closed_period"] or row["value_text"] is None
                or row.get("available_at") is not None or row.get("has_formula")):
            raise ValueError("towell_sales_provenance_invalid")
        try:
            amount = exact_quantity(row["value_text"])
        except ValueError:
            exceptions.append({"identity_key": row["identity_key"], "reason": "EXACT_QUANTITY_OUTSIDE_EXISTING_IMPORT_CONTRACT",
                               "source_reference": row["source_reference"], "source_value_literal": row["source_value_literal"]})
            continue
        own = identity["attributes"]
        facts.append({**own, "chain_id": identity["chain_id"], "product_id": identity["product_id"],
            "chain": identity["raw"].get("chain_name_original"), "purchase_type": "RS", "item": None,
            "period": row["period"], "metric_code": "SALES", "value": amount,
            "source_value": row["source_value_literal"], "source_raw_value_literal": row["source_value_literal"],
            "source_id": sources[0]["id"], "source_file": source["name"], "source_sha256": source["sha256"],
            "source_sheet": "Towell", "source_row": str(row["row"]), "source_cell": row["column"] + str(row["row"]),
            "source_reference": row["source_reference"], "available_at": "", "availability_source": "UNKNOWN",
            "source_model_cell": "AE" + str(row["row"]), "source_presentation_cell": "AF" + str(row["row"]),
            "source_color_cell": "AH" + str(row["row"]), "source_product_type_cell": "AE" + str(row["row"]),
            "master_identity_reference": identity["binding_reference"]})
    if not facts:
        raise ValueError("no_accredited_towell_sales")
    duplicate = Counter((r["chain_id"], r["product_id"], r["period"], "SALES") for r in facts)
    accepted = []
    for row in facts:
        if duplicate[row["chain_id"], row["product_id"], row["period"], "SALES"] > 1:
            exceptions.append({"reason": "DUPLICATE_SALES_DESTINATION", "source_reference": row["source_reference"]})
        else:
            accepted.append(row)
    facts = accepted
    canonical = {(r["chain_id"], r["product_id"]): r["attributes"] for r in identities.values()
                 if r["state"] == "COINCIDENCIA_VERIFICADA"}
    _, identifiers = catalog_index(snapshot)
    inventory = []
    for stock in stock_rows:
        if (stock.get("metric_code") != "INVENTORY_AGGREGATE" or stock.get("source_sheet") != "BAASE"
                or not re.fullmatch(r"BAASE!Y\d+", stock.get("source_reference", ""))
                or stock.get("source_id") != stock_source["id"] or stock.get("purchase_type") != "RS"):
            raise ValueError("inventory_only_credited_baase_y_required")
        if stock["period"] > closure["last_closed_period"]:
            raise ValueError("inventory_future_period_forbidden")
        if set(identifiers.get((stock["chain_id"], stock["upc"]), {})) != {stock["product_id"]}:
            exceptions.append({"reason": "INVENTORY_IDENTITY_NOT_UNIQUE", "source_reference": stock["source_reference"]})
            continue
        attrs = canonical.get((stock["chain_id"], stock["product_id"]))
        raw = {k: stock.get(k) or None for k in ("model", "presentation", "color", "product_type", "commercial_code", "account")}
        inventory.append({**stock, **(attrs or {}), "source_native_attributes": raw,
                          "canonical_attribute_source": {"contract": CONTRACT, "master_sha256": source["sha256"],
                              "identity": list((attrs or {}).get(k) for k in ("account", "format", "upc"))} if attrs else None,
                          "source_raw_value_literal": stock["source_value"], "available_at": "", "availability_source": "UNKNOWN"})
    policy = {"contract": POLICY, "sales_source": "TOWELL_KLM_ONLY", "inventory_source": "STATISTICA_BAASE_Y_ONLY",
              "all_quantities_unit": "PIEZAS", "last_closed_period": closure["last_closed_period"],
              "forbidden_statistics_metrics": ["SALES", "ORDER", "DELIVERY", "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY"],
              "no_old_forecast_reuse": True, "identity_criterion": "ACCREDITED_ACCOUNT_FORMAT_UPC_BINDING"}
    source_hash = fingerprint({"contract": CONTRACT, "sources": sources, "policy": policy,
                               "facts": facts, "inventory": inventory, "identities": list(identities.values())})
    for section in (facts, inventory):
        for row in section:
            row["id"] = str(uuid5(NAMESPACE_URL, fingerprint([source_hash, row["source_reference"], row["chain_id"],
                row["product_id"], row["period"], row.get("metric_code"), row["value"]])))
    periods = [r["period"] for r in facts + inventory]
    payload = {"schema": "official-source-1", "source_hash": source_hash, "first_period": min(periods),
               "last_period": max(periods), "sources": sources, "observations": facts, "inventory": inventory}
    context = {"sales_contract": CONTRACT, "sales_file_sha256": source["sha256"], "sheet": "Towell",
               "inventory_source": "ESTADISTICA", "authorized_source_hash": source_hash}
    return {"import": payload, "catalog": list(identities.values()), "exceptions": exceptions,
            "references": raw_references, "policy": policy, "forecast_source_context": context,
            "summary": {"reader_identities": len(identities), "binding_states": dict(Counter(r["state"] for r in identities.values())),
                        "sales_observations": len(facts), "sales_products": len({(r["chain_id"], r["product_id"]) for r in facts}),
                        "inventory_observations": len(inventory), "inventory_products": len({(r["chain_id"], r["product_id"]) for r in inventory}),
                        "first_period": min(periods), "last_period": max(periods), "cutoff": closure["last_closed_period"]}}


def prepare_source(*, workspace, master_sha256, bindings_path, bindings_sha256, snapshot_path, snapshot_sha256,
                   inventory_file, inventory_sha256, output):
    output = Path(output)
    if output.exists() or output.resolve().is_relative_to(REPO):
        raise ValueError("new_private_output_required")
    document = read_towell_sales_source(Path(workspace), expected_sha256=master_sha256)
    if file_hash(snapshot_path) != snapshot_sha256:
        raise ValueError("catalog_snapshot_changed")
    snapshot = json_read(snapshot_path)
    binding_manifest = json_read(Path(bindings_path).parent / "Version_candidata.json")
    if (not re.fullmatch(r"[0-9a-f]{64}", binding_manifest["source_hash"])
            or bindings_sha256 != IDENTITY_SHA):
        raise ValueError("accredited_identity_source_invalid")
    bindings = _master_bindings(Path(bindings_path), bindings_sha256, snapshot)
    inventory, stock_source, stock_exceptions = read_inventory_source(
        inventory_file, inventory_sha256, snapshot, cutoff=document["closure"]["last_closed_period"])
    rules = json_read(REPO / "config/commercial_catalog_rules.json")
    prepared = prepare_packet(document, bindings, snapshot, inventory, stock_source, rules=rules)
    prepared["exceptions"].extend({**r, "source": "ESTADISTICA_STOCK_ONLY"} for r in stock_exceptions)
    payload = prepared["import"]
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "Import.json", payload)
    columns = [*FACT_FIELDS, *EXTRA_FIELDS]
    write_csv(output / "Facts.csv", payload["observations"], columns)
    write_csv(output / "Inventory.csv", payload["inventory"], columns)
    for name, value in (("Catalog.json", prepared["catalog"]), ("Exceptions.json", prepared["exceptions"]),
                        ("References.json", serializable(prepared["references"])), ("Reader_Audit.json", serializable(document)),
                        ("Summary.json", prepared["summary"])):
        write_json(output / name, value)
    source = document["source"]
    write_json(output / "Authorized_Sales_Policy.json", {"contract": CONTRACT, "master_path": source["path"],
        "master_sha256": source["sha256"], "source_hash": payload["source_hash"], "generation": EXPECTED_GENERATION,
        "sheet": "Towell", "unit": "PIEZAS", "last_closed_period": document["closure"]["last_closed_period"]})
    manifest = {"schema": "official-source-1", "source_hash": payload["source_hash"], "generation": EXPECTED_GENERATION,
        "created_at": datetime.now(timezone.utc).isoformat(), "first_period": payload["first_period"],
        "cutoff": document["closure"]["last_closed_period"], "unit": "PIEZAS", "sources": payload["sources"],
        "policy": prepared["policy"], "forecast_source_context": prepared["forecast_source_context"],
        "input_hashes": {"master": source["sha256"], "inventory": stock_source["source_hash"],
                         "bindings": bindings_sha256, "catalog": snapshot_sha256,
                         "prior_master_used_for_identity_evidence": binding_manifest["source_hash"],
                         "commercial_rules": file_hash(REPO / "config/commercial_catalog_rules.json")},
        "outputs_sha256": {p.name: file_hash(p) for p in sorted(output.iterdir())}}
    write_json(output / "Manifest.json", manifest)
    return manifest, prepared["summary"]


def forecast_source(directory, manifest_sha256, output, *, compute=None):
    manifest = json_read(Path(directory) / "Manifest.json")
    if manifest.get("policy", {}).get("contract") != POLICY or manifest.get("generation") != EXPECTED_GENERATION:
        raise ValueError("towell_forecast_contract_required")
    kwargs = {"first_period": manifest["first_period"], "context_generation": EXPECTED_GENERATION,
              "extra_source_context": manifest["forecast_source_context"]}
    if compute is not None:
        kwargs["compute"] = compute
    return run_existing_forecast(directory, manifest_sha256, output, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_cli = sub.add_parser("prepare")
    for name in ("workspace", "bindings-path", "snapshot-path", "inventory-file", "output"):
        prepare_cli.add_argument("--" + name, type=Path, required=True)
    for name in ("master-sha256", "bindings-sha256", "snapshot-sha256", "inventory-sha256"):
        prepare_cli.add_argument("--" + name, required=True)
    forecast_cli = sub.add_parser("forecast")
    forecast_cli.add_argument("--source", type=Path, required=True)
    forecast_cli.add_argument("--manifest-sha256", required=True)
    forecast_cli.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    if action == "prepare":
        manifest, summary = prepare_source(**args)
        print(json.dumps({"source_hash": manifest["source_hash"], "generation": EXPECTED_GENERATION, "summary": summary}))
    else:
        result = forecast_source(args["source"], args["manifest_sha256"], args["output"])
        print(json.dumps(result["families"]))


if __name__ == "__main__":
    main()
