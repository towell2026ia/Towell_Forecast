"""Capture private, authenticated READ ONLY evidence for commercial dashboards."""
import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_portal_periods import query  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--catalog-sql", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / "outputs") or output.exists():
        raise ValueError("fresh_private_output_required")
    actor = str(uuid.UUID(args.actor_id))
    sql = args.catalog_sql.read_text(encoding="utf-8-sig")
    if not sql.startswith("BEGIN TRANSACTION READ ONLY;") or f"'{actor}'" not in sql or "ROLLBACK;" not in sql:
        raise ValueError("reviewed_read_only_catalog_sql_required")
    catalog = query(sql)[0]["catalog"]
    if catalog["actor"] is None or catalog["actor"]["id"] != actor or catalog["actor"]["status"] != "ACTIVE":
        raise ValueError("active_rls_identity_required")
    dashboard = query(f"""begin transaction read only;
set local statement_timeout='60s';
set local "request.jwt.claim.sub"='{actor}';
set local role authenticated;
select jsonb_build_object(
'history',(select coalesce(jsonb_agg(jsonb_build_object('chain_id',chain_id,'product_id',product_id,'period',period,'metric_code',metric_code,'value',value,'observation_id',observation_id,'chain_name',chain_name,'product_code',product_code,'variant_code',variant_code,'product_description',product_description,'category_id',category_id,'availability_source',availability_source) order by observation_id),'[]') from public.portal_monthly_observations_current),
'calculations',(select coalesce(jsonb_agg(to_jsonb(c) order by c.id),'[]') from public.forecast_calculations c),
'horizons',(select coalesce(jsonb_agg(to_jsonb(h) order by h.id),'[]') from public.forecast_calculation_horizons h),
'decisions',(select coalesce(jsonb_agg(to_jsonb(s) order by s.id),'[]') from public.forecast_selection_events s),
'classifications',(select coalesce(jsonb_agg(to_jsonb(c) order by c.product_id),'[]') from public.product_commercial_classifications_current c),
'mapping_count',(select count(*) from public.product_commercial_classifications),
'customers',(select coalesce(jsonb_agg(to_jsonb(c) order by c.id),'[]') from public.customer_forecast_rows c)
) as dashboard;
rollback;""")[0]["dashboard"]
    output.mkdir(parents=True)
    (output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"products": len(catalog["products"]), "observations": len(dashboard["history"]),
        "calculations": len(dashboard["calculations"]), "decisions": len(dashboard["decisions"]), "mappings": dashboard["mapping_count"], "writes": 0}))


if __name__ == "__main__":
    main()
