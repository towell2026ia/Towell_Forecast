"""Prepare the two sealed commercial books as a private standalone source."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from .preparation import REPO, SOURCES, file_hash, fingerprint, label, number
from .unification import (
    CUTOFF, FACTS, METRICS, PLAN, REFERENCE, ROW_FIELDS, catalog_index, csv_rows,
    fendi_control, json_read, summarize_rows, verify_inputs,
    write_csv, write_json,
)


SCHEMA = "official-source-1"
FIELD_COLUMNS = {
    "fcst_cliente_piezas": "T", "fcst_towell_piezas": "U", "venta_piezas": "V",
    "pedido_piezas": "W", "entrega_piezas": "X", "inventario_reportado_piezas": "Y",
}
FACT_FIELDS = (
    "id", "chain_id", "product_id", "purchase_type", "chain", "format", "upc", "item",
    "account", "commercial_code", "product_type", "model", "presentation", "color",
    "source_product_type_cell", "source_model_cell", "source_presentation_cell", "source_color_cell",
    "master_identity_reference", "period", "metric_code", "value", "source_value",
    "source_id", "source_file", "source_sha256", "source_sheet", "source_row", "source_cell",
    "source_reference", "available_at", "availability_source",
)
EXCEPTION_FIELDS = (
    "source_id", "source_file", "source_sha256", "source_sheet", "source_row", "source_cell",
    "source_reference", "purchase_type", "chain", "format", "upc", "period", "metric_code",
    "value", "reason",
)


def exact_quantity(literal):
    state, normalized = number(literal)
    if state != "VALUE":
        raise ValueError("invalid_source_quantity")
    value = Decimal(normalized)
    if value >= Decimal("1e15") or value.as_tuple().exponent < -16:
        raise ValueError("source_quantity_outside_contract")
    return format(Decimal(literal.replace(",", "")), "f")


def _book_sources(package, hashes):
    entries = json_read(package / "manifest.json")["sources"]
    books = {}
    for entry in entries:
        name = entry["name"]
        kind = "closing" if name.startswith("Estadistica ") else "master"
        if kind in books:
            raise ValueError("ambiguous_source_books")
        sha = hashes[name]
        books[kind] = {
            "id": str(uuid5(NAMESPACE_URL, "towell-official-book:" + sha)),
            "source_name": name, "source_hash": sha,
            "reference": "BAASE" if kind == "closing" else "Towell/Cliente/Pedidos/Propuesta RS Dic Def",
        }
    if books.keys() != {"closing", "master"}:
        raise ValueError("two_source_books_required")
    return books


def _cached_bindings(prior, expected_sha, hashes, facts):
    manifest_path = prior / "Manifest.json"
    if file_hash(manifest_path) != expected_sha:
        raise ValueError("reconciliation_manifest_hash_mismatch")
    manifest = json_read(manifest_path)
    if manifest["input_hashes"] != hashes:
        raise ValueError("reconciliation_source_changed")
    for name, sha in manifest["results_sha256"].items():
        if Path(name).name != name or file_hash(prior / name) != sha:
            raise ValueError("reconciliation_artifact_changed")
    originals = {row["fila"]: row for row in facts}
    if len(originals) != len(facts):
        raise ValueError("source_row_id_duplicate")
    bindings, reasons = {}, defaultdict(set)
    for row in csv_rows(prior / "Comparacion.csv"):
        original = originals.get(row["fila"])
        if original is None or any(original[field] != row[field] for field in ROW_FIELDS):
            raise ValueError("reconciliation_trace_changed")
        selected = row["chain_id"], row["product_id"], row["period"]
        if row["fila"] in bindings and bindings[row["fila"]] != selected:
            raise ValueError("reconciliation_binding_conflict")
        bindings[row["fila"]] = selected
    for row in csv_rows(prior / "Excepciones.csv"):
        if not row["metric_code"]:
            reasons[row["fila"]].add(row["reason"])
    if set(bindings) & reasons.keys() or set(bindings) | reasons.keys() != originals.keys():
        raise ValueError("reconciliation_row_coverage_incomplete")
    return [
        (row, bindings[row["fila"]][2], bindings[row["fila"]][:2], [])
        if row["fila"] in bindings else (row, None, None, sorted(reasons[row["fila"]]))
        for row in facts
    ], manifest


def _master_bindings(path, expected_hash, snapshot):
    if file_hash(path) != expected_hash:
        raise ValueError("master_identity_file_changed")
    rows = json_read(path)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("master_identity_duplicate")
    live = {(row["chain_id"], row["id"]): row for row in snapshot["products"]}
    _, identifiers = catalog_index({**snapshot, "observations": []})
    for row in rows:
        if row["state"] != "COINCIDENCIA_VERIFICADA":
            continue
        target = live.get((row["chain_id"], row["product_id"]))
        possible = identifiers.get((row["chain_id"], row["barcode"]), {})
        if target is None or target["status"] != "ACTIVE" or set(possible) != {row["product_id"]}:
            raise ValueError("master_identity_catalog_changed")
    return rows


def _trace(source, row, cell, *, sheet="BAASE"):
    return {
        "source_id": source["id"], "source_file": source["source_name"], "source_sha256": source["source_hash"],
        "source_sheet": sheet, "source_row": str(row), "source_cell": cell,
        "source_reference": sheet + "!" + cell,
    }


def _exception(trace, row, reason, *, metric="", value="", period=None):
    return {**trace, "purchase_type": row.get("tipo_compra", "RS"), "chain": row["cadena"],
            "format": row["formato"], "upc": row["upc"], "period": period or row.get("periodo_etiqueta_rs", ""),
            "metric_code": metric, "value": value, "reason": reason}


def _catalog(facts, decisions, identities, books):
    bound = {}
    for row, _, ids, reasons in decisions:
        if ids and not reasons:
            k = (label(row["cadena"]), row["tipo_compra"], label(row["formato"]), row["upc"])
            if k in bound and bound[k] != ids:
                raise ValueError("source_catalog_binding_ambiguous")
            bound[k] = ids
    source_identities = {}
    products = {}

    def add(source, account, chain, fmt, purchase, upc, product_type, model, size, color, item, code, ids, reason, reference):
        business_key = [source["id"], account, label(chain), label(fmt), purchase, upc]
        source_key = fingerprint(business_key)
        entry = source_identities.setdefault(source_key, {
            "identity_id": source_key, "source_id": source["id"], "account": account,
            "chain": chain, "format": fmt, "purchase_type": purchase, "upc": upc,
            "chain_id": ids[0] if ids else None, "product_id": ids[1] if ids else None,
            "status": "VERIFIED" if ids else "EXCEPTION", "reason": reason,
            "attribute_variants": [], "references": [],
        })
        attributes = {"product_type": product_type, "model": model, "presentation": size,
                      "color": color, "item": item, "commercial_code": code}
        if attributes not in entry["attribute_variants"]:
            entry["attribute_variants"].append(attributes)
        if reference not in entry["references"]:
            entry["references"].append(reference)
        if ids:
            product_key = (*ids, purchase, label(fmt))
            product = products.setdefault(product_key, {
                "chain_id": ids[0], "product_id": ids[1], "purchase_type": purchase, "format": fmt,
                "upcs": set(), "source_identities": set(), "attribute_variants": [],
            })
            product["upcs"].add(upc)
            product["source_identities"].add(source_key)
            if attributes not in product["attribute_variants"]:
                product["attribute_variants"].append(attributes)

    for row in facts:
        k = (label(row["cadena"]), row["tipo_compra"], label(row["formato"]), row["upc"])
        add(books["closing"], "", row["cadena"], row["formato"], row["tipo_compra"], row["upc"],
            row["linea"], row["modelo"], row["tamano"], row["color"], row["item"], None, bound.get(k),
            "cached_upc_scope_binding" if k in bound else "source_scope_or_identity_requires_review", row["referencia_origen"])
    for row in identities:
        ids = (row["chain_id"], row["product_id"]) if row["state"] == "COINCIDENCIA_VERIFICADA" else None
        add(books["master"], row["account"], row["chain"], row["format"], "RS", row["barcode"], None, row["model"],
            row["size"], row["color"], None, row.get("commercial_code"), ids, row["reason"], row["id"])
    output_products = []
    for k, entry in sorted(products.items()):
        output_products.append({**entry, "upcs": sorted(entry["upcs"]), "source_identities": sorted(entry["source_identities"]),
                                "attribute_variants": sorted(entry["attribute_variants"], key=fingerprint)})
    for entry in source_identities.values():
        entry["attribute_variants"].sort(key=fingerprint)
        entry["references"].sort()
    return {"source_identities": sorted(source_identities.values(), key=lambda row: row["identity_id"]),
            "products": output_products}


def prepare_official_source(facts, references, plans, identities, decisions, books):
    sources = [books[kind] for kind in ("closing", "master")]
    source_hash = fingerprint({
        "schema": SCHEMA, "sources": sources, "master_bindings": identities,
        "attribute_contract": "SOURCE_NATIVE_COMMERCIAL_1",
        "closing_bindings": [[row["fila"], period, ids, reasons] for row, period, ids, reasons in decisions],
        "source_rows_hash": fingerprint(facts), "reference_rows_hash": fingerprint(references),
        "plan_rows_hash": fingerprint(plans),
    })
    master_targets = defaultdict(list)
    for entry in identities:
        if entry["state"] == "COINCIDENCIA_VERIFICADA":
            master_targets[entry["chain_id"], entry["product_id"]].append(entry)
    observations, inventory, ce, exceptions, missing, accepted = [], [], [], [], [], []
    seen = set()
    for row, period, ids, reasons in decisions:
        row_trace = _trace(books["closing"], row["fila"], "A" + row["fila"] + ":Z" + row["fila"])
        if reasons:
            exceptions.extend(_exception(row_trace, row, reason) for reason in reasons)
            continue
        if period < "2025-01" or period > CUTOFF or not ids or row["tipo_compra"] not in {"RS", "CE"}:
            raise ValueError("cached_source_binding_invalid")
        selected = {**row, "period": period, "chain_id": ids[0], "product_id": ids[1]}
        master = master_targets[ids]
        master_entry = master[0] if len(master) == 1 else {}
        for field, metric in METRICS.items():
            literal = row[field]
            trace = _trace(books["closing"], row["fila"], FIELD_COLUMNS[field] + row["fila"])
            if not literal.strip():
                missing.append(_exception(trace, row, "source_blank", metric=metric, period=period))
                continue
            try:
                value = exact_quantity(literal)
            except ValueError:
                exceptions.append(_exception(trace, row, "invalid_or_unrepresentable_quantity", metric=metric, value=literal, period=period))
                continue
            selected[metric] = Decimal(value)
            business_key = (*ids, row["tipo_compra"], label(row["formato"]), period, metric)
            if business_key in seen:
                raise ValueError("official_source_duplicate_key")
            seen.add(business_key)
            record = {
                "id": str(uuid5(NAMESPACE_URL, fingerprint([source_hash, trace["source_reference"], business_key, literal]))),
                "chain_id": ids[0], "product_id": ids[1], "purchase_type": row["tipo_compra"],
                "chain": row["cadena"], "format": row["formato"], "upc": row["upc"], "item": row["item"],
                "account": master_entry.get("account") if row["tipo_compra"] == "RS" else None,
                "commercial_code": master_entry.get("commercial_code") if row["tipo_compra"] == "RS" else None,
                "product_type": row["linea"], "model": row["modelo"], "presentation": row["tamano"], "color": row["color"],
                "source_product_type_cell": "E" + row["fila"], "source_model_cell": "F" + row["fila"],
                "source_presentation_cell": "K" + row["fila"], "source_color_cell": "J" + row["fila"],
                "master_identity_reference": master_entry.get("id") if row["tipo_compra"] == "RS" else None,
                "period": period, "metric_code": metric, "value": value, "source_value": literal,
                **trace, "available_at": None, "availability_source": "UNKNOWN",
            }
            if row["tipo_compra"] == "CE":
                ce.append(record)
            elif metric == "INVENTORY_AGGREGATE":
                inventory.append(record)
            else:
                observations.append(record)
        accepted.append(selected)
    key = lambda row: (row["chain_id"], row["product_id"], row["period"], row["metric_code"])
    observations.sort(key=key)
    inventory.sort(key=key)
    ce.sort(key=key)
    mapping = {row["id"]: row for row in identities}
    reference_rows = []
    for row in references:
        binding = mapping.get(row["cuenta"] + "|" + row["upc"])
        if binding is None:
            raise ValueError("master_reference_identity_missing")
        reference_rows.append({
            **row, "metric_code": SOURCES[row["hoja"]], "source_id": books["master"]["id"],
            "source_file": books["master"]["source_name"], "source_sha256": books["master"]["source_hash"],
            "identity_state": binding["state"], "chain_id": binding["chain_id"], "product_id": binding["product_id"],
            "operational": False, "role": "HISTORICAL_REFERENCE",
        })
    plan_rows = []
    for row in plans:
        binding = mapping.get(row["cuenta"] + "|" + row["upc"])
        if binding is None:
            raise ValueError("master_plan_identity_missing")
        plan_rows.append({**row, "source_id": books["master"]["id"], "source_file": books["master"]["source_name"],
                          "source_sha256": books["master"]["source_hash"], "identity_state": binding["state"],
                          "chain_id": binding["chain_id"], "product_id": binding["product_id"],
                          "operational": False, "role": "SAVED_COMMERCIAL_PROPOSAL"})
    catalog = _catalog(facts, decisions, identities, books)
    payload = {
        "schema": SCHEMA, "sources": sources, "first_period": min(row["period"] for row in observations) if observations else None,
        "last_period": max(row["period"] for row in observations) if observations else None,
        "observations": observations, "inventory": inventory,
    }
    payload["source_hash"] = source_hash
    stats = {
        "input_rows": len(facts), "accepted_source_rows": dict(Counter(row["tipo_compra"] for row in accepted)),
        "structural_exception_rows": dict(Counter(row["tipo_compra"] for row, _, _, reasons in decisions if reasons)),
        "facts_by_metric": dict(sorted(Counter(row["metric_code"] for row in observations).items())),
        "operational_facts": len(observations), "operational_inventory": len(inventory), "ce_records_outside_rs": len(ce),
        "source_identities": len(catalog["source_identities"]), "catalog_products_by_purchase_type": dict(Counter(row["purchase_type"] for row in catalog["products"])),
        "master_identities": len(identities), "master_identity_states": dict(Counter(row["state"] for row in identities)),
        "historical_reference_records": len(reference_rows), "saved_proposal_rows": len(plan_rows),
        "first_period": payload["first_period"], "last_period": payload["last_period"],
        "inherited_observations": 0, "operational_actuals_2024": 0,
        "missing_by_metric": dict(Counter(row["metric_code"] for row in missing)),
        "exceptions_by_reason": dict(sorted(Counter(row["reason"] for row in exceptions).items())),
    }
    with localcontext() as context:
        context.prec = 80
        coverage = summarize_rows(accepted)
        fendi = fendi_control(facts, plans, accepted)
    return {
        "import": payload, "catalog": catalog, "references": reference_rows, "plans": plan_rows,
        "ce": ce, "exceptions": exceptions, "missing": missing, "coverage": coverage,
        "fendi": fendi, "stats": stats,
    }


def run(package, snapshot_path, identities_path, identity_sha, prior, prior_sha, output):
    package, output = Path(package), Path(output)
    if output.resolve().is_relative_to(REPO) or output.exists():
        raise ValueError("new_private_output_required")
    hashes = verify_inputs(package)
    books = _book_sources(package, hashes)
    snapshot = json_read(snapshot_path)
    snapshot_hash = file_hash(snapshot_path)
    identity_manifest = json_read(Path(identities_path).parent / "Version_candidata.json")
    if identity_manifest["source_hash"] != books["master"]["source_hash"]:
        raise ValueError("master_reconciliation_source_changed")
    identities = _master_bindings(Path(identities_path), identity_sha, snapshot)
    facts = csv_rows(package / FACTS)
    decisions, prior_manifest = _cached_bindings(Path(prior), prior_sha, hashes, facts)
    if prior_manifest["baseline_snapshot_sha256"] != snapshot_hash:
        raise ValueError("reconciliation_catalog_snapshot_changed")
    prepared = prepare_official_source(facts, csv_rows(package / REFERENCE), csv_rows(package / PLAN),
                                       identities, decisions, books)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "Import.json", prepared["import"])
    write_csv(output / "Facts.csv", prepared["import"]["observations"], FACT_FIELDS)
    write_csv(output / "Inventory.csv", prepared["import"]["inventory"], FACT_FIELDS)
    write_csv(output / "CE_Outside_RS.csv", prepared["ce"], FACT_FIELDS)
    write_json(output / "Catalog.json", prepared["catalog"])
    for name, records in (("References.csv", prepared["references"]), ("Plans.csv", prepared["plans"])):
        write_csv(output / name, records, list(records[0]) if records else [])
    write_csv(output / "Exceptions.csv", prepared["exceptions"], EXCEPTION_FIELDS)
    write_csv(output / "Missing.csv", prepared["missing"], EXCEPTION_FIELDS)
    write_csv(output / "Coverage.csv", prepared["coverage"], (
        "period", "cadena", "formato", "tipo_compra", "rows",
        *(name for metric in METRICS.values() for name in (metric, metric + "_rows")),
        "fill_rate_paired_rows", "fill_rate",
    ))
    write_json(output / "Fendi.json", prepared["fendi"])
    output_hashes = {path.name: file_hash(path) for path in sorted(output.iterdir())}
    manifest = {
        "schema": SCHEMA, "source_id": "OS-" + prepared["import"]["source_hash"][:20],
        "source_hash": prepared["import"]["source_hash"], "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "PREPARED_LOCAL", "unit": "PIEZAS", "sources": prepared["import"]["sources"],
        "input_hashes": hashes, "cutoff": CUTOFF, "stats": prepared["stats"], "outputs_sha256": output_hashes,
        "reused_identity_file_sha256": identity_sha, "reused_reconciliation_manifest_sha256": prior_sha,
        "catalog_evidence_sha256": snapshot_hash, "procedure_sha256": file_hash(__file__),
        "policy": {
            "operational": "BAASE_RS_ONLY_2025_2026",
            "master": "HISTORICAL_AND_PROPOSAL_REFERENCES_WITH_ORIGINAL_SEMANTICS",
            "catalog": "ALL_SOURCE_IDENTITIES_WITH_EXISTING_VERIFIED_LINKS_AND_EXCEPTIONS",
            "inventory_grain": "CHAIN_FORMAT_PURCHASE_TYPE_PRODUCT_UPC_MONTH",
            "temporal_availability": "UNKNOWN", "data_origin": "TWO_BOOKS_ONLY",
        },
    }
    if verify_inputs(package) != hashes or file_hash(snapshot_path) != snapshot_hash:
        raise ValueError("input_changed_while_preparing")
    write_json(output / "Manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--identities", required=True, type=Path)
    parser.add_argument("--identities-sha256", required=True)
    parser.add_argument("--reconciliation", required=True, type=Path)
    parser.add_argument("--reconciliation-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = run(args.package, args.snapshot, args.identities, args.identities_sha256,
                   args.reconciliation, args.reconciliation_sha256, args.output)
    print(manifest["source_id"], manifest["stats"])


if __name__ == "__main__":
    main()
