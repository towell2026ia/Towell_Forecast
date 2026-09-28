"""Explicit read-only linked-project periods/fingerprint audit. Never applies SQL.

Uses the CLI's normal login; no credential extraction, user/profile mutation,
bootstrap, model run or historical repair. Private outputs stay under outputs/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.assistant_api.certified_bootstrap import verify_remote_facts  # noqa: E402

PROJECT = "bskoyqhbgrycpwhydnnr"
DATASET_SHA = "3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4"
LINEAGE_SHA = "b73bdddd15b7a6867e81c19a47856c5d3ff6b659020c7b343e246ecb3caf487f"
MIGRATION008_SHA = "4203cb1d5021a5225d6d2247e1b648e268875637887c4631ffe4331b7dccb195"


def query(sql):
    if (ROOT / "supabase/.temp/project-ref").read_text(encoding="utf-8").strip() != PROJECT:
        raise ValueError("linked_project_mismatch_STOP")
    # SQL contains no credentials; avoid shell composition and raw error logs.
    result = subprocess.run(["node", "node_modules/supabase/dist/supabase.js", "db", "query",
                             "--linked", "--project-ref", PROJECT, "--output", "json", sql],
                            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("read_only_cli_query_failed_STOP")
    payload = json.loads(result.stdout)
    return payload["rows"] if isinstance(payload, dict) else payload


def audit(args):
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / "outputs") or output.exists():
        raise ValueError("fresh_private_output_required")
    output.mkdir(parents=True)
    content = (ROOT / "supabase/migrations/202609280001_portal_published_history.sql").read_text(encoding="utf-8")
    if hashlib.sha256(content.encode()).hexdigest() != MIGRATION008_SHA:
        raise ValueError("migration008_changed_STOP")
    counts = query("""begin transaction read only;
select (select count(*) from public.monthly_observations) as observations,
 (select count(*) from public.monthly_observations where availability_source='UNKNOWN' and available_at is null) as unknown,
 (select count(*) from public.portal_monthly_observations_current) as published,
 (select count(distinct chain_id) from public.portal_monthly_observations_current) as scopes,
 (select count(distinct product_id) from public.portal_monthly_observations_current) as products,
 (select max(period)::text from public.portal_monthly_observations_current) as latest,
 (select count(*) from public.forecast_runs) as runs,
 (select count(*) from public.forecast_vintages) as vintages,
 (select count(*) from public.champion_registry) as champions,
 (select pg_get_viewdef('public.monthly_observations_current'::regclass,true)) as temporal_definition,
 (select pg_get_viewdef('public.portal_monthly_observations_current'::regclass,true)) as history_definition,
 (select jsonb_agg(version order by version) from supabase_migrations.schema_migrations) as migrations;
commit;""")[0]
    for field, expected in {"observations": 39270, "unknown": 39270, "published": 39270,
                            "scopes": 18, "products": 1010, "latest": "2026-07-01",
                            "runs": 0, "vintages": 0, "champions": 0}.items():
        if counts[field] != expected:
            raise ValueError("corpus_or_model_gate_failed_" + field)
    migrations = [f"20260925000{i}" for i in range(1, 8)] + ["202609280001"]
    if args.after:
        migrations += ["202609280002"]
    if counts["migrations"] != migrations:
        raise ValueError("unexpected_migrations_STOP")
    measurement = query(r"""begin transaction read only;
select count(*) as observations,encode(sha256(convert_to(string_agg(
 jsonb_build_object('scope',c.code,'product',p.product_code,'variant',p.variant_code,
 'period',o.period,'metric',o.metric_code,'value',o.value,'version',o.version_no,
 'available_at',o.available_at,'availability_source',o.availability_source,
 'canonical_fact',e.description::jsonb->'canonical_fact')::text,E'\n'
 order by c.code,p.product_code,p.variant_code,o.period,o.metric_code,o.version_no),'UTF8')),'hex') as business_lineage_sha
from public.monthly_observations o join public.products p on p.id=o.product_id
join public.chains c on c.id=o.chain_id join public.source_evidence e on e.id=o.availability_evidence_id
join public.import_batches ib on ib.id=o.source_batch_id join public.import_profile_versions pv on pv.id=ib.profile_version_id
where pv.mapping_json->>'bootstrap_id'='7dcd4781-f485-5479-83d5-1dd9edc29f06'; commit;""")[0]
    if measurement != {"observations": 39270, "business_lineage_sha": LINEAGE_SHA}:
        raise ValueError("historical_business_lineage_changed_STOP")
    result = {"phase": "after" if args.after else "before", "counts": counts,
              "measurement": measurement, "historical_writes": 0, "migration008_unchanged": True}
    if args.after:
        if not args.before_dir or not args.plan or not args.admin_user_id:
            raise ValueError("postflight_requires_before_plan_verified_admin")
        before = json.loads((args.before_dir / "RESULT.json").read_text(encoding="utf-8"))
        for field in ("temporal_definition", "history_definition"):
            if counts[field] != before["counts"][field]:
                raise ValueError("existing_view_changed_STOP")
        if before["measurement"] != measurement:
            raise ValueError("before_after_lineage_mismatch_STOP")
        import uuid
        admin = str(uuid.UUID(args.admin_user_id))
        if not query(f"begin transaction read only; select exists(select 1 from public.profiles where id='{admin}' and status='ACTIVE' and global_role='ADMIN') as valid; commit;")[0]["valid"]:
            raise ValueError("approved_admin_required_STOP")
        privileges = query("""begin transaction read only;
