"""Rehearse an archive's exact IDs and membership in the marked loopback lab."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json
from uuid import NAMESPACE_URL, uuid5

from services.operational_update.unification import catalog_index, identity as commercial_identity

from .cutover_archive import key
from .local import digest


def utc_instant(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.utcoffset() is None:
        raise ValueError("archive_availability_requires_offset")
    return parsed


def compare_membership(c, version, expected):
    actual = {}
    for row in c.execute("""
        select o.chain_id::text,o.product_id::text,to_char(o.period,'YYYY-MM') period,o.metric_code,
          o.id::text observation_id,o.version_no,o.value,o.availability_source,o.available_at
        from operational_data_version_observations m join monthly_observations o on o.id=m.observation_id
        where m.version_id=%s
    """, (version,)):
        actual[key(row)] = row
    if actual.keys() != expected.keys():
        raise ValueError("archive_membership_drift")
    for identity, row in expected.items():
        got = actual[identity]
        if (got["observation_id"] != row["observation_id"] or got["version_no"] != int(row["version_no"])
                or got["value"] != Decimal(row["value"]) or got["availability_source"] != row["availability_source"]
                or got["available_at"] != utc_instant(row["available_at"])):
            raise ValueError("archive_observation_drift")


def verify_archive_target(lab, c, archive):
    lab.checked(c)
    if not lab.config["database"].startswith("gateb_unify_"):
        raise ValueError("STOP_UNIFICATION_LAB")
    actual = c.execute("select current_setting('towell.cutover_manifest_sha256',true) as sha").fetchone()["sha"]
    if actual != archive.sha256:
        raise ValueError("maintenance_archive_sha_mismatch")
    products = {p["id"]: p["chain_id"] for p in archive.catalog["products"]}
    chains = {r["id"] for r in archive.catalog["chains"]}
    current_products = c.execute("select id::text,chain_id::text,product_code,variant_code,description,status from products").fetchall()
    current_chains = c.execute("select id::text,code,name,status,timezone from chains").fetchall()
    for saved, live, fields in (
        (archive.catalog["products"], current_products, ("chain_id", "product_code", "variant_code", "description", "status")),
        (archive.catalog["chains"], current_chains, ("code", "name", "status", "timezone")),
    ):
        by_id = {r["id"]: r for r in live}
        if by_id.keys() != {r["id"] for r in saved} or any(
            by_id[r["id"]][field] != r[field] for r in saved for field in fields if field in r
        ):
            raise ValueError("archive_catalog_identity_drift")
    profiles = c.execute("select chain_id::text,mapping_json from import_profile_versions").fetchall()
    scopes, identifiers = catalog_index({"products": current_products, "profiles": profiles, "observations": []})
    for row in [*archive.changes.values(), *archive.inventory]:
        found, problem = commercial_identity(row, scopes, identifiers)
        if problem or found != (row["chain_id"], row["product_id"]):
            raise ValueError("archive_commercial_mapping_drift")
    return products, chains


def initialize_baseline(lab, c, archive, baseline_id, request_id):
    """Seal A against the existing imported selection and the approved archive."""
    lab.checked(c)
    with c.transaction():
        pointer = c.execute("select version_id,generation from operational_data_active where singleton for update").fetchone()
        if not pointer or pointer["version_id"] is not None or pointer["generation"] != 0:
            raise ValueError("baseline_already_initialized")
        c.execute("lock table monthly_observations,products,chains,import_batches in share row exclusive mode")
        products, chains = verify_archive_target(lab, c, archive)
        document = {
            "chains": sorted(chains), "products": sorted(products), "absences": [], "exclusions": [],
            "policy": "unification-rs-baseline", "archive_manifest_sha256": archive.sha256,
        }
        c.execute("insert into operational_data_versions(id,document) values(%s,%s::jsonb)",
                  (baseline_id, json.dumps(document)))
        c.execute("""
            insert into operational_data_version_observations
            select %s,m.chain_id,m.product_id,m.period,m.metric_code,m.id from (
              select distinct on(o.chain_id,o.product_id,o.period,o.metric_code) o.*
              from monthly_observations o join import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
              where b.status='IMPORTED'
              order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc
            ) m
        """, (baseline_id,))
        compare_membership(c, baseline_id, archive.baseline)
        seal = lab.seal(c, baseline_id)
        return lab.activate(c, baseline_id, seal, None, 0, request_id)


def build_batches(c, archive, profile_versions, actor):
    grouped = defaultdict(list)
    for identity, row in archive.changes.items():
        grouped[identity[0], identity[2]].append(row)
    batches = {}
    for (chain, period), rows in sorted(grouped.items()):
        profile_version = profile_versions.get(chain)
        if not profile_version or not c.execute("""
            select 1 from import_profile_versions v join import_profiles p
            on p.id=v.profile_id and p.chain_id=v.chain_id
            where v.id=%s and v.chain_id=%s and p.status='ACTIVE'
        """, (profile_version, chain)).fetchone():
            raise ValueError("archive_import_profile_missing")
        batch = str(uuid5(NAMESPACE_URL, f"{archive.version_id}/{chain}/{period}"))
        source_sha = digest(sorted((r["observation_id"], r["value"], r["referencia_origen"]) for r in rows))
        c.execute("""
            insert into import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by)
            values(%s,%s,%s,%s,%s,%s,%s)
        """, (batch, chain, profile_version, archive.candidate_id + ".csv", source_sha, period + "-01", actor))
        c.execute("update import_batches set status='VALIDATING',row_count=%s,valid_rows=%s where id=%s",
                  (len(rows), len(rows), batch))
        for state in ("VALIDATED", "CONFIRMED", "IMPORTED"):
            c.execute("update import_batches set status=%s where id=%s", (state, batch))
        batches[chain, period] = batch
    return batches


def promote_archive(lab, c, context, archive, profile_versions, request_id):
    """A B import can commit only with its seal and CAS activation in one transaction."""
    lab.checked(c)
    with c.transaction():
        lab.lock_active(c, context)
        c.execute("lock table monthly_observations,products,chains,import_batches,import_profile_versions,source_evidence in share row exclusive mode")
        compare_membership(c, context.version_id, archive.baseline)
        products, chains = verify_archive_target(lab, c, archive)
        actor = c.execute("select auth.uid() as actor").fetchone()["actor"]
        batches = build_batches(c, archive, profile_versions, actor)
        physical = {(r["chain_id"], r["product_id"], r["period"], r["metric_code"]): r["last_version"]
                    for r in c.execute("""
                        select chain_id::text,product_id::text,to_char(period,'YYYY-MM') period,
                          metric_code,max(version_no) last_version
                        from monthly_observations group by chain_id,product_id,period,metric_code
                    """)}
        insertions = []
        for identity in archive.changes:
            row = archive.candidate[identity]
            version = int(row["version_no"])
            if version != physical.get(identity, 0) + 1:
                raise ValueError("archive_physical_version_drift")
            chain, product, period, metric = identity
            insertions.append((row["observation_id"], chain, product, period + "-01", metric,
                               row["value"], version, batches[chain, period]))
        with c.cursor() as cursor:
            cursor.executemany("""
                insert into monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,
                  availability_source,source_batch_id)
                values(%s,%s,%s,%s,%s,%s,%s,'UNKNOWN',%s)
            """, insertions)
            cursor.executemany("""
                insert into private.operational_source_lineage
                  (observation_id,archive_manifest_sha256,source_reference,value_literal)
                values(%s,%s,%s,%s)
            """, [(r["observation_id"], archive.sha256, r["referencia_origen"], r["value"])
                   for r in archive.changes.values()])
        document = {
            "chains": sorted(chains), "products": sorted(products), "absences": [],
            "exclusions": [{"archive_manifest_sha256": archive.sha256}],
            "policy": "unification-rs-operational", "contract": "unification-1",
            "archive_candidate_id": archive.candidate_id, "archive_manifest_sha256": archive.sha256,
        }
        version_id = archive.version_id
        c.execute("insert into operational_data_versions(id,base_version_id,document) values(%s,%s,%s::jsonb)",
                  (version_id, context.version_id, json.dumps(document)))
        members = c.execute("""
            insert into operational_data_version_observations
            select %s,chain_id,product_id,period,metric_code,id from monthly_observations where id=any(%s::uuid[])
        """, (version_id, [r["observation_id"] for r in archive.candidate.values()]))
        if members.rowcount != len(archive.candidate):
            raise ValueError("archive_member_id_not_found")
        with c.cursor() as cursor:
            cursor.executemany("""
                insert into operational_data_version_inventory
                  (version_id,chain_id,product_id,period,format,upc,purchase_type,quantity,source_reference)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, [(version_id, r["chain_id"], r["product_id"], r["period"] + "-01", r["formato"],
                   r["upc"], r["tipo_compra"], r["value"], r["referencia_origen"]) for r in archive.inventory])
        compare_membership(c, version_id, archive.candidate)
        seal = lab.seal(c, version_id)
        return lab.activate(c, version_id, seal, context.version_id, context.generation, request_id)
