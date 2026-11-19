"""Guarded local-only unified source installation using existing import contracts."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from services.operational_versions.official_source import AuthorizedOfficialConnection, COMMERCIAL_FIELDS, validate_package
from .unified_runtime import uid, norm, catalog_origin_batch


def build_payload(runtime, previous, installed_sources, *, source_periods):
    stock_source = [s for s in installed_sources if s["source_hash"] == runtime["source_context"]["fulfillment_file_sha256"]]
    if len(stock_source) != 1:
        raise ValueError("unified_installed_inventory_evidence_required")
    stock_source = stock_source[0]
    metadata = runtime["metadata"]
    by_product = {(r["chain_id"], r["product_id"]): r for r in previous["catalog"]}
    payload = {"schema": "official-source-1", "contract": "pedido-unificado-v1", "source_hash": runtime["source_hash"],
               "sources": [runtime["source"], stock_source], "first_period": "2024-01", "last_period": "2026-12",
               "observations": deepcopy(runtime["facts"]), "inventory": [], "fulfillment": [], "source_grid_periods": sorted(set(source_periods))}
    for row in previous["inventory"]:
        attrs = metadata.get(row["product_id"], by_product[row["chain_id"], row["product_id"]])
        attrs = {k: attrs.get(k) for k in COMMERCIAL_FIELDS}
        payload["inventory"].append({**row, **attrs, "id": uid("inventory:" + runtime["source_hash"] + ":" + row["chain_id"] + ":" + row["product_id"] + ":" + str(row["period"])),
            "value": row["quantity_text"], "period": str(row["period"])[:7], "source_id": stock_source["id"],
            "source_native_attributes": {k: row.get(k, by_product[row["chain_id"],row["product_id"]].get(k)) for k in COMMERCIAL_FIELDS}})
    for row in previous["fulfillment"]:
        attrs = metadata.get(row["product_id"], by_product[row["chain_id"],row["product_id"]])
        attrs = {k: attrs.get(k) for k in COMMERCIAL_FIELDS}
        payload["fulfillment"].append({**row, **attrs, "id": uid("fulfillment:" + runtime["source_hash"] + ":" + row["chain_id"] + ":" + row["product_id"] + ":" + str(row["period"])),
            "period": str(row["period"])[:7], "order_value": row["order_text"], "delivery_value": row["delivery_text"],
            "source_id": stock_source["id"]})
    validate_package(payload)
    return payload


class UnifiedLocalWriter(AuthorizedOfficialConnection):
    """Exact profile/product/account membership for explicitly local identities."""
    def install_scenario(self, c, scenario):
        """Append private provenance only; never write commercial close as ORDER."""
        from .unified_scenario import validate_scenario, canonical

        validate_scenario(scenario)
        if not self.local_rehearsal or scenario["source_hash"] != self.source_hash:
            raise ValueError("unified_scenario_local_source_required")
        self.maintenance(c, self.source_hash)
        if not c.execute("select private.is_active_admin() allowed").fetchone()["allowed"]:
            raise ValueError("unified_local_admin_required")
        header = {key: value for key, value in scenario.items() if key != "records"}
        entries = [("Planning_Scenario_Manifest", 1, header)] + [
            ("Planning_Scenario", row["source_row"], row) for row in scenario["records"]]
        expected = {}
        for sheet, row_number, value in entries:
            text = canonical(value)
            expected[sheet, row_number] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            c.execute("""insert into private.unified_source_rows(source_hash,sheet,source_row,payload,row_sha256)
                values(%s,%s,%s,%s::jsonb,%s) on conflict do nothing""",
                (self.source_hash, sheet, row_number, text, expected[sheet, row_number]))
        actual = {(r["sheet"], r["source_row"]): r["row_sha256"] for r in c.execute(
            """select sheet,source_row,row_sha256 from private.unified_source_rows
               where source_hash=%s and sheet in ('Planning_Scenario','Planning_Scenario_Manifest')""", (self.source_hash,))}
        if actual != expected:
            raise ValueError("unified_scenario_persisted_hash_mismatch")
        return {"scenario_hash": scenario["scenario_hash"], "rows": len(entries), "observed_facts_mutated": False}

    def _profiles(self, c, records, approved_profiles):
        if not self.local_rehearsal:
            raise ValueError("unified_local_profiles_only")
        mappings = {}
        for chain, profile_id in approved_profiles.items():
            row = c.execute("""select v.mapping_json from public.import_profile_versions v
                join public.import_profiles p on p.id=v.profile_id and p.chain_id=v.chain_id
                where v.id=%s and v.chain_id=%s and p.status='ACTIVE'""", (profile_id, chain)).fetchone()
            if row is None:
                raise ValueError("unified_profile_not_active")
            mappings[chain] = row["mapping_json"]
        active = {r["id"]: r for r in c.execute("select id::text,chain_id::text,product_code from public.products where status='ACTIVE'")}
        for row in records:
            profile = mappings.get(row["chain_id"])
            product = active.get(row["product_id"])
            if (profile is None or product is None or product["chain_id"] != row["chain_id"] or product["product_code"] != row["upc"]
                    or not any(p["id"] == row["product_id"] and p["product_code"] == row["upc"] for p in profile.get("products", []))):
                raise ValueError("unified_profile_product_identity_invalid")
            fmt = str(profile["scope"]["code"]).split("::")[-1]
            if norm(fmt) != norm(row["format"]):
                raise ValueError("unified_profile_format_invalid")

    def install_catalog(self, c, document, bindings, payload, snapshot):
        self.maintenance(c, payload["source_hash"])
        c.execute(Path(__file__).with_name("unified_local.sql").read_text(encoding="utf-8"))
        actor = c.execute("select auth.uid() actor").fetchone()["actor"]
        if actor is None or not c.execute("select private.is_active_admin() allowed").fetchone()["allowed"]:
            raise ValueError("unified_local_admin_required")
        for scope in bindings["new_scopes"]:
            c.execute("insert into public.chains(id,code,name,status) values(%s,%s,%s,'ACTIVE') on conflict(id) do nothing",
                      (scope["id"], scope["code"], scope["name"]))
        # Seed imported product provenance before their mandatory batch FK.
        grouped = defaultdict(list)
        for identity in bindings["identities"]:
            if identity["state"] not in {"EXISTING_UUID", "NEW_LOCAL_SOURCE_IDENTITY"}:
                continue
            grouped[identity["chain_id"]].append(identity)
        previous_profiles = {r["chain_id"]: r for r in snapshot["profiles"]}
        approved = {}
        for chain in sorted({r["chain_id"] for r in [*payload["observations"],*payload["inventory"],*payload["fulfillment"]]} | set(grouped)):
            profile = previous_profiles.get(chain)
            identities = grouped.get(chain, [])
            scope = identities[0]["scope"] if identities else profile["mapping_json"]["scope"]
            profile_id = uid("import-profile:" + chain)
            version_id = uid("import-profile-version:" + payload["source_hash"] + ":" + chain)
            approved[chain] = version_id
            c.execute("insert into public.import_profiles(id,chain_id,name,status) values(%s,%s,'Unified source local','ACTIVE') on conflict(id) do nothing",(profile_id,chain))
            mapped = {p["id"]: p for p in (profile["mapping_json"].get("products", []) if profile else [])}
            for identity in identities:
                raw = identity["raw"]
                mapped[identity["product_id"]] = {"id": identity["product_id"], "chain_id":chain,"product_code":raw["upc"],
                    "identifier_mappings":[[None,raw["upc"]]],"category_id":identity["category_id"],"account":raw["cuenta"],"format":raw["formato"],
                    "description":raw["descripcion"],"attributes":identity["attributes"],"source_identity_key":raw["clave"]}
            mapping = {"scope":scope,"products":list(mapped.values()),"contract":"pedido-unificado-v1","source_file_sha256":document["source"]["sha256"]}
            c.execute("""insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
                values(%s,%s,%s,(select coalesce(max(version),0)+1 from public.import_profile_versions where profile_id=%s),%s::jsonb,'[]'::jsonb,%s) on conflict(id) do nothing""",(version_id,profile_id,chain,profile_id,json.dumps(mapping,ensure_ascii=False),actor))
            bootstrap_batch = catalog_origin_batch(c, chain_id=chain, file_sha256=document["source"]["sha256"],
                candidate_id=uid("catalog-bootstrap-batch:" + payload["source_hash"] + ":" + chain),
                profile_version_id=version_id, actor=actor)
            for identity in identities:
                if identity["state"] != "NEW_LOCAL_SOURCE_IDENTITY":
                    continue
                raw = identity["raw"]
                c.execute("""insert into public.products(id,chain_id,category_id,product_code,variant_code,description,status,first_seen_period,last_seen_period,created_from_batch_id)
                    values(%s,%s,%s,%s,%s,%s,'ACTIVE','2024-01-01','2026-12-01',%s) on conflict(id) do nothing""",
                    (identity["product_id"],chain,identity["category_id"],raw["upc"],raw["cuenta"],raw["descripcion"],bootstrap_batch))
        for identity in bindings["identities"]:
            if identity["state"] not in {"EXISTING_UUID", "NEW_LOCAL_SOURCE_IDENTITY"}:
                continue
            attrs = {k:identity["attributes"].get(k) for k in COMMERCIAL_FIELDS}
            c.execute("""insert into private.unified_catalog(source_hash,chain_id,product_id,format,upc,attributes,source_identity_key,source_row)
                values(%s,%s,%s,%s,%s,%s::jsonb,%s,%s) on conflict do nothing""",
                (payload["source_hash"],identity["chain_id"],identity["product_id"],identity["raw"]["formato"],identity["raw"]["upc"],json.dumps(attrs),identity["identity_key"],identity["raw"]["source_row"]))
        rows = []
        for section in ("catalog", "rows", "methods", "definitives"):
            for row in document[section]:
                text = json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(",",":"))
                rows.append((payload["source_hash"],row["source_sheet"],row["source_row"],text,hashlib.sha256(text.encode()).hexdigest()))
        with c.cursor() as cur:
            cur.executemany("insert into private.unified_source_rows(source_hash,sheet,source_row,payload,row_sha256) values(%s,%s,%s,%s::jsonb,%s) on conflict do nothing",rows)
        # Retain all former validation, extending its range inventory with the
        # explicit monthly source grid (including valid future reference cells).
        body = c.execute("select pg_get_functiondef('private.official_validate_current()'::regprocedure) body").fetchone()["body"]
        marker = ") x;\n  if beginning is null"
        if "private.unified_source_rows u" not in body:
            if marker not in body:
                raise ValueError("unified_period_guard_shape_changed")
            body = body.replace(marker,"union all select ((u.payload->>'periodo')||'-01')::date from private.unified_source_rows u where u.source_hash=new.source_hash and u.sheet='Base_2024_2026'\n  " + marker)
            c.execute(body)
        return approved
