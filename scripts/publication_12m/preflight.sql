-- READ-ONLY preparation contract, not executed on production by this change.
-- Run only with the authorised user's JWT/context in an explicitly authorised
-- environment. Never replace missing RLS membership with service_role.
select routine_schema, routine_name
from information_schema.routines
where routine_schema='public' and routine_name in
 ('operational_read_context','operational_read_catalog','operational_read_observations',
  'operational_read_planning_run','operational_read_inventory','operational_read_fulfillment')
order by routine_name;

-- Checks presence, not write access or commercial permission. The hosted
-- durability contract is the existing server-owned Railway volume; no new
-- writable Data API table/RPC is exposed for uploading fabricated results.
select table_schema,table_name
from information_schema.tables
where table_schema='public' and table_name in ('profiles','user_chain_access')
order by table_name;

-- Reading this context alone does not attest a future close or grant execution.
-- Exact source hash/generation, observed/model cutoffs and all context fields
-- must agree with the app's frozen request and the RLS-scoped input read.
select * from public.operational_read_context();
