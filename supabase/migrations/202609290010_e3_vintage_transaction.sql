-- E3: one structured append-only gate table and atomic server-only vintage commands.
-- Historical facts and available_at are never updated. No seed data.

create table public.forecast_quality_gates (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  gate_type text not null check (gate_type in ('DATA_QUALITY','FORECAST_QUALITY','SERVICE_LEVEL')),
  status text not null,
  score numeric,
  target numeric,
  observed numeric,
  details_json jsonb not null default '{}'::jsonb check (jsonb_typeof(details_json)='object'),
  policy_version text not null,
  evaluated_at timestamptz not null default now(),
  evaluated_by uuid references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  unique (vintage_id,gate_type,policy_version)
);
create index forecast_quality_gates_chain_vintage on public.forecast_quality_gates(chain_id,vintage_id);
alter table public.forecast_quality_gates enable row level security;
create policy forecast_quality_gates_read on public.forecast_quality_gates for select to authenticated
  using (private.can_chain(chain_id,'view'));
create trigger forecast_quality_gates_insert_only before update or delete on public.forecast_quality_gates
  for each row execute function public.contract_insert_only();
grant select on public.forecast_quality_gates to authenticated;
grant select,insert on public.forecast_quality_gates to service_role;

-- The previous guard correctly blocked UNKNOWN as point-in-time evidence, but E3 also
-- needs FK-backed retrospective lineage. The explicit mode does not certify availability.
alter table public.forecast_run_inputs add column evidence_mode text not null default 'POINT_IN_TIME'
  check (evidence_mode in ('POINT_IN_TIME','RETROSPECTIVE_TRAINING'));
create or replace function public.contract_run_input_guard() returns trigger language plpgsql as $$
declare r public.forecast_runs%rowtype; o public.monthly_observations%rowtype;
declare c public.customer_forecast_versions%rowtype; v public.forecast_vintages%rowtype;
begin
  select * into r from public.forecast_runs where id=new.run_id;
  select * into v from public.forecast_vintages where run_id=new.run_id;
  if v.frozen_at is not null then raise exception 'cannot add input to frozen run' using errcode='23514'; end if;
  if new.data_snapshot_hash<>r.data_snapshot_hash then raise exception 'run input snapshot hash mismatch' using errcode='23514'; end if;
  perform public.contract_evidence_chain(new.source_evidence_id,new.chain_id);
  if new.monthly_observation_id is not null then
    select * into o from public.monthly_observations where id=new.monthly_observation_id;
    if o.period>r.issue_period then raise exception 'observation postdates issue' using errcode='23514'; end if;
    if new.evidence_mode='POINT_IN_TIME' and (o.available_at is null or o.available_at>r.cutoff_at) then
      raise exception 'observation unavailable at run cutoff' using errcode='23514';
    end if;
    if new.evidence_mode='RETROSPECTIVE_TRAINING' and (v.id is null or v.certification_status<>'PROVISIONAL') then
      raise exception 'retrospective input requires provisional vintage' using errcode='23514';
    end if;
  else
    if new.evidence_mode<>'POINT_IN_TIME' then raise exception 'customer forecast requires PIT evidence' using errcode='23514'; end if;
    select * into c from public.customer_forecast_versions where id=new.customer_forecast_version_id;
    if c.received_at>r.cutoff_at or c.issue_period>r.issue_period or c.status not in ('VALIDATED','FROZEN') then
      raise exception 'customer forecast unavailable at run cutoff' using errcode='23514';
    end if;
  end if;
  return new;
end $$;

