-- PRD 09.2E.1.1 / migration 008: DISPLAY history, not point-in-time data.
-- Schema-only. No facts, batches, evidence, policies or temporal views changed.
create view public.portal_monthly_observations_current
with (security_invoker=true) as
select distinct on (o.chain_id,o.product_id,o.period,o.metric_code)
  o.id as observation_id,
  o.chain_id,c.code as chain_code,c.name as chain_name,
  o.product_id,p.product_code,p.variant_code,
  p.description as product_description,p.category_id,
  o.period,o.metric_code,o.value,o.version_no,
  o.availability_source,o.available_at,o.source_batch_id
from public.monthly_observations o
join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
join public.products p on p.id=o.product_id and p.chain_id=o.chain_id
join public.chains c on c.id=o.chain_id
where b.status='IMPORTED'
order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc;

-- Caller identity and all four underlying RLS policies remain authoritative.
revoke all on public.portal_monthly_observations_current from public,anon,authenticated;
grant select on public.portal_monthly_observations_current to authenticated;
comment on view public.portal_monthly_observations_current is
  'Latest IMPORTED historical display facts. UNKNOWN/NULL preserved. Not eligible for point-in-time replay.';
notify pgrst, 'reload schema';
