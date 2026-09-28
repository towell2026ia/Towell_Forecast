-- PRD 09.2E.1.3 / migration 009: small, RLS-scoped published period catalog.
-- Schema-only; migration 008, policies, facts and temporal availability unchanged.
create view public.portal_history_periods
with (security_invoker=true) as
select distinct chain_id, period
from public.portal_monthly_observations_current;

revoke all on public.portal_history_periods from public, anon, authenticated;
grant select on public.portal_history_periods to authenticated;
comment on view public.portal_history_periods is
  'Distinct scope/period catalog from caller-visible published facts; no quantities or private metadata.';
notify pgrst, 'reload schema';