-- All multi-table writes execute in one PostgreSQL statement/transaction. Only the
-- server's service_role may execute these functions; browser JWTs cannot call them.
create function public.e3_create_vintage_candidate(p jsonb) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v_id uuid; r_id uuid; actor uuid; chain uuid; item jsonb; digest text; issue date;
begin
  actor := (p->>'actor_id')::uuid; chain := (p->>'chain_id')::uuid;
  digest := p->>'content_hash'; issue := (p->>'issue_period')::date;
  if digest !~ '^[0-9a-f]{64}$' or p->>'certification_status'<>'PROVISIONAL' or
     p->>'objective' not in ('Venta','Pedido','Entrega') or extract(day from issue)<>1 or
     p->>'evidence_mode' not in ('POINT_IN_TIME','RETROSPECTIVE_TRAINING') or
     jsonb_typeof(p->'horizons')<>'array' or jsonb_array_length(p->'horizons')<12 or
     jsonb_typeof(p->'aggregates')<>'array' or jsonb_array_length(p->'aggregates')<12 or
     jsonb_typeof(p->'inputs')<>'array' or jsonb_array_length(p->'inputs')<1 or
     jsonb_typeof(p->'models')<>'array' or jsonb_array_length(p->'models')<1 or
     jsonb_typeof(p->'gates')<>'array' or jsonb_array_length(p->'gates')<>3 or
     not exists(select 1 from public.profiles where id=actor and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'invalid candidate request' using errcode='23514';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('E3-'||digest,0));
  select fv.id into v_id from public.forecast_runs fr join public.forecast_vintages fv on fv.run_id=fr.id
    where fr.run_code='E3-'||digest;
  if v_id is not null then return v_id; end if;
  r_id := gen_random_uuid(); v_id := gen_random_uuid();
  insert into public.forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,
    data_snapshot_hash,started_at,finished_at,actor_id,engine_version,git_sha)
  values(r_id,'E3-'||digest,chain,p->>'objective',issue,(p->>'cutoff_at')::timestamptz,
    'COMPLETED',p->>'data_snapshot_hash',now(),now(),actor,p->>'engine_version',p->>'git_sha');
  insert into public.forecast_vintages(id,run_id,chain_id,objective,issue_period,cutoff_at,
    forecast_version,certification_status)
  values(v_id,r_id,chain,p->>'objective',issue,(p->>'cutoff_at')::timestamptz,
    'E3-'||digest,'PROVISIONAL');
  for item in select value from jsonb_array_elements(p->'models') loop
    insert into public.model_versions(run_id,chain_id,model_family,algorithm,model_version,
      parameters_json,validation_wape,certification_status,candidate_status)
    values(r_id,chain,item->>'family',item->>'algorithm',p->>'engine_version',
      coalesce(item->'parameters','{}'::jsonb),(item->>'validation_wape')::numeric,
      'PROVISIONAL','CHALLENGER');
  end loop;
  for item in select value from jsonb_array_elements(p->'horizons') loop
    insert into public.forecast_horizons(vintage_id,chain_id,product_id,category_id,target_period,horizon,
      statistical_value,ml_value,ensemble_value,forecast_towell,operational_value,model_strategy,
      confidence,p10,p50,p90,p95,band_basis,band_observations,forecast_status,certification_status)
    values(v_id,chain,(item->>'product_id')::uuid,(item->>'category_id')::uuid,
      (item->>'target_period')::date,(item->>'horizon')::integer,(item->>'statistical_value')::numeric,
      (item->>'ml_value')::numeric,(item->>'ensemble_value')::numeric,
      (item->>'forecast_towell')::numeric,null,item->>'model_strategy',item->>'confidence',
      (item->>'p10')::numeric,(item->>'p50')::numeric,(item->>'p90')::numeric,(item->>'p95')::numeric,
      item->>'band_basis',(item->>'band_observations')::integer,item->>'forecast_status','PROVISIONAL');
  end loop;
  for item in select value from jsonb_array_elements(p->'aggregates') loop
    insert into public.forecast_aggregates(vintage_id,chain_id,level,category_id,target_period,horizon,
      forecast_towell,p10,p50,p90,p95)
    values(v_id,chain,item->>'level',(item->>'category_id')::uuid,(item->>'target_period')::date,
      (item->>'horizon')::integer,(item->>'forecast_towell')::numeric,(item->>'p10')::numeric,
      (item->>'p50')::numeric,(item->>'p90')::numeric,(item->>'p95')::numeric);
  end loop;
  for item in select value from jsonb_array_elements(p->'inputs') loop
    insert into public.forecast_run_inputs(run_id,chain_id,monthly_observation_id,source_evidence_id,
      data_snapshot_hash,evidence_mode)
    values(r_id,chain,(item->>'observation_id')::uuid,(item->>'source_evidence_id')::uuid,
      p->>'data_snapshot_hash',p->>'evidence_mode');
  end loop;
  for item in select value from jsonb_array_elements(p->'metrics') loop
    insert into public.performance_metrics(vintage_id,chain_id,metric,phase,value,period)
    values(v_id,chain,item->>'metric','VALIDATION',(item->>'value')::numeric,issue);
  end loop;
  for item in select value from jsonb_array_elements(p->'gates') loop
    insert into public.forecast_quality_gates(vintage_id,chain_id,gate_type,status,score,target,
      observed,details_json,policy_version,evaluated_by)
    values(v_id,chain,item->>'gate_type',item->>'status',(item->>'score')::numeric,
      (item->>'target')::numeric,(item->>'observed')::numeric,item->'details',
      p->>'policy_version',actor);
  end loop;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
  values(chain,actor,'CREATE_VINTAGE_CANDIDATE','forecast_vintage',v_id,
    jsonb_build_object('content_hash',digest,'preview_id',p->>'preview_id',
      'policy_version',p->>'policy_version','evidence_mode',p->>'evidence_mode'));
  return v_id;
