-- Result visibility follows the sole current source.
alter table public.forecast_runs add column source_context jsonb not null default '{}'::jsonb
  check(jsonb_typeof(source_context)='object');
create function private.official_result_current(p_context jsonb) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.official_source_current s where s.singleton
    and p_context->>'source_hash'=s.source_hash and p_context->>'generation'=s.generation::text);
$$;
create function private.official_run_current(p_run uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.forecast_runs r where r.id=p_run
    and private.official_result_current(r.source_context));
$$;
create function private.official_observation_current(p_observation uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from private.official_observation_source p
    join public.monthly_observations o on o.id=p.observation_id
    join public.import_batches b on b.id=o.source_batch_id and b.status='IMPORTED'
    join public.official_source_current s on s.source_hash=b.official_source_hash and b.evidence_id=any(s.evidence_ids)
    where p.observation_id=p_observation);
$$;
revoke all on function private.official_result_current(jsonb),private.official_run_current(uuid),
  private.official_observation_current(uuid)
  from public,anon,authenticated,service_role;
grant execute on function private.official_result_current(jsonb),private.official_run_current(uuid),
  private.official_observation_current(uuid) to authenticated;
create function private.official_batch_current(p_batch uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.import_batches b join public.official_source_current s
    on s.source_hash=b.official_source_hash and b.evidence_id=any(s.evidence_ids) where b.id=p_batch);
$$;
create function private.official_evidence_current(p_evidence uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.official_source_current s where p_evidence=any(s.evidence_ids));
$$;
revoke all on function private.official_batch_current(uuid),private.official_evidence_current(uuid)
  from public,anon,authenticated,service_role;
grant execute on function private.official_batch_current(uuid),private.official_evidence_current(uuid) to authenticated;
create policy official_imports_current on public.import_batches as restrictive for select to authenticated
  using(private.official_batch_current(id));
create policy official_evidence_current on public.source_evidence as restrictive for select to authenticated
  using(private.official_evidence_current(id));
create function private.official_research_current(p_snapshot uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.forecast_runs r where r.research_snapshot_id=p_snapshot
    and private.official_result_current(r.source_context));
$$;
create function private.official_job_current(p_job uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.jobs j where j.id=p_job and (
    private.official_run_current(j.run_id) or private.official_result_current(j.metadata_json->'source_context')));
$$;
revoke all on function private.official_research_current(uuid),private.official_job_current(uuid)
  from public,anon,authenticated,service_role;
grant execute on function private.official_research_current(uuid),private.official_job_current(uuid) to authenticated;
create policy official_research_current on public.research_snapshots as restrictive for select to authenticated
  using(private.official_research_current(id));
create policy official_research_sources_current on public.research_snapshot_sources as restrictive for select to authenticated
  using(private.official_research_current(snapshot_id));
create policy official_jobs_current on public.jobs as restrictive for select to authenticated
  using(private.official_run_current(run_id) or private.official_result_current(metadata_json->'source_context'));
create policy official_logs_current on public.run_logs as restrictive for select to authenticated
  using(private.official_run_current(run_id) or private.official_job_current(job_id));
revoke select on public.import_batches,public.source_evidence,public.research_snapshots,
  public.research_snapshot_sources,public.jobs,public.run_logs from service_role;

create policy official_runs_current on public.forecast_runs as restrictive for select to authenticated
  using(private.official_result_current(source_context));
create policy official_models_current on public.model_versions as restrictive for select to authenticated
  using(private.official_run_current(run_id));
create policy official_vintages_current on public.forecast_vintages as restrictive for select to authenticated
  using(private.official_run_current(run_id));
do $$ declare relation text; begin
  foreach relation in array array['forecast_horizons','forecast_aggregates','forecast_decisions','actual_evaluations','performance_metrics'] loop
    execute format('create policy official_vintage_current on public.%I as restrictive for select to authenticated using (
      exists(select 1 from public.forecast_vintages v where v.id=vintage_id and private.official_run_current(v.run_id))
    )',relation);
  end loop;
end $$;
do $$ begin
  if to_regclass('public.forecast_quality_gates') is not null then
    create policy official_vintage_current on public.forecast_quality_gates as restrictive for select to authenticated
      using(exists(select 1 from public.forecast_vintages v where v.id=vintage_id and private.official_run_current(v.run_id)));
    revoke select on public.forecast_quality_gates from service_role;
  end if;
