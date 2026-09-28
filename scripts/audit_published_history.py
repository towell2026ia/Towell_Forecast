"""Explicit linked-project read-only audit; private artifacts, never runtime.

No bootstrap, model execution, mutation, token extraction, or temporal repair.
Use the normally authenticated CLI profile through SUPABASE_PROFILE.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.execute_certified_bootstrap import LinkedCLI, private_output  # noqa: E402
from services.assistant_api.certified_bootstrap import verify_remote_facts  # noqa: E402
from services.assistant_api.bootstrap_sql import measurement_sql  # noqa: E402

PROJECT = "bskoyqhbgrycpwhydnnr"
EXPECTED_SHA = "3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4"


def save(output, name, data):
    (output / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def final_checks(plan_path, output, before_dir):
    """Compact remote security/immutability proof, no synthetic remote writes."""
    output = private_output(output)
    cli = LinkedCLI(PROJECT, output)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    before = json.loads((before_dir / "counts.json").read_text(encoding="utf-8"))
    measurement = cli.query(measurement_sql(plan))[0]
    if measurement != json.loads((before_dir / "measurement.json").read_text(encoding="utf-8")):
        raise ValueError("historical_business_or_lineage_changed_STOP")
    result = cli.query("""begin transaction read only;
select pg_get_viewdef('public.monthly_observations_current'::regclass,true) as temporal_definition,
 has_table_privilege('anon','public.portal_monthly_observations_current','SELECT') as anon_select,
 has_table_privilege('authenticated','public.portal_monthly_observations_current','SELECT') as authenticated_select,
 has_table_privilege('authenticated','public.portal_monthly_observations_current','INSERT,UPDATE,DELETE') as authenticated_write,
 (select reloptions @> array['security_invoker=true'] from pg_class
 where oid='public.portal_monthly_observations_current'::regclass) as security_invoker,
 (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relkind='r' and c.relrowsecurity) as rls_tables,
 (select count(*) from public.forecast_runs) as runs,
 (select count(*) from public.forecast_vintages) as vintages,
 (select count(*) from public.champion_registry) as champions,
 (select jsonb_agg(version order by version) from supabase_migrations.schema_migrations) as migrations;
commit;""")[0]
    if result["temporal_definition"] != before["temporal_definition"]:
        raise ValueError("temporal_view_changed_STOP")
    for key, expected in {"anon_select": False, "authenticated_select": True,
                          "authenticated_write": False, "security_invoker": True,
                          "rls_tables": 28, "runs": 0, "vintages": 0, "champions": 0}.items():
        if result[key] != expected:
            raise ValueError("security_or_model_gate_failed_" + key)
    if result["migrations"] != [f"20260925000{i}" for i in range(1, 8)] + ["202609280001"]:
        raise ValueError("unexpected_migration_history_STOP")
    save(output, "security_immutability.json", result)
    save(output, "measurement.json", measurement)
    print(json.dumps({"status": "PASS_READ_ONLY_SECURITY_IMMUTABILITY", **{k: v for k, v in result.items() if k != "temporal_definition"}}))


def run(plan_path, output, after=False):
    output = private_output(output)
    cli = LinkedCLI(PROJECT, output)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    counts = cli.query("""begin transaction read only;
select (select count(*) from public.monthly_observations) as observations,
 (select count(*) from public.monthly_observations where available_at is null) as null_available_at,
 (select count(*) from public.monthly_observations_current) as temporal_current,
 (select count(*) from public.monthly_observations o join public.import_batches b
 on b.id=o.source_batch_id and b.chain_id=o.chain_id where b.status='IMPORTED') as imported,
 (select count(*) from public.categories) as categories,
 (select count(distinct p.category_id) from public.monthly_observations o
 join public.products p on p.id=o.product_id and p.chain_id=o.chain_id) as fact_categories,
 (select pg_get_viewdef('public.monthly_observations_current'::regclass,true)) as temporal_definition,
 (select count(*) from public.forecast_runs) as runs,
 (select count(*) from public.forecast_vintages) as vintages,
 (select count(*) from public.champion_registry) as champions;