end $$;

create function public.e3_freeze_vintage(p_vintage_id uuid,p_actor_id uuid) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype; r public.forecast_runs%rowtype;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  if v.id is null or not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'manager or vintage missing' using errcode='23514'; end if;
  if v.frozen_at is not null then return v.id; end if;
  select * into r from public.forecast_runs where id=v.run_id;
  if r.status<>'COMPLETED' or not exists(select 1 from public.forecast_run_inputs where run_id=r.id) or
     (select count(*) from public.forecast_quality_gates where vintage_id=v.id and policy_version='E3-GATES-1.0.0')<>3 then
    raise exception 'vintage not freezable' using errcode='23514'; end if;
  update public.forecast_vintages set frozen_at=now() where id=v.id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id)
    values(v.chain_id,p_actor_id,'FREEZE_VINTAGE','forecast_vintage',v.id);
  return v.id;
end $$;

-- Official status is an append-only audit event; frozen forecast rows never change.
create function public.e3_publish_official(p_vintage_id uuid,p_actor_id uuid,p_comment text) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  if v.id is null or v.frozen_at is null or p_comment is null or length(trim(p_comment))<5 or
     not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'publication precondition failed' using errcode='23514'; end if;
  if exists(select 1 from public.forecast_run_inputs where run_id=v.run_id and evidence_mode='RETROSPECTIVE_TRAINING') or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and gate_type='DATA_QUALITY' and status='DATA_QUALITY_READY') or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and gate_type='FORECAST_QUALITY' and status='FORECAST_QUALITY_READY') or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and gate_type='SERVICE_LEVEL') then
    raise exception 'quality gate blocked' using errcode='23514'; end if;
  if exists(select 1 from public.audit_log where action='PUBLISH_OFFICIAL' and entity_id=v.id) then return v.id; end if;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(v.chain_id,p_actor_id,'PUBLISH_OFFICIAL','forecast_vintage',v.id,
      jsonb_build_object('comment',p_comment,'issue_period',v.issue_period,'forecast_version',v.forecast_version));
  return v.id;
end $$;

