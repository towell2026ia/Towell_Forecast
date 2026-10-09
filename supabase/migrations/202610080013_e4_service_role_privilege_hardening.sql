-- PRD 09.2E4 Gate B.1: narrow the materialized table ACL for service_role.
-- Remote audit found direct relacl entries with ALL privileges on exactly these
-- seven E4 tables. service_role is not their owner and inherits no table role;
-- PUBLIC has no grant. Do not change global default privileges or migration 012.

revoke all privileges on table
  public.forecast_calculations,
  public.forecast_calculation_horizons,
  public.forecast_selection_events,
  public.forecast_capture_sessions,
  public.forecast_live_evaluations,
  public.forecast_learning_events,
  public.forecast_live_closures
from service_role;

grant select, insert, update on table
  public.forecast_calculations,
  public.forecast_capture_sessions
to service_role;

grant select, insert on table
  public.forecast_calculation_horizons,
  public.forecast_selection_events,
  public.forecast_live_evaluations,
  public.forecast_learning_events,
  public.forecast_live_closures
to service_role;

-- Fail the migration atomically if inherited/owner/PUBLIC rights make the
-- effective matrix broader than intended. Browser access remains read-only
-- for authenticated users, subject to the existing chain RLS policies.
do $$
declare v_table text; v_privilege text; v_relation regclass; v_expected boolean;
begin
  foreach v_table in array array[
    'forecast_calculations', 'forecast_calculation_horizons',
    'forecast_selection_events', 'forecast_capture_sessions',
    'forecast_live_evaluations', 'forecast_learning_events',
    'forecast_live_closures'
  ] loop
    v_relation := format('public.%I', v_table)::regclass;
    if not (select relrowsecurity from pg_class where oid = v_relation) or
       exists (select 1 from pg_class c cross join lateral aclexplode(c.relacl) a
               where c.oid = v_relation and a.grantee = 0) then
      raise exception 'E4 RLS/PUBLIC privilege guard failed for %', v_table;
    end if;
    foreach v_privilege in array array[
      'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
    ] loop
      v_expected := v_privilege in ('SELECT', 'INSERT') or
        (v_privilege = 'UPDATE' and v_table in ('forecast_calculations', 'forecast_capture_sessions'));
      if has_table_privilege('service_role', v_relation, v_privilege) is distinct from v_expected or
         has_table_privilege('authenticated', v_relation, v_privilege) is distinct from (v_privilege = 'SELECT') or
         has_table_privilege('anon', v_relation, v_privilege) then
        raise exception 'E4 effective privilege guard failed for %: %', v_table, v_privilege;
      end if;
    end loop;
  end loop;
end $$;