commit;""")[0]
    save(output, "counts.json", counts)
    if any(counts[k] != v for k, v in {
        "observations": 39270, "null_available_at": 39270,
        "temporal_current": 0, "imported": 39270, "categories": 50,
    }.items()):
        raise ValueError("remote_gate_mismatch_STOP")
    save(output, "measurement.json", cli.query(measurement_sql(plan))[0])
    if not after:
        print(json.dumps({"status": "PASS_READ_ONLY_PREFLIGHT", **{k: v for k, v in counts.items() if k != "temporal_definition"}}))
        return
    admin = "47242c4b-79c9-453f-8548-0b9781c35012"
    summary = cli.query(f"""begin transaction read only;
set local role authenticated;
select set_config('request.jwt.claim.sub','{admin}',true);
select count(*) as observations,count(distinct chain_id) as scopes,
 count(distinct product_id) as products,count(distinct category_id) as fact_categories,
 (select count(*) from public.categories) as categories,
 count(*) filter(where metric_code='SALES') as sales,
 count(*) filter(where metric_code='ORDER') as orders,
 count(*) filter(where metric_code='DELIVERY') as deliveries,
 count(*) filter(where availability_source='UNKNOWN') as unknown,
 count(*) filter(where available_at is null) as null_available_at,
 max(period)::text as latest_period
from public.portal_monthly_observations_current;
commit;""")[-1]
    save(output, "admin_summary.json", summary)
    for key, n in {"observations": 39270, "scopes": 18, "products": 1010,
                   "categories": 50, "sales": 13153, "orders": 13099,
                   "deliveries": 13018, "unknown": 39270, "null_available_at": 39270}.items():
        if summary[key] != n:
            raise ValueError("portal_count_mismatch_STOP_" + key)
    # Read from the NEW view, enriching only in this private administrative audit.
    # The browser never receives evidence descriptions or the complete corpus.
    remote = []
    for offset in range(0, 39270, 1000):
        remote.extend(cli.query(f"""begin transaction read only;
select v.observation_id as id,v.chain_id,v.product_id,to_char(v.period,'YYYY-MM-DD') as period,
 v.metric_code,v.value::text as value,v.version_no,v.available_at,v.availability_source,
 v.source_batch_id,o.availability_evidence_id,v.chain_code as scope_code,
 v.product_code,v.variant_code,e.source_hash as evidence_source_hash,
 b.status as batch_status,e.description::jsonb as lineage
from public.portal_monthly_observations_current v
join public.monthly_observations o on o.id=v.observation_id
join public.source_evidence e on e.id=o.availability_evidence_id and e.chain_id=v.chain_id
join public.import_batches b on b.id=v.source_batch_id and b.chain_id=v.chain_id
order by v.observation_id limit 1000 offset {offset};
commit;"""))
        print(json.dumps({"private_readback": len(remote)}), flush=True)
    fingerprint = verify_remote_facts(remote, plan)
    if fingerprint["dataset_sha"] != EXPECTED_SHA:
        raise ValueError("portal_fingerprint_mismatch_STOP")
    save(output, "fingerprint.json", fingerprint)
    save(output, "joined_view_facts.json", remote)
    save(output, "RESULT.json", {"status": "PASS_REMOTE_PUBLISHED_VIEW", "summary": summary,
                                 "fingerprint": fingerprint, "historical_writes": 0})
    print(json.dumps({"status": "PASS_REMOTE_PUBLISHED_VIEW", "summary": summary, "fingerprint": fingerprint}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--after", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--before-dir", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        if args.before_dir is None:
            parser.error("--verify-only requires --before-dir")
        final_checks(args.plan, args.output_dir, args.before_dir)
    else:
        run(args.plan, args.output_dir, args.after)