end $$;
create policy official_champion_current on public.champion_registry as restrictive for select to authenticated
  using(exists(select 1 from public.model_versions m where m.id=model_version_id and private.official_run_current(m.run_id)));
create policy official_run_inputs_current on public.forecast_run_inputs as restrictive for select to authenticated
  using(private.official_run_current(run_id));
create policy official_customer_forecast_replaced on public.customer_forecast_versions as restrictive for select to authenticated using(false);
create policy official_customer_rows_replaced on public.customer_forecast_rows as restrictive for select to authenticated using(false);
revoke all on public.forecast_runs,public.model_versions,public.forecast_vintages,public.forecast_horizons,
  public.forecast_aggregates,public.forecast_decisions,public.actual_evaluations,public.performance_metrics,
  public.champion_registry,public.forecast_run_inputs,public.customer_forecast_versions,public.customer_forecast_rows from service_role;

do $$ begin
  if to_regclass('public.forecast_calculations') is not null then
    execute $ddl$
      create function private.official_calculation_current(p_calculation uuid) returns boolean
      language sql stable security definer set search_path='' as $body$
        select exists(select 1 from public.forecast_calculations c where c.id=p_calculation
          and private.official_result_current(c.input_snapshot->'source_context'));
      $body$;
      revoke all on function private.official_calculation_current(uuid) from public,anon,authenticated,service_role;
      grant execute on function private.official_calculation_current(uuid) to authenticated;
      create policy official_calculation_current on public.forecast_calculations as restrictive for select to authenticated
        using(private.official_result_current(input_snapshot->'source_context'));
    $ddl$;
  end if;