create function public.e3_promote_champion(p_vintage_id uuid,p_model_version_id uuid,
  p_actor_id uuid,p_comment text) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype; m public.model_versions%rowtype; c_id uuid;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  select * into m from public.model_versions where id=p_model_version_id;
  if v.id is null or v.frozen_at is null or m.id is null or m.run_id<>v.run_id or
     m.certification_status<>'CERTIFIED' or p_comment is null or length(trim(p_comment))<5 or
     not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and gate_type='FORECAST_QUALITY'
       and status='FORECAST_QUALITY_READY' and details_json->>'no_degradation'='true') then
    raise exception 'champion precondition failed' using errcode='23514'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v.chain_id::text||v.objective,0));
  select id into c_id from public.champion_registry where chain_id=v.chain_id and objective=v.objective
    and scope_type='CHAIN' and scope_id=v.chain_id and status='ACTIVE';
  if c_id is not null and exists(select 1 from public.champion_registry where id=c_id and model_version_id=m.id) then
    return c_id; end if;
  if c_id is not null then
    update public.champion_registry set status='RETIRED',valid_to=now() where id=c_id;
    insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id)
      values(v.chain_id,p_actor_id,'RETIRE_CHAMPION','champion_registry',c_id);
  end if;
  c_id := gen_random_uuid();
  insert into public.champion_registry(id,chain_id,objective,scope_type,scope_id,model_version_id,
    strategy,published_at,published_by,valid_from,status)
  values(c_id,v.chain_id,v.objective,'CHAIN',v.chain_id,m.id,m.algorithm,now(),p_actor_id,now(),'ACTIVE');
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(v.chain_id,p_actor_id,'PROMOTE_CHAMPION','champion_registry',c_id,
      jsonb_build_object('vintage_id',v.id,'model_version_id',m.id,'comment',p_comment));
  return c_id;
end $$;

