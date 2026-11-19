"""Source-native identities and normalized records for the unified local source.

No reconciliation by description. Existing UUIDs are retained only for an exact
scope/UPC with compatible commercial account. Newly authorized identities use a
stable UUIDv5 of account + format + UPC; source quantities remain untouched.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5

from .unified_source import CONTRACT, HISTORICAL_FIELDS


def norm(text):
    return re.sub(r"[\s_-]+", "", unicodedata.normalize("NFKC", str(text or "")).upper())


def uid(seed):
    return str(uuid5(NAMESPACE_URL, "towell-unified:" + seed))


def commercial_attributes(row, existing=None):
    """Apply only approved generic prefixes and presentation equivalences."""
    original = row["modelo"]
    tokens = original.split()
    product_type = None
    if tokens and tokens[0].upper() in {"TOALLA", "TRAPO", "TAPETE", "BATA", "BATAS"}:
        product_type = "BATA" if tokens[0].upper() == "BATAS" else tokens[0].upper()
        tokens = tokens[1:]
    # The exact source presentation is also present in the model prefix.
    presentation = row.get("presentacion")
    equivalents = {"MB": "MEDIO BAÑO", "MEDIO BAÑO": "MEDIO BAÑO", "BAÑO": "BAÑO", "BANO": "BAÑO",
                   "PULL": "PULLMAN", "PULLMAN": "PULLMAN", "FACIAL": "FACIAL"}
    if tokens and presentation and norm(tokens[0]) == norm(presentation) and presentation.upper() in equivalents:
        tokens = tokens[1:]
    model = " ".join(tokens) or original
    return {"account": row["cuenta"], "format": row["formato"], "upc": row["upc"],
            "model": model, "presentation": equivalents.get(str(presentation).upper(), presentation),
            "color": row.get("color"), "commercial_code": row.get("cod_ax"),
            "product_type": product_type or (existing or {}).get("product_type"),
            "source_native_attributes": {"account": row["cuenta"], "format": row["formato"], "upc": row["upc"],
                "model": original, "presentation": presentation, "color": row.get("color"), "commercial_code": row.get("cod_ax")}}


def bind_catalog(document, snapshot, previous):
    profiles = snapshot["profiles"]
    current = {(r["chain_id"], r["product_id"]): r for r in previous["catalog"]}
    products = snapshot["products"]
    by_scope_upc = defaultdict(list)
    for product in products:
        if product["status"] == "ACTIVE":
            by_scope_upc[product["chain_id"], product["product_code"]].append(product)
    scopes = {r["chain_id"]: r["mapping_json"]["scope"] for r in profiles
              if r["mapping_json"]["scope"].get("level") == "COMMERCIAL_UNIT"}
    scope_formats = defaultdict(list)
    for chain, scope in scopes.items():
        scope_formats[norm(scope["code"].split("::")[-1])].append(chain)
    # Parent identity is accredited by existing account metadata. A format
    # label alone is insufficient ("Hiper" can occur in unrelated chains).
    # An identical legal-client label inside this single source can carry the
    # already known parent to another account, never a fuzzy name match.
    direct, account_parent, client_parent = {}, defaultdict(set), defaultdict(set)
    for record in previous["catalog"]:
        scope = scopes.get(record["chain_id"])
        if scope and record.get("account"):
            account_parent[record["account"]].add(scope["parent_uuid"])
    for row in document["catalog"]:
        known = account_parent[row["cuenta"]]
        if len(known) == 1:
            client_parent[row["cliente"]].update(known)
    for row in document["catalog"]:
        parents = account_parent[row["cuenta"]] or client_parent[row["cliente"]]
        choices = [chain for chain in scope_formats[norm(row["formato"])]
                   if len(parents) == 1 and scopes[chain]["parent_uuid"] in parents]
        if len(choices) == 1:
            direct[row["clave"]] = choices[0]
    new_scopes, identities, exceptions = {}, [], []
    for row in document["catalog"]:
        chain = direct.get(row["clave"])
        scope = scopes.get(chain)
        if not chain:
            parents = account_parent[row["cuenta"]] or client_parent[row["cliente"]]
            if len(parents) != 1:
                identities.append({"identity_key": row["clave"], "state": "PENDING_SCOPE", "raw": row})
                exceptions.append({"identity_key": row["clave"], "reason": "NO_UNIQUE_ACCREDITED_PARENT_SCOPE"})
                continue
            parent = next(iter(parents))
            chain = uid("scope:" + parent + ":" + row["formato"])
            parent_name = next(r["name"] for r in snapshot["chains"] if r["id"] == parent)
            code = parent_name + "::" + row["formato"]
            scope = {"code": code, "level": "COMMERCIAL_UNIT", "status": "ACTIVE", "scope_id": "CU-UNIFIED-" + chain,
                     "sql_code": "UNIFIED_" + chain.replace("-", "").upper(), "chain_uuid": chain,
                     "parent_uuid": parent, "parent_id": next(s["parent_id"] for s in scopes.values() if s["parent_uuid"] == parent),
                     "parent_code": parent_name, "scope_type": "COMMERCIAL_UNIT", "actual_bearing": True,
                     "aggregation_policy": "SINGLE_SCOPE_ONLY_NO_AUTOMATIC_ROLLUP"}
            new_scopes[chain] = {"id": chain, "code": scope["sql_code"], "name": code, "status": "ACTIVE", "scope": scope,
                                 "source_account": row["cuenta"], "source_client": row["cliente"]}
        candidates = by_scope_upc[chain, row["upc"]]
        source_peers = [r for r in document["catalog"] if direct.get(r["clave"]) == chain and r["upc"] == row["upc"]]
        compatible = [p for p in candidates if current.get((chain, p["id"]), {}).get("account") == row["cuenta"]]
        if not compatible and len(candidates) == 1 and len(source_peers) <= 1:
            known_account = current.get((chain, candidates[0]["id"]), {}).get("account")
            if known_account is None:
                compatible = candidates
        if len(compatible) > 1:
            identities.append({"identity_key": row["clave"], "state": "AMBIGUOUS", "raw": row})
            exceptions.append({"identity_key": row["clave"], "reason": "MULTIPLE_COMPATIBLE_EXISTING_UUIDS"})
            continue
        selected = compatible[0] if compatible else None
        pid = selected["id"] if selected else uid("product:" + row["clave"])
        attrs = commercial_attributes(row, current.get((chain, pid)))
        identity = {"identity_key": row["clave"], "state": "EXISTING_UUID" if selected else "NEW_LOCAL_SOURCE_IDENTITY",
                    "chain_id": chain, "product_id": pid, "category_id": selected.get("category_id") if selected else None,
                    "scope": scope, "attributes": attrs, "raw": row}
        identities.append(identity)
    targets = defaultdict(list)
    for identity in identities:
        if "product_id" in identity:
            targets[identity["chain_id"], identity["product_id"]].append(identity)
    for group in targets.values():
        if len(group) > 1:
            for identity in group:
                identity["state"] = "AMBIGUOUS"
                exceptions.append({"identity_key": identity["identity_key"], "reason": "DIFFERENT_SOURCE_IDENTITIES_SHARE_PHYSICAL_TARGET"})
    return {"identities": identities, "new_scopes": list(new_scopes.values()), "exceptions": exceptions,
            "counts": dict(Counter(r["state"] for r in identities))}


def normalized_records(document, bindings, *, generation=5):
    source_hash = hashlib.sha256(json.dumps({"contract": CONTRACT, "file_sha256": document["source"]["sha256"],
                "last_closed_period": document["closure"]["last_closed_period"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    source = {"id": uid("source:" + source_hash), "source_hash": document["source"]["sha256"],
              "source_name": document["source"]["name"], "reference": "Unified workbook; Base_2024_2026 literal monthly ORDER and separate real sales; commercial references are not adopted Champions"}
    by_key = {r["identity_key"]: r for r in bindings["identities"] if r["state"] in {"EXISTING_UUID", "NEW_LOCAL_SOURCE_IDENTITY"}}
    facts, engine, metadata = [], [], {}
    metric_map = dict(zip(HISTORICAL_FIELDS, ("ORDER", "SALES", "SALES_CLIENTE"), strict=True))
    for row in document["rows"]:
        identity = by_key.get(row["clave"])
        if identity is None:
            continue
        attrs = deepcopy(identity["attributes"])
        pid = identity["product_id"]
        metadata[pid] = {**attrs, "identity_key": row["clave"], "chain_id": identity["chain_id"], "product_id": pid,
                         "category_id": identity["category_id"], "has_history": identity["raw"]["tiene_historico"]}
        for field, metric in metric_map.items():
            if row[field] is None:
                continue
            ref = row["lineage"][field]["cell"]
            fact = {**attrs, "id": uid("fact:" + source_hash + ":" + row["clave"] + ":" + row["periodo"] + ":" + metric),
                    "chain_id": identity["chain_id"], "product_id": pid, "period": row["periodo"], "metric_code": metric,
                    "value": row[field], "source_id": source["id"], "source_reference": ref,
                    "source_sheet": "Base_2024_2026", "source_file_sha256": document["source"]["sha256"],
                    "purchase_type": "RS", "available_at": None, "availability_source": "UNKNOWN",
                    "training_eligible": metric == "ORDER" and row["apto_entrenamiento_pedido"],
                    "original_reference": row.get({"ORDER": "pedido_celda", "SALES": "venta_towell_celda", "SALES_CLIENTE": "venta_cliente_celda"}[metric]),
                    "canonical_attribute_source": {"contract": CONTRACT, "master_sha256": document["source"]["sha256"],
                                                   "identity": [row["cuenta"], row["formato"], row["upc"]]}}
            facts.append(fact)
            if fact["training_eligible"]:
                engine.append({"chain_id": identity["chain_id"], "product_id": pid, "product_code": row["upc"],
                    "description": attrs["model"], "category": attrs["product_type"] or "UNCLASSIFIED", "variant": attrs["color"],
                    "period": row["periodo"], "objective": "Pedido", "value": row[field], "source_id": fact["id"],
                    "available_at": None, "availability_source": "UNKNOWN", "evidence_mode": "RETROSPECTIVE_TRAINING",
                    "source_file_sha256": document["source"]["sha256"], "target_sheet": "Base_2024_2026",
                    "metric_code": "ORDER", "training_eligible": True, "source_reference": ref})
    context = {"source_hash": source_hash, "authorized_source_hash": source_hash, "generation": generation,
               "forecast_contract": CONTRACT, "objective": "Pedido", "target_metric": "ORDER", "target_sheet": "Base_2024_2026",
               "target_file_sha256": document["source"]["sha256"], "unit": "PIEZAS", "training_cutoff": "2026-09",
               "target_source_available": True, "forecast_available": True, "forecast_unavailable_reason": None,
               "forecast_block_code": None, "first_period": "2024-01", "last_period": "2026-12"}
    return {"schema": "unified-runtime-1", "source_hash": source_hash, "source": source,
            "source_context": context, "facts": facts, "engine_records": engine, "metadata": metadata}
