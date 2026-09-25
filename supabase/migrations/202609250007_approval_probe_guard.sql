-- PRD 09.2C hardening: prevent direct calls to the SECURITY DEFINER helper
-- from probing decision existence in chains without can_approve.
-- This is forward-only; do not edit applied migration 005.
create or replace function private.has_open_decision(
  p_vintage uuid, p_product uuid, p_target date, p_version integer, p_forecast numeric
) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.forecast_decisions d
    where private.can_chain(d.chain_id, 'approve')
      and d.vintage_id = p_vintage and d.product_id = p_product
      and d.target_period = p_target and d.version_no = p_version - 1
      and d.forecast_towell = p_forecast and d.approved_by is null
      and p_version > 1
  );
$$;
