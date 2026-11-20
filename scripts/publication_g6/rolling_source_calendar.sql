-- Optional only when installing a newly accredited source after G6.
-- Calculating revisions against already published G6 does not require this change.
begin;
do $calendar$
declare
  constraint_name name;
  original_expression text;
begin
  -- Preserve the installed rule for G6 exactly, including its original dates.
  select conname, pg_get_expr(conbin, conrelid) into strict constraint_name, original_expression
  from pg_constraint
  where conrelid = 'private.ft_g6_release'::regclass and contype = 'c'
    and position('2026-09' in pg_get_expr(conbin, conrelid)) > 0
    and position('2027-12' in pg_get_expr(conbin, conrelid)) > 0;
  if constraint_name = 'ft_release_authorized_calendar' then return; end if;
  execute format('alter table private.ft_g6_release drop constraint %I', constraint_name);
  execute format($ddl$alter table private.ft_g6_release add constraint ft_release_authorized_calendar
  check (((%s) or (
    (context->>'generation') ~ '^[1-9][0-9]*$' and (context->>'generation')::bigint > 6
    and context->>'forecast_contract' = 'pedido-unificado-v1' and context->>'objective' = 'Pedido'
    and context->>'target_metric' = 'ORDER' and context->>'unit' = 'PIEZAS'
    and context->>'target_sheet' = 'Base_2024_2026'
    and context->>'target_file_sha256' = '29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d'
    and context->>'source_hash' ~ '^[a-f0-9]{64}$' and context->>'source_hash' = context->>'authorized_source_hash'
    and context->>'scenario_hash' ~ '^[a-f0-9]{64}$' and context->>'planning_basis' = 'OBSERVED_AND_COMMERCIAL_CLOSE'
    and context->>'first_period' ~ '^20[0-9]{2}-(0[1-9]|1[012])$'
    and context->>'observed_cutoff' ~ '^20[0-9]{2}-(0[1-9]|1[012])$'
    and context->>'training_cutoff' ~ '^20[0-9]{2}-(0[1-9]|1[012])$'
    and context->>'last_period' ~ '^20[0-9]{2}-(0[1-9]|1[012])$'
    and context->>'observed_cutoff' between context->>'first_period' and context->>'training_cutoff'
    and context->>'training_cutoff' <= context->>'last_period'
    and context->>'planning_start' = to_char((context->>'training_cutoff' || '-01')::date + interval '1 month','YYYY-MM')
    and context->>'planning_end' = to_char((context->>'training_cutoff' || '-01')::date + interval '12 months','YYYY-MM')
  )) is true)$ddl$, original_expression);
end
$calendar$;
commit;