select has_table_privilege('anon','public.portal_history_periods','SELECT') as anon,
 has_table_privilege('authenticated','public.portal_history_periods','SELECT') as authenticated,
 has_table_privilege('authenticated','public.portal_history_periods','INSERT,UPDATE,DELETE') as writes,
 (select reloptions @> array['security_invoker=true'] from pg_class where oid='public.portal_history_periods'::regclass) as invoker,
 (select jsonb_agg(column_name order by ordinal_position) from information_schema.columns where table_schema='public' and table_name='portal_history_periods') as columns;
commit;""")[0]
        if privileges != {"anon": False, "authenticated": True, "writes": False, "invoker": True, "columns": ["chain_id", "period"]}:
            raise ValueError("periods_security_gate_failed_STOP")
        result["privileges"] = privileges
        result["periods"] = query(f"""begin transaction read only;
set local role authenticated; set local request.jwt.claim.sub='{admin}';
set local statement_timeout='8s';
select count(*) as scope_period_rows,count(distinct period) as global_months,
 array_agg(distinct period order by period) as visible_months,
 (select jsonb_build_object('chain_rows',count(*),'visible_months',array_agg(period order by period))
  from public.portal_history_periods where chain_id=(select c.id from public.chains c
   where regexp_replace(lower(c.name),'[^a-z]','','g')='alsuper'
   and exists(select 1 from public.portal_history_periods p where p.chain_id=c.id)
   order by c.code limit 1)) as chain
 from public.portal_history_periods;
commit;""")[0]
        if not 0 < result["periods"]["scope_period_rows"] < 1000 or not result["periods"]["chain"]["chain_rows"]:
            raise ValueError("catalog_cardinality_gate_failed_STOP")
        # Fresh private readback, unchanged verifier and exact original dataset SHA.
        remote = []
        for offset in range(0, 39270, 1000):
            remote.extend(query(f"""begin transaction read only;
select v.observation_id as id,v.chain_id,v.product_id,to_char(v.period,'YYYY-MM-DD') as period,
 v.metric_code,v.value::text as value,v.version_no,v.available_at,v.availability_source,
 v.source_batch_id,o.availability_evidence_id,v.chain_code as scope_code,
 v.product_code,v.variant_code,e.source_hash as evidence_source_hash,
 b.status as batch_status,e.description::jsonb as lineage
from public.portal_monthly_observations_current v
join public.monthly_observations o on o.id=v.observation_id
join public.source_evidence e on e.id=o.availability_evidence_id and e.chain_id=v.chain_id
join public.import_batches b on b.id=v.source_batch_id and b.chain_id=v.chain_id
order by v.observation_id limit 1000 offset {offset}; commit;"""))
            if len(remote) % 5000 == 0:
                print(json.dumps({"private_readback_progress": len(remote)}), flush=True)
        result["fingerprint"] = verify_remote_facts(remote, json.loads(args.plan.read_text(encoding="utf-8")))
        if result["fingerprint"]["dataset_sha"] != DATASET_SHA:
            raise ValueError("dataset_sha_changed_STOP")
    result["status"] = "PASS_READ_ONLY_PERIODS_AUDIT"
    (output / "RESULT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    # Only aggregate evidence in logs; no private rows, identifiers or raw error.
    print(json.dumps({"status": result["status"], "phase": result["phase"], "observations": counts["observations"],
                      "measurement": measurement, "periods": result.get("periods"), "dataset_sha": result.get("fingerprint", {}).get("dataset_sha")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--after", action="store_true")
    parser.add_argument("--before-dir", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--admin-user-id")
    try:
        audit(parser.parse_args())
    except Exception as error:
        # Never expose upstream SDK/CLI error content or a credential-bearing trace.
        safe = str(error) if isinstance(error, ValueError) and str(error).replace('_', '').isalnum() else type(error).__name__
        print("PERIODS_AUDIT_FAIL: " + safe, file=sys.stderr)
        raise SystemExit(1)
