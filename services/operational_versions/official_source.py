"""Read a normalized official package and rehearse its current-only SQL import."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from .local import METRICS, REPO, LocalLab, digest, quantity

MONTH = re.compile(r"20\d\d-(0[1-9]|1[0-2])\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMERCIAL_FIELDS = ("model", "presentation", "color", "product_type", "commercial_code", "account")


def _attributes(row):
    attributes = {name: row.get(name) for name in COMMERCIAL_FIELDS}
    if any(value is not None and not isinstance(value, str) for value in attributes.values()):
        raise ValueError("official_commercial_attributes_invalid")
    return attributes


def _attribute_references(row):
    return {name: value for name,value in row.items()
            if (name.startswith("source_") and name.endswith("_cell") or name=="master_identity_reference")
            and isinstance(value,str)}


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("official_identity_invalid")
    return value


def validate_package(payload):
    if (not isinstance(payload, dict) or payload.get("schema") != "official-source-1"
            or not isinstance(payload.get("source_hash"), str) or not SHA256.fullmatch(payload["source_hash"])
            or not MONTH.fullmatch(payload["first_period"]) or not MONTH.fullmatch(payload["last_period"])
            or payload["first_period"] > payload["last_period"]):
        raise ValueError("official_package_invalid")
    sources = payload["sources"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("official_sources_invalid")
    source_ids, source_hashes = set(), set()
    for source in sources:
        sid = _uuid(source["id"])
        if (sid in source_ids or not SHA256.fullmatch(source["source_hash"])
                or source["source_hash"] in source_hashes
                or not isinstance(source["source_name"], str) or not source["source_name"].strip()
                or not isinstance(source["reference"], str) or not source["reference"].strip()):
            raise ValueError("official_sources_invalid")
        source_ids.add(sid)
        source_hashes.add(source["source_hash"])
    ids, periods = set(), []
    for section in ("observations", "inventory"):
        records = payload[section]
        if not isinstance(records, list):
            raise ValueError("official_records_invalid")
        keys = set()
        for row in records:
            identity = (_uuid(row["chain_id"]), _uuid(row["product_id"]), row["period"])
            oid, source = _uuid(row["id"]), _uuid(row["source_id"])
            metric = row.get("metric_code") if section == "observations" else "INVENTORY"
            if (oid in ids or (*identity, metric) in keys or source not in source_ids
                    or not MONTH.fullmatch(row["period"]) or row["purchase_type"] != "RS"
                    or not isinstance(row["format"], str) or not row["format"].strip()
                    or not isinstance(row["upc"], str) or not re.fullmatch(r"[0-9]+", row["upc"])
                    or not isinstance(row["source_reference"], str) or not row["source_reference"].strip()
                    or section == "observations" and metric not in METRICS):
                raise ValueError("official_record_identity_invalid")
            if not isinstance(row["value"], str) or quantity(row["value"]) is None:
                raise ValueError("official_value_invalid")
            _attributes(row)
            ids.add(oid)
            keys.add((*identity, metric))
            periods.append(row["period"])
    if not periods or min(periods) != payload["first_period"] or max(periods) != payload["last_period"]:
        raise ValueError("official_periods_invalid")
    return payload


def read_package(path, approved_sha256):
    raw = Path(path).read_bytes()
    if not isinstance(approved_sha256, str) or hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("official_package_sha_mismatch")
    return validate_package(json.loads(raw))


class OfficialSourceLab(LocalLab):
    def _official(self):
        if not self.config["database"].startswith("gateb_official_"):
            raise ValueError("OFFICIAL_LAB_REQUIRED")

    def install(self, source_hash):
        self._official()
        proposals = list((REPO / "lab/official_source/supabase/migrations").glob("*.sql"))
        if len(proposals) != 1:
            raise ValueError("official_sql_proposal_missing")
        with self.connect() as c, c.transaction():
            self.maintenance(c, source_hash)
            c.execute(proposals[0].read_text(encoding="utf-8"))
            c.execute((REPO / "lab/official_source/cutover/02_result_source_guards.sql").read_text(encoding="utf-8"))

    def maintenance(self, c, source_hash):
        self.checked(c)
        self._official()
        if not isinstance(source_hash, str) or not SHA256.fullmatch(source_hash):
            raise ValueError("official_source_hash_invalid")
        c.execute("""select set_config('towell.official_database',%s,false),
                            set_config('towell.official_source_hash',%s,false)""",
                  (self.config["database"], source_hash))

    def _profiles(self, c, records, approved_profiles):
        profile_rows = c.execute("""select v.id::text,v.chain_id::text,v.mapping_json
            from public.import_profile_versions v join public.import_profiles p
              on p.id=v.profile_id and p.chain_id=v.chain_id where p.status='ACTIVE'""").fetchall()
        active_products = {r["id"] for r in c.execute("select id::text from public.products where status='ACTIVE'")}
        mapped = defaultdict(set)
        profiles = {r["id"]: r for r in profile_rows}
        for profile in profile_rows:
            for product in profile["mapping_json"].get("products", []):
                if product["id"] in active_products:
                    for _, upc in product.get("identifier_mappings", []):
                        if upc:
                            mapped[profile["chain_id"], str(upc).strip()].add(product["id"])
        for row in records:
            profile = profiles.get(approved_profiles.get(row["chain_id"]))
            if (profile is None or profile["chain_id"] != row["chain_id"]
                    or mapped[row["chain_id"], row["upc"]] != {row["product_id"]}):
                raise ValueError("official_catalog_mapping_mismatch")
            scope = profile["mapping_json"].get("scope", {})
            parent, separator, fmt = str(scope.get("code", "")).partition("::")
            canonical = lambda s: re.sub(r"[\s_-]+", "", s).upper()
            if (scope.get("level") != "COMMERCIAL_UNIT" or not scope.get("actual_bearing")
                    or canonical(fmt if separator else parent) != canonical(row["format"])):
                raise ValueError("official_catalog_format_mismatch")

    def load(self, c, payload, approved_profiles, *, expected_generation, request_id):
        """Import both accredited sources and replace the singleton atomically."""
        self.checked(c)
        self._official()
        payload = validate_package(payload)
        source_hash = payload["source_hash"]
        with c.transaction():
            self.maintenance(c, source_hash)
            c.execute("select pg_advisory_xact_lock(hashtextextended('official-current-source',0))")
            current = c.execute("select source_hash,generation from public.official_source_current where singleton for update").fetchone()
            observed_generation = current["generation"] if current else 0
            if type(expected_generation) is not int or observed_generation != expected_generation:
                raise ValueError("official_source_generation_conflict")
            if current and current["source_hash"] == source_hash:
                return {"source_hash": source_hash, "generation": observed_generation, "unchanged": True}
            c.execute("lock table public.monthly_observations,public.products,public.import_batches,public.source_evidence in share row exclusive mode")
            c.execute("select set_config('towell.official_expected_generation',%s,true)", (str(expected_generation),))
            records = [*payload["observations"], *payload["inventory"]]
            self._profiles(c, records, approved_profiles)
            actor = c.execute("select auth.uid() actor").fetchone()["actor"]
            if actor is None or not c.execute("select private.is_active_admin() allowed").fetchone()["allowed"]:
                raise ValueError("official_import_admin_required")
            for source in payload["sources"]:
                existing = c.execute("select source_hash,source_name,description from public.source_evidence where id=%s",
                                     (source["id"],)).fetchone()
                expected = (source["source_hash"], source["source_name"], source["reference"])
                if existing:
                    if tuple(existing.values()) != expected:
                        raise ValueError("official_source_evidence_conflict")
                else:
                    c.execute("""insert into public.source_evidence(id,evidence_level,evidence_type,source_name,source_hash,description)
                        values(%s,'UNKNOWN','OFFICIAL_COMMERCIAL_WORKBOOK',%s,%s,%s)""",
                        (source["id"], source["source_name"], source["source_hash"], source["reference"]))
            grouped = defaultdict(list)
            for row in records:
                grouped[row["source_id"], row["chain_id"], row["period"]].append(row)
            batches = {}
            for batch_key, selected in sorted(grouped.items()):
                source_id, chain, period = batch_key
                batch = str(uuid5(NAMESPACE_URL, "official-batch:" + source_hash + ":" + "/".join(batch_key)))
                batches[batch_key] = batch
                c.execute("""insert into public.import_batches
                    (id,chain_id,profile_version_id,filename,sha256,period,uploaded_by,evidence_id,official_source_hash)
                    values(%s,%s,%s,'official-source-import.json',%s,%s,%s,%s,%s)""",
                    (batch, chain, approved_profiles[chain], digest(selected), period + "-01", actor, source_id, source_hash))
                c.execute("update public.import_batches set status='VALIDATING',row_count=%s,valid_rows=%s where id=%s",
                          (len(selected), len(selected), batch))
                for state in ("VALIDATED", "CONFIRMED", "IMPORTED"):
                    c.execute("update public.import_batches set status=%s where id=%s", (state, batch))
            physical = {
                (r["chain_id"], r["product_id"], r["period"], r["metric_code"]): r["last_version"]
                for r in c.execute("""select chain_id::text,product_id::text,to_char(period,'YYYY-MM') period,
                    metric_code,max(version_no) last_version from public.monthly_observations
                    group by chain_id,product_id,period,metric_code""")
            }
            insertions = []
            for row in payload["observations"]:
                identity = row["chain_id"], row["product_id"], row["period"], row["metric_code"]
                previous = physical.get(identity, 0)
                insertions.append((row["id"], row["chain_id"], row["product_id"], row["period"] + "-01", row["metric_code"],
                                   row["value"], previous + 1, batches[row["source_id"], row["chain_id"], row["period"]]))
            with c.cursor() as cursor:
                cursor.executemany("""insert into public.monthly_observations
                    (id,chain_id,product_id,period,metric_code,value,version_no,source_batch_id,availability_source)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,'UNKNOWN')""", insertions)
                cursor.executemany("""insert into private.official_observation_source
                    (observation_id,format,upc,purchase_type,source_reference,value_literal,attributes,attribute_references)
                    values(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                    [(r["id"], r["format"], r["upc"], r["purchase_type"], r["source_reference"], r["value"],
                      json.dumps(_attributes(r)),json.dumps(_attribute_references(r)))
                     for r in payload["observations"]])
                cursor.executemany("""insert into public.monthly_inventory
                    (id,chain_id,product_id,period,format,upc,purchase_type,quantity,value_literal,source_reference,source_batch_id,attributes,attribute_references)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                    [(r["id"], r["chain_id"], r["product_id"], r["period"] + "-01", r["format"], r["upc"], r["purchase_type"],
                      r["value"], r["value"], r["source_reference"], batches[r["source_id"], r["chain_id"], r["period"]],
                      json.dumps(_attributes(r)),json.dumps(_attribute_references(r)))
                     for r in payload["inventory"]])
            generation = expected_generation + 1
            values = (source_hash, generation, payload["first_period"] + "-01", payload["last_period"] + "-01",
                      [s["id"] for s in payload["sources"]])
            if current:
                c.execute("""update public.official_source_current set source_hash=%s,generation=%s,first_period=%s,
                    last_period=%s,evidence_ids=%s::uuid[],activated_at=clock_timestamp() where singleton""", values)
            else:
                c.execute("""insert into public.official_source_current
                    (singleton,source_hash,generation,first_period,last_period,evidence_ids)
                    values(true,%s,%s,%s,%s,%s::uuid[])""", values)
            c.execute("""insert into public.audit_log(actor_id,action,entity_type,entity_id,new_data,request_id)
                values(%s,'OFFICIAL_SOURCE_REPLACED','official_source',%s,%s::jsonb,%s)""",
                (actor, str(uuid5(NAMESPACE_URL, "towell-official-source-singleton")),
                 json.dumps({"source_hash": source_hash, "generation": generation,
                                   "observation_count": len(payload["observations"]), "inventory_count": len(payload["inventory"])}),
                 request_id))
            return {"source_hash": source_hash, "generation": generation}


class AuthorizedOfficialConnection:
    """Apply the tested writer through an owner connection supplied by the operator.

    This class never opens a connection. The operator supplies an approved
    endpoint, source fingerprint and generation after project/TLS verification.
    """
    _profiles = OfficialSourceLab._profiles

    def __init__(self, connection, *, expected_host, expected_database, expected_user,
                 project_ref, source_hash, expected_generation, authorization, local_rehearsal=False):
        required = f"ACTIVATE_OFFICIAL_SOURCE:{source_hash}:{expected_generation}"
        if (authorization != required or not isinstance(source_hash, str) or not SHA256.fullmatch(source_hash)
                or type(expected_generation) is not int or expected_generation < 0):
            raise ValueError("official_activation_authorization_required")
        if local_rehearsal:
            if expected_host != "127.0.0.1" or not expected_database.startswith("gateb_official_"):
                raise ValueError("OFFICIAL_LAB_REQUIRED")
        elif (project_ref != "bskoyqhbgrycpwhydnnr" or not (
            expected_host == f"db.{project_ref}.supabase.co" or
            expected_host.endswith(".pooler.supabase.com") and expected_user.endswith("." + project_ref)
        )):
            raise ValueError("official_project_target_mismatch")
        self.connection = connection
        self.target = expected_host, expected_database, expected_user
        self.config = {"database": expected_database}
        self.source_hash, self.expected_generation = source_hash, expected_generation
        self.local_rehearsal = local_rehearsal
        self.checked(connection)
        if not local_rehearsal:
            tls = connection.execute("select ssl from pg_stat_ssl where pid=pg_backend_pid()").fetchone()
            if not tls or not tls["ssl"] or connection.info.get_parameters().get("sslmode") != "verify-full":
                raise ValueError("official_tls_required")

    def checked(self, c):
        if c is not self.connection or c.closed or (c.info.host, c.info.dbname, c.info.user) != self.target:
            raise ValueError("official_connection_target_mismatch")
        owner = c.execute("""select session_user=pg_get_userbyid(relowner) allowed
            from pg_class where oid='public.monthly_observations'::regclass""").fetchone()
        if not owner or not owner["allowed"]:
            raise ValueError("official_owner_connection_required")

    def _official(self):
        self.checked(self.connection)

    def maintenance(self, c, source_hash):
        self.checked(c)
        if source_hash != self.source_hash:
            raise ValueError("official_source_hash_mismatch")
        c.execute("""select set_config('towell.official_database',%s,false),
                    set_config('towell.official_source_hash',%s,false)""", (self.config["database"], source_hash))

    def load(self, c, payload, approved_profiles, *, expected_generation, request_id):
        if expected_generation != self.expected_generation:
            raise ValueError("official_activation_generation_mismatch")
        return OfficialSourceLab.load(self, c, payload, approved_profiles,
                                      expected_generation=expected_generation, request_id=request_id)