end $$;
do $$ declare relation text; begin
  foreach relation in array array[
    'forecast_calculation_horizons','forecast_selection_events','forecast_live_evaluations','forecast_learning_events'
  ] loop
    if to_regclass('public.'||relation) is not null then
      execute format('create policy official_calculation_current on public.%I as restrictive for select to authenticated
        using(private.official_calculation_current(calculation_id))',relation);
      execute format('revoke all on public.%I from service_role',relation);
    end if;
  end loop;
  if to_regclass('public.forecast_calculations') is not null then
    revoke all on public.forecast_calculations from service_role;
  end if;
  if to_regclass('public.current_forecast_selection') is not null then
    revoke all on public.current_forecast_selection from public,anon,service_role;
    alter view public.current_forecast_selection set (security_invoker=true);
  end if;
  if to_regclass('public.forecast_live_closures') is not null then
    create policy official_closure_current on public.forecast_live_closures as restrictive for select to authenticated
      using(private.official_observation_current(sale_observation_id));
    revoke select on public.forecast_live_closures from service_role;
  end if;
end $$;

-- Existing transaction bodies are retained under private names. The wrappers
-- add source provenance while their numeric fields and algorithms stay intact.
do $$ declare implementation text; anchor text;
begin
  if to_regprocedure('public.e4_create_calculation(jsonb)') is not null then
    alter function public.e4_create_calculation(jsonb) set schema private;
    alter function private.e4_create_calculation(jsonb) rename to official_e4_create_impl;
    implementation=pg_get_functiondef('private.official_e4_create_impl(jsonb)'::regprocedure);
    anchor=$text$jsonb_build_object('product',p->'product_snapshot','observation_ids',p->'input_observation_ids')$text$;
    if strpos(implementation,anchor)=0
      then raise exception 'OFFICIAL_E4_SOURCE_CONTRACT_UNRECOGNIZED'; end if;
    execute replace(implementation,anchor,
      $text$jsonb_build_object('product',p->'product_snapshot','observation_ids',p->'input_observation_ids','source_context',p->'source_context')$text$);
    revoke all on function private.official_e4_create_impl(jsonb) from public,anon,authenticated,service_role;
    execute $ddl$
      create function public.e4_create_calculation(p jsonb) returns uuid
      language plpgsql security definer set search_path='' as $body$
      declare created uuid;
      begin
        perform 1 from public.official_source_current where singleton for share;
        if not private.official_result_current(p->'source_context')
          then raise exception 'OFFICIAL_SOURCE_CHANGED' using errcode='40001'; end if;
        if exists(select 1 from jsonb_array_elements_text(p->'input_observation_ids') i(id)
          where not exists(select 1 from private.official_observation_source provenance
            join public.monthly_observations o on o.id=provenance.observation_id
            join public.import_batches b on b.id=o.source_batch_id
            join public.official_source_current s on s.source_hash=b.official_source_hash
              and b.evidence_id=any(s.evidence_ids)
            where o.id=i.id::uuid and b.status='IMPORTED'))
          then raise exception 'OFFICIAL_CALCULATION_INPUT_INVALID'; end if;
        created=private.official_e4_create_impl(p);
        if not private.official_calculation_current(created)
          then raise exception 'OFFICIAL_CALCULATION_SOURCE_INVALID'; end if;
        return created;
      end $body$;
      revoke all on function public.e4_create_calculation(jsonb) from public,anon,authenticated,service_role;
      grant execute on function public.e4_create_calculation(jsonb) to service_role;
    $ddl$;
  end if;
  if to_regprocedure('public.e3_create_vintage_candidate(jsonb)') is not null then
    alter function public.e3_create_vintage_candidate(jsonb) set schema private;
    alter function private.e3_create_vintage_candidate(jsonb) rename to official_e3_create_impl;
    revoke all on function private.official_e3_create_impl(jsonb) from public,anon,authenticated,service_role;
    execute $ddl$
      create function public.e3_create_vintage_candidate(p jsonb) returns uuid
      language plpgsql security definer set search_path='' as $body$
      declare created uuid; run uuid;
      begin
        perform 1 from public.official_source_current where singleton for share;
        if not private.official_result_current(p->'source_context')
          then raise exception 'OFFICIAL_SOURCE_CHANGED' using errcode='40001'; end if;
        if exists(select 1 from public.forecast_runs r where r.run_code='E3-'||(p->>'content_hash')
          and not private.official_result_current(r.source_context))
          then raise exception 'OFFICIAL_CALCULATION_SOURCE_INVALID'; end if;
        if exists(select 1 from jsonb_array_elements(p->'inputs') i
          where not exists(select 1 from private.official_observation_source provenance
            join public.monthly_observations o on o.id=provenance.observation_id
            join public.import_batches b on b.id=o.source_batch_id
            join public.official_source_current s on s.source_hash=b.official_source_hash and b.evidence_id=any(s.evidence_ids)
            where o.id=(i->>'observation_id')::uuid and b.status='IMPORTED'))
          then raise exception 'OFFICIAL_CALCULATION_INPUT_INVALID'; end if;
        created=private.official_e3_create_impl(p);
        select v.run_id into run from public.forecast_vintages v where v.id=created;
        update public.forecast_runs set source_context=p->'source_context' where id=run and source_context='{}'::jsonb;
        if not private.official_run_current(run)
          then raise exception 'OFFICIAL_CALCULATION_SOURCE_INVALID'; end if;
        return created;
      end $body$;
      revoke all on function public.e3_create_vintage_candidate(jsonb) from public,anon,authenticated,service_role;
      grant execute on function public.e3_create_vintage_candidate(jsonb) to service_role;
    $ddl$;
  end if;
  if to_regprocedure('public.e4_select_forecast(jsonb)') is not null then
    alter function public.e4_select_forecast(jsonb) set schema private;
    alter function private.e4_select_forecast(jsonb) rename to official_e4_select_impl;
    implementation=pg_get_functiondef('private.official_e4_select_impl(jsonb)'::regprocedure);
    anchor='old.objective=c.objective';
    if strpos(implementation,anchor)=0
      then raise exception 'OFFICIAL_E4_SELECTION_CONTRACT_UNRECOGNIZED'; end if;
    execute replace(implementation,anchor,anchor||' and private.official_calculation_current(old.id)');
    revoke all on function private.official_e4_select_impl(jsonb) from public,anon,authenticated,service_role;
    execute $ddl$
      create function public.e4_select_forecast(p jsonb) returns uuid
      language plpgsql security definer set search_path='' as $body$
      begin
        perform 1 from public.official_source_current where singleton for share;
        if not private.official_calculation_current((p->>'calculation_id')::uuid)
          then raise exception 'OFFICIAL_CALCULATION_SOURCE_INVALID' using errcode='40001'; end if;
        return private.official_e4_select_impl(p);
      end $body$;
      revoke all on function public.e4_select_forecast(jsonb) from public,anon,authenticated,service_role;
      grant execute on function public.e4_select_forecast(jsonb) to service_role;
    $ddl$;
  end if;
end $$;
