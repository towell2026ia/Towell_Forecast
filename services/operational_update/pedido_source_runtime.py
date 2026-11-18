"""Prepare captured Pedidos for local consultation; an unknown close blocks forecasts.

The existing accredited identity map and immutable G2 SALES/stock are reused.
No SQL, model call, new identity, conversion, or fallback lives in this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from .official_source import FACT_FIELDS, _master_bindings, exact_quantity
from .pedidos_order_source import read_pedidos_order_source
from .preparation import REPO, file_hash, fingerprint
from .towell_sales_source import serializable
from .towell_source_runtime import EXTRA_FIELDS, IDENTITY_SHA, _attributes, _identity_key
from .unification import catalog_index, json_read, write_csv, write_json

CONTRACT = "pedido-towell-v1"
POLICY = "FT-FIX-PEDIDO-03"
GENERATION = 3
BLOCK_CODE = "ORDER_CLOSURE_UNVERIFIED"
BLOCK_REASON = "Sin cálculo: cierre de Pedido no acreditado"
ATTRIBUTE_FIELDS = ("account", "format", "upc", "commercial_code", "model", "presentation", "color", "product_type")


def sealed_g2(directory, manifest_sha256):
    """Read only the exact, fully sealed G2 contract; do not query a newer source."""
    directory = Path(directory)
    if file_hash(directory / "Manifest.json") != manifest_sha256:
        raise ValueError("g2_manifest_changed")
    manifest = json_read(directory / "Manifest.json")
    if (manifest.get("generation") != 2 or manifest.get("forecast_source_context", {}).get("sales_contract") != "towell-sales-v1"):
        raise ValueError("accredited_g2_sales_required")
    for name, digest in manifest["outputs_sha256"].items():
        if Path(name).name != name or file_hash(directory / name) != digest:
            raise ValueError("g2_output_changed")
    payload = json_read(directory / "Import.json")
    if payload["source_hash"] != manifest["source_hash"]:
        raise ValueError("g2_source_changed")
    if any(row["metric_code"] != "SALES" or row.get("source_sheet") != "Towell"
           or not re.fullmatch(r"Towell![KLM]\d+", row.get("source_reference", ""))
           for row in payload["observations"]):
        raise ValueError("g2_sales_provenance_required")
    if any(row.get("source_sheet") != "BAASE"
           or not re.fullmatch(r"BAASE!Y\d+", row.get("source_reference", ""))
           for row in payload["inventory"]):
        raise ValueError("g2_inventory_provenance_required")
    return manifest, payload


def prepare_packet(document, bindings, snapshot, g2_payload, *, rules, fulfillment_rows=None, fulfillment_source=None):
    """Prepare only captured ORDER, informative SALES and independent stock.

    `eligible` remains a training attribute. `capture_eligible` permits importing
    a valid observed historical quantity without fabricating a closed month.
    """
    source, closure = document["source"], document["closure"]
    if (document.get("schema") != "pedidos-order-source-1" or document.get("contract") != POLICY
            or source.get("sheet") != "Pedidos" or source.get("metric_code") != "ORDER"
            or source.get("unit") != "PIEZAS" or not source.get("unchanged_after_read")
            or closure.get("forecast_available") is not bool(closure.get("last_closed_period"))
            or closure.get("forecast_block_code") != (None if closure.get("last_closed_period") else BLOCK_CODE)):
        raise ValueError("pedidos_capture_contract_required")
    closed = closure.get("last_closed_period")
    if closed and (not isinstance(closed, str) or not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", closed)
                   or not isinstance(closure.get("evidence"), dict)):
        raise ValueError("pedidos_explicit_closure_required")
    prior_sources = g2_payload["sources"]
    master_sources = [s for s in prior_sources if s["source_hash"] == source["sha256"]]
    if len(prior_sources) != 2 or len(master_sources) != 1:
        raise ValueError("same_accredited_master_required_for_informative_sales")
    closure_seed = ":" + fingerprint(closure) if closed else ""
    master = {"id": str(uuid5(NAMESPACE_URL, "towell-source-contract:" + CONTRACT + ":" + source["sha256"] + closure_seed)),
              "source_name": source["name"], "source_hash": source["sha256"],
              "reference": "Pedidos!K:M ORDER captured, " + ("confirmed closed through " + closed if closed else "closure unknown")
                  + "; Towell!K:M SALES informative; " + POLICY}
    prior_stock = next(s for s in prior_sources if s["id"] != master_sources[0]["id"])
    stock = deepcopy(prior_stock)
    if (fulfillment_rows is None) != (fulfillment_source is None):
        raise ValueError("independent_fulfillment_contract_incomplete")
    if fulfillment_source is not None:
        if (not closed or fulfillment_source.get("source_hash") != prior_stock["source_hash"]
                or fulfillment_source.get("source_name") != prior_stock["source_name"]):
            raise ValueError("independent_fulfillment_source_invalid")
        stock = deepcopy(fulfillment_source)
    sources = [master, stock]
    bound, targets = defaultdict(list), defaultdict(list)
    for binding in bindings:
        bound[_identity_key(binding)].append(binding)
    identities, exceptions, references = {}, deepcopy(document.get("exceptions", [])), []
    for original in document["identities"]:
        matches = bound[_identity_key(original)]
        selected = matches[0] if (len(matches) == 1 and matches[0]["state"] == "COINCIDENCIA_VERIFICADA"
            and _chain_key(matches[0]["chain"]) == _chain_key(original.get("chain_name_original"))) else None
        identity = {"identity_key": original["identity_key"], "raw": original,
            "state": "COINCIDENCIA_VERIFICADA" if selected else "REVISION_REQUERIDA",
            "reason": None if selected else "NO_UNIQUE_ACCREDITED_ACCOUNT_FORMAT_UPC_BINDING"}
        if selected:
            identity.update(chain_id=selected["chain_id"], product_id=selected["product_id"],
                            attributes=_attributes(original, rules), binding_reference=selected["id"])
            targets[selected["chain_id"], selected["product_id"]].append(identity)
        identities[original["identity_key"]] = identity
    for matches in targets.values():
        if len(matches) > 1:
            for identity in matches:
                identity.update(state="AMBIGUO", reason="PRIVATE_IDENTITIES_SHARE_TARGET")
    facts = []
    for row in document["records"]:
        identity = identities.get(row["identity_key"])
        if not row.get("capture_eligible") or not identity or identity["state"] != "COINCIDENCIA_VERIFICADA":
            references.append({**row, "binding_state": identity["state"] if identity else "UNRESOLVED"})
            continue
        if (row["metric_code"] != "ORDER" or row["sheet"] != "Pedidos"
                or not re.fullmatch(r"Pedidos![KLM]\d+", row["source_reference"])
                or row.get("available_at") is not None or row.get("has_formula") or row["value_text"] is None
                or row.get("eligible") is not bool(closed and row["period"] <= closed)):
            raise ValueError("captured_pedidos_provenance_invalid")
        try:
            amount = exact_quantity(row["value_text"])
        except ValueError:
            exceptions.append({"identity_key": row["identity_key"], "reason": "EXACT_QUANTITY_OUTSIDE_EXISTING_IMPORT_CONTRACT",
                "source_reference": row["source_reference"], "source_value_literal": row["source_value_literal"]})
            continue
        facts.append({**identity["attributes"], "chain_id": identity["chain_id"], "product_id": identity["product_id"],
            "chain": identity["raw"].get("chain_name_original"), "purchase_type": "RS", "item": None,
            "period": row["period"], "metric_code": "ORDER", "value": amount,
            "source_value": row["source_value_literal"], "source_raw_value_literal": row["source_value_literal"],
            "source_id": master["id"], "source_file": source["name"], "source_sha256": source["sha256"],
            "source_sheet": "Pedidos", "source_row": str(row["row"]), "source_cell": row["column"] + str(row["row"]),
            "source_reference": row["source_reference"], "available_at": "", "availability_source": "UNKNOWN",
            "source_model_cell": "AE" + str(row["row"]), "source_presentation_cell": "AF" + str(row["row"]),
            "source_color_cell": "AH" + str(row["row"]), "source_product_type_cell": "AE" + str(row["row"]),
            "master_identity_reference": identity["binding_reference"]})
    counts = Counter((r["chain_id"], r["product_id"], r["period"], r["metric_code"]) for r in facts)
    unique = []
    for row in facts:
        if counts[row["chain_id"], row["product_id"], row["period"], row["metric_code"]] != 1:
            exceptions.append({"reason": "DUPLICATE_ORDER_DESTINATION", "source_reference": row["source_reference"]})
        else:
            unique.append(row)
    facts = unique
    if not facts:
        raise ValueError("no_accredited_captured_orders")
    canonical = {(r["chain_id"], r["product_id"]): r["attributes"] for r in identities.values()
                 if r["state"] == "COINCIDENCIA_VERIFICADA"}
    _, identifiers = catalog_index(snapshot)
    informative, inventory = [], []
    for section, dest in (("observations", informative), ("inventory", inventory)):
        for previous in g2_payload[section]:
            row = deepcopy(previous)
            metric = "SALES" if section == "observations" else "INVENTORY_AGGREGATE"
            sheet, ref = ("Towell", r"Towell![KLM]\d+") if section == "observations" else ("BAASE", r"BAASE!Y\d+")
            if (row.get("metric_code") != metric or row.get("source_sheet") != sheet
                    or not re.fullmatch(ref, row.get("source_reference", ""))
                    or row.get("purchase_type") != "RS"
                    or row.get("source_id") != (master_sources[0]["id"] if section == "observations" else prior_stock["id"])):
                raise ValueError("informative_g2_provenance_invalid")
            if set(identifiers.get((row["chain_id"], row["upc"]), {})) != {row["product_id"]}:
                raise ValueError("informative_g2_identity_changed")
            amount = exact_quantity(row["value"])
            if amount != row["value"]:
                raise ValueError("informative_g2_quantity_changed")
            own = canonical.get((row["chain_id"], row["product_id"]))
            if own:
                row.update(own)
                row["source_native_attributes"] = {k: previous.get(k) for k in ATTRIBUTE_FIELDS if k not in {"upc", "format"}}
                row["canonical_attribute_source"] = {"contract": CONTRACT, "master_sha256": source["sha256"],
                    "identity": [own.get(k) for k in ("account", "format", "upc")]}
            row["source_id"] = master["id"] if section == "observations" else stock["id"]
            dest.append(row)
    fulfillment = deepcopy(fulfillment_rows or [])
    for row in fulfillment:
        if (row.get("source_id") != stock["id"] or row.get("source_sha256") != stock["source_hash"]
                or row.get("purchase_type") != "RS"
                or not re.fullmatch(r"BAASE!W\d+", row.get("order_source_reference", ""))
                or not re.fullmatch(r"BAASE!X\d+", row.get("delivery_source_reference", ""))
                or set(identifiers.get((row["chain_id"], row["upc"]), {})) != {row["product_id"]}):
            raise ValueError("independent_fulfillment_row_invalid")
        for name in ("order_value", "delivery_value"):
            if row.get(name) is not None:
                exact_quantity(row[name])
    policy = {"contract": POLICY, "target_source": "PEDIDOS_KLM_ONLY", "target_metric": "ORDER",
              "informative_sales_source": "ACCREDITED_G2_TOWELL_KLM", "inventory_source": "STATISTICA_BAASE_Y_ONLY",
              "last_closed_period": closed, "training_cutoff": closed, "forecast_available": bool(closed),
              "forecast_block_code": None if closed else BLOCK_CODE, "forecast_unavailable_reason": None if closed else BLOCK_REASON,
              "unit": "PIEZAS", "no_old_forecast_reuse": True,
              "identity_criterion": "ACCREDITED_ACCOUNT_FORMAT_UPC_BINDING"}
    if fulfillment_source is not None:
        policy["fill_rate_source"] = "STATISTICA_BAASE_WX_INDEPENDENT_PAIR_ONLY"
        policy["fill_rate_never_training_target"] = True
    source_hash = fingerprint({"contract": CONTRACT, "sources": sources, "policy": policy,
        "facts": facts, "informative_sales": informative, "inventory": inventory,
        "identities": list(identities.values()), "g2_source_hash": g2_payload["source_hash"],
        **({"fulfillment": fulfillment} if fulfillment_source is not None else {})})
    for rows in (facts, informative, inventory):
        for row in rows:
            row["id"] = str(uuid5(NAMESPACE_URL, fingerprint([source_hash, row["source_reference"], row["chain_id"],
                row["product_id"], row["period"], row.get("metric_code"), row["value"]])))
    for row in fulfillment:
        row["id"] = str(uuid5(NAMESPACE_URL, fingerprint([source_hash, "INDEPENDENT_FULFILLMENT", row["chain_id"],
            row["product_id"], row["period"], row["source_reference"], row.get("order_value"), row.get("delivery_value")])))
    periods = [r["period"] for r in facts + informative + inventory]
    payload = {"schema": "official-source-1", "source_hash": source_hash, "sources": sources,
        "first_period": min(periods), "last_period": max(periods), "observations": facts + informative, "inventory": inventory}
    if fulfillment_source is not None:
        payload["fulfillment"] = fulfillment
    context = {"forecast_contract": CONTRACT, "objective": "Pedido", "target_metric": "ORDER", "target_sheet": "Pedidos",
        "target_file_sha256": source["sha256"], "training_cutoff": closed, "unit": "PIEZAS", "inventory_source": "ESTADISTICA",
        "authorized_source_hash": source_hash, "forecast_available": bool(closed), "forecast_unavailable_reason": None if closed else BLOCK_REASON,
        "forecast_block_code": None if closed else BLOCK_CODE, "target_source_available": True}
    if fulfillment_source is not None:
        context.update(fulfillment_source="ESTADISTICA_BAASE", fulfillment_file_sha256=stock["source_hash"], fulfillment_available=True)
    return {"import": payload, "catalog": list(identities.values()), "exceptions": exceptions,
        "references": references, "policy": policy, "forecast_source_context": context,
        "summary": {"reader_identities": len(identities), "binding_states": dict(Counter(r["state"] for r in identities.values())),
            "order_observations": len(facts), "order_products": len({(r["chain_id"], r["product_id"]) for r in facts}),
            "informative_sales_observations": len(informative), "inventory_observations": len(inventory),
            "inventory_products": len({(r["chain_id"], r["product_id"]) for r in inventory}),
            "first_period": min(periods), "last_period_with_quantity": max(periods), "cutoff": closed,
            "order_training_observations": sum(r["period"] <= closed for r in facts) if closed else 0,
            "order_partial_observations": sum(r["period"] > closed for r in facts) if closed else len(facts),
            **({"independent_fulfillment_rows": len(fulfillment)} if fulfillment_source is not None else {}),
            "forecast_available": bool(closed), "forecast_block_code": None if closed else BLOCK_CODE}}


def _chain_key(value):
    # Same normalization already used by the accredited source preparation.
    from services.assistant_api.commercial_catalog import key
    return key(value)


def prepare_source(*, workspace, master_sha256, bindings_path, bindings_sha256,
                   snapshot_path, snapshot_sha256, g2_directory, g2_manifest_sha256, output,
                   confirmed_closed_period=None, closure_evidence=None, generation=GENERATION, fulfillment_file=None):
    output = Path(output)
    if output.exists() or output.resolve().is_relative_to(REPO):
        raise ValueError("new_private_output_required")
    if type(generation) is not int or generation < GENERATION:
        raise ValueError("pedidos_generation_invalid")
    document = read_pedidos_order_source(Path(workspace), expected_sha256=master_sha256,
        confirmed_closed_period=confirmed_closed_period, closure_evidence=closure_evidence)
    if file_hash(snapshot_path) != snapshot_sha256:
        raise ValueError("catalog_snapshot_changed")
    if bindings_sha256 != IDENTITY_SHA:
        raise ValueError("accredited_identity_source_invalid")
    snapshot = json_read(snapshot_path)
    bindings = _master_bindings(Path(bindings_path), bindings_sha256, snapshot)
    g2_manifest, g2_payload = sealed_g2(g2_directory, g2_manifest_sha256)
    fulfillment, fulfillment_source, fulfillment_exceptions = None, None, []
    if fulfillment_file is not None:
        from .fulfillment_source import read_fulfillment_source
        stock_hash = next(s["source_hash"] for s in g2_payload["sources"] if s["source_hash"] != master_sha256)
        from services.forecast_engine.engine import add_month
        fulfillment, fulfillment_source, fulfillment_exceptions = read_fulfillment_source(fulfillment_file,
            stock_hash, snapshot, partial_from=add_month(confirmed_closed_period, 1), observed_through=g2_payload["last_period"])
    prepared = prepare_packet(document, bindings, snapshot, g2_payload,
        rules=json_read(REPO / "config/commercial_catalog_rules.json"),
        fulfillment_rows=fulfillment, fulfillment_source=fulfillment_source)
    prepared["exceptions"].extend(fulfillment_exceptions)
    payload = prepared["import"]
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "Import.json", payload)
    columns = [*FACT_FIELDS, *EXTRA_FIELDS]
    write_csv(output / "Facts.csv", payload["observations"], columns)
    write_csv(output / "Inventory.csv", payload["inventory"], columns)
    if "fulfillment" in payload:
        write_csv(output / "Fulfillment.csv", payload["fulfillment"], list(payload["fulfillment"][0]) if payload["fulfillment"] else [])
    for name, value in (("Catalog.json", prepared["catalog"]), ("Exceptions.json", prepared["exceptions"]),
                        ("References.json", serializable(prepared["references"])), ("Reader_Audit_Pedidos.json", serializable(document)),
                        ("Summary.json", prepared["summary"])):
        write_json(output / name, value)
    write_json(output / "Closure_Evidence.json", closure_evidence if confirmed_closed_period else {
        "sheet": "Pedidos", "metric_code": "ORDER", "last_closed_period": None, "confirmed_at": None,
        "state": "UNVERIFIED", "available_at": None,
        "statement": "El cierre de Pedidos aún no está acreditado",
        "authority": "LATEST_PROJECT_OWNER_USER_INSTRUCTION",
        "supersedes": "Earlier source closure interpretations, including PRD3A.9; no earlier month inferred."})
    write_json(output / "Informative_Sales_G2_Seal.json", {"source_hash": g2_manifest["source_hash"],
        "generation": 2, "manifest_sha256": g2_manifest_sha256,
        "import_sha256": g2_manifest["outputs_sha256"]["Import.json"],
        "sales_observations": prepared["summary"]["informative_sales_observations"],
        "policy": "Exact quantities and source cells preserved; never a training target or fallback for Pedido."})
    authorized_policy = {"contract": CONTRACT, "master_path": document["source"]["path"],
        "master_sha256": master_sha256, "source_hash": payload["source_hash"], "generation": generation,
        "sheet": "Pedidos", "unit": "PIEZAS", "last_closed_period": confirmed_closed_period}
    if fulfillment_source is not None:
        authorized_policy.update(fulfillment_source="ESTADISTICA_BAASE",
            fulfillment_file_sha256=fulfillment_source["source_hash"], fulfillment_available=True)
    write_json(output / "Authorized_Sales_Policy.json", authorized_policy)
    manifest = {"schema": "official-source-1", "source_hash": payload["source_hash"], "generation": generation,
        "created_at": datetime.now(timezone.utc).isoformat(), "first_period": payload["first_period"],
        "source_last_period": payload["last_period"], "cutoff": confirmed_closed_period,
        "training_cutoff": confirmed_closed_period, "unit": "PIEZAS",
        "sources": payload["sources"], "policy": prepared["policy"], "forecast_source_context": prepared["forecast_source_context"],
        "input_hashes": {"master": master_sha256, "bindings": bindings_sha256, "catalog": snapshot_sha256,
            "g2_manifest": g2_manifest_sha256, "g2_import": g2_manifest["outputs_sha256"]["Import.json"],
            "commercial_rules": file_hash(REPO / "config/commercial_catalog_rules.json")},
        "outputs_sha256": {p.name: file_hash(p) for p in sorted(output.iterdir())}}
    write_json(output / "Manifest.json", manifest)
    return manifest, prepared["summary"]