-- Explicit LIVE close for future observations with literal available_at. Atomic,
-- append-only, and separate from publication or Champion promotion.
create function public.e3_close_target_period(p_vintage_id uuid,p_target_period date,p_actor_id uuid)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype; h record; actual public.monthly_observations%rowtype;
declare sum_actual numeric := 0; sum_abs numeric := 0; sum_signed numeric := 0;
declare sum_squared numeric := 0; count_actual integer := 0; wape numeric; bias numeric;
declare f record; ordered numeric; delivered numeric; order_total numeric := 0;
declare delivery_total numeric := 0; service_pairs integer := 0; measurable boolean := true;
declare fill_rate numeric;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  if v.id is null or v.frozen_at is null or extract(day from p_target_period)<>1 or
     not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'live close precondition failed' using errcode='23514'; end if;
  if exists(select 1 from public.performance_metrics where vintage_id=v.id and period=p_target_period and phase='LIVE') then
    if (select count(*) from public.performance_metrics where vintage_id=v.id and period=p_target_period and phase='LIVE')=5 then
      return jsonb_build_object('status','ALREADY_CLOSED','vintage_id',v.id,'period',p_target_period);
    end if;
    raise exception 'partial live close conflict' using errcode='23514';
  end if;
  for h in select * from public.forecast_horizons where vintage_id=v.id and target_period=p_target_period order by product_id loop
    select * into actual from public.monthly_observations o where o.chain_id=v.chain_id
      and o.product_id=h.product_id and o.period=p_target_period
      and o.metric_code=case v.objective when 'Venta' then 'SALES' when 'Pedido' then 'ORDER' else 'DELIVERY' end
      and o.available_at is not null and o.available_at<=now()
      order by o.version_no desc limit 1;
    if actual.id is null then raise exception 'actual observation missing' using errcode='23514'; end if;
    insert into public.actual_evaluations(vintage_id,chain_id,product_id,horizon,target_period,
      forecast_value,actual_value,actual_observation_id)
      values(v.id,v.chain_id,h.product_id,h.horizon,p_target_period,h.forecast_towell,actual.value,actual.id);
    sum_actual := sum_actual + abs(actual.value);
    sum_abs := sum_abs + abs(actual.value-h.forecast_towell);
    sum_signed := sum_signed + h.forecast_towell-actual.value;
    sum_squared := sum_squared + power(h.forecast_towell-actual.value,2);
    count_actual := count_actual + 1;
  end loop;
  if count_actual=0 then raise exception 'no horizons for target period' using errcode='23514'; end if;
  wape := case when sum_actual=0 then case when sum_abs=0 then 0 else 100 end else 100*sum_abs/sum_actual end;
  bias := case when sum_actual=0 then case when sum_signed=0 then 0 when sum_signed>0 then 100 else -100 end
    else 100*sum_signed/sum_actual end;
  for f in select distinct product_id from public.monthly_observations where chain_id=v.chain_id
    and period=p_target_period and metric_code in ('ORDER','DELIVERY') and available_at is not null and available_at<=now() loop
    select value into ordered from public.monthly_observations where chain_id=v.chain_id
      and product_id=f.product_id and period=p_target_period and metric_code='ORDER'
      and available_at is not null and available_at<=now() order by version_no desc limit 1;
    select value into delivered from public.monthly_observations where chain_id=v.chain_id
      and product_id=f.product_id and period=p_target_period and metric_code='DELIVERY'
      and available_at is not null and available_at<=now() order by version_no desc limit 1;
    if ordered is null or delivered is null or (ordered=0 and delivered>0) then measurable := false;
    else
      order_total := order_total + ordered; delivery_total := delivery_total + delivered;
      service_pairs := service_pairs + 1;
    end if;
    ordered := null; delivered := null;
  end loop;
  fill_rate := case when not measurable or service_pairs=0 then null when order_total=0 then 100
    else 100*delivery_total/order_total end;
  insert into public.performance_metrics(vintage_id,chain_id,metric,phase,value,period) values
    (v.id,v.chain_id,'WAPE','LIVE',wape,p_target_period),
    (v.id,v.chain_id,'BIAS','LIVE',bias,p_target_period),
    (v.id,v.chain_id,'MAE','LIVE',sum_abs/count_actual,p_target_period),
    (v.id,v.chain_id,'RMSE','LIVE',sqrt(sum_squared/count_actual),p_target_period),
    (v.id,v.chain_id,'FILL_RATE','LIVE',fill_rate,p_target_period);
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(v.chain_id,p_actor_id,'MONTHLY_CLOSE','forecast_vintage',v.id,
      jsonb_build_object('target_period',p_target_period,'actual_count',count_actual,
        'service_pairs',service_pairs,'fill_rate_target',95.0,'fill_rate_gap_pp',fill_rate-95.0));
  return jsonb_build_object('status','CLOSED','vintage_id',v.id,'period',p_target_period,
    'actual_count',count_actual,'wape',wape,'bias',bias,'fill_rate',fill_rate,'target_fill_rate',95.0);
end $$;

revoke all on function public.e3_create_vintage_candidate(jsonb) from public,anon,authenticated;
revoke all on function public.e3_freeze_vintage(uuid,uuid) from public,anon,authenticated;
revoke all on function public.e3_publish_official(uuid,uuid,text) from public,anon,authenticated;
revoke all on function public.e3_promote_champion(uuid,uuid,uuid,text) from public,anon,authenticated;
revoke all on function public.e3_close_target_period(uuid,date,uuid) from public,anon,authenticated;
grant execute on function public.e3_create_vintage_candidate(jsonb) to service_role;
grant execute on function public.e3_freeze_vintage(uuid,uuid) to service_role;
grant execute on function public.e3_publish_official(uuid,uuid,text) to service_role;
grant execute on function public.e3_promote_champion(uuid,uuid,uuid,text) to service_role;
grant execute on function public.e3_close_target_period(uuid,date,uuid) to service_role;
grant select,insert,update on public.forecast_runs,public.model_versions,public.forecast_vintages,
  public.forecast_horizons,public.forecast_aggregates,public.forecast_run_inputs,
  public.performance_metrics,public.champion_registry,public.audit_log to service_role;
grant select,insert on public.actual_evaluations to service_role;
grant usage on schema public to service_role;
grant select on public.profiles,public.monthly_observations,public.products,
  public.categories,public.chains,public.source_evidence to service_role;
notify pgrst, 'reload schema';
