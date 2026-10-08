-- PRD 09.2E4, additive PREPARED migration. Do not apply remotely without approval.
-- All writes are server-only RPC transactions. No historical row is rewritten.
create table public.forecast_calculations (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  product_id uuid not null,
  objective text not null check (objective='Venta'),
  calculation_no integer not null check (calculation_no>0),
  calculation_code text not null check (calculation_code ~ '^C[1-9][0-9]*$'),
  issue_period date not null check (extract(day from issue_period)=1),
  cutoff_at timestamptz not null,
  preview_id text not null,
  data_snapshot_hash char(64) not null check (data_snapshot_hash ~ '^[0-9a-f]{64}$'),
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  input_snapshot jsonb not null check (jsonb_typeof(input_snapshot)='object'),
  candidate_metrics jsonb not null check (jsonb_typeof(candidate_metrics)='object'),
  research_snapshot jsonb,
  suggested_reference jsonb,
  scope_leader jsonb,
  evidence_mode text not null check (evidence_mode in ('POINT_IN_TIME','RETROSPECTIVE_TRAINING')),
  engine_version text not null,
  git_sha text not null,
  recalculation_reason text,
  status text not null default 'READY_FOR_DECISION' check (status in
    ('QUEUED','RUNNING','READY_FOR_DECISION','DECIDED','SUPERSEDED','FAILED','BLOCKED')),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  idempotency_key text not null,
  request_hash char(64) not null,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  unique (chain_id,product_id,objective,calculation_no),
  unique (created_by,idempotency_key), unique (id,chain_id,product_id)
);
create index forecast_calculations_scope on public.forecast_calculations(chain_id,product_id,objective,calculation_no desc);

create table public.forecast_calculation_horizons (
  id uuid primary key default gen_random_uuid(),
  calculation_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  horizon smallint not null check (horizon between 1 and 12),
  target_period date not null check (extract(day from target_period)=1),
  statistical_value numeric(18,3) not null check (statistical_value>=0),
  ml_value numeric(18,3) check (ml_value>=0),
  ensemble_value numeric(18,3) check (ensemble_value>=0),
  ensemble_ml_component numeric(18,3) check (ensemble_ml_component>=0),
  statistical_model text,
  ml_model text,
  ensemble_ml_model text,
  statistical_weight numeric(6,5) check (statistical_weight between 0 and 1),
  ml_weight numeric(6,5) check (ml_weight between 0 and 1),
  p10 numeric(18,3), p50 numeric(18,3), p90 numeric(18,3), p95 numeric(18,3),
  band_basis text not null check (band_basis in ('PRODUCT','CATEGORY','CHAIN','INSUFFICIENT')),
  band_observations integer not null check (band_observations>=0),
  band_status text not null check (band_status in ('AVAILABLE','INSUFFICIENT_BAND_EVIDENCE')),
  created_at timestamptz not null default now(),
  foreign key (calculation_id,chain_id,product_id) references public.forecast_calculations(id,chain_id,product_id) on delete restrict,
  unique (calculation_id,horizon), unique (calculation_id,target_period),
  constraint e4_band_shape check (
    (band_basis='INSUFFICIENT' and band_status='INSUFFICIENT_BAND_EVIDENCE' and
      p10 is null and p50 is null and p90 is null and p95 is null)
    or (band_basis<>'INSUFFICIENT' and band_status='AVAILABLE' and band_observations>=3 and
      p10 is not null and p50 is not null and p90 is not null and p95 is not null and
      p10>=0 and p10<=p50 and p50<=p90 and p90<=p95)),
  constraint e4_ensemble_shape check (
    (ensemble_value is null and ensemble_ml_component is null and ensemble_ml_model is null and
      statistical_weight is null and ml_weight is null)
    or (ensemble_value is not null and ensemble_ml_component is not null and
      nullif(btrim(ensemble_ml_model),'') is not null and statistical_weight is not null and
      ml_weight is not null and statistical_weight+ml_weight=1 and
      abs(ensemble_value-(statistical_value*statistical_weight+ensemble_ml_component*ml_weight))<=0.02))
);
create index forecast_calculation_horizons_target on public.forecast_calculation_horizons(chain_id,product_id,target_period);

create table public.forecast_selection_events (
  id uuid primary key default gen_random_uuid(),
  calculation_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  system_suggestion text,
  selected_candidate text not null check (selected_candidate in ('STATISTICAL','ML','ENSEMBLE')),
  selected_curve jsonb not null check (jsonb_typeof(selected_curve)='array' and jsonb_array_length(selected_curve)=12),
  decision_reason text,
  comment text,
  selected_by uuid not null references auth.users(id) on delete restrict,
  selected_at timestamptz not null default now(),
  supersedes_selection_id uuid,
  idempotency_key text not null,
  request_hash char(64) not null,
  foreign key (calculation_id,chain_id,product_id) references public.forecast_calculations(id,chain_id,product_id) on delete restrict,
  foreign key (supersedes_selection_id,chain_id,product_id) references public.forecast_selection_events(id,chain_id,product_id) on delete restrict,
  unique (selected_by,idempotency_key), unique (id,chain_id,product_id)
);
create index forecast_selection_scope on public.forecast_selection_events(chain_id,product_id,selected_at desc,id desc);

create table public.forecast_capture_sessions (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  product_id uuid not null,
  period date not null check (extract(day from period)=1),
  order_value numeric(18,3) not null check (order_value>=0),
  sale_value numeric(18,3) not null check (sale_value>=0),
  delivery_value numeric(18,3) not null check (delivery_value>=0),
  notes text,
  correction_reason text,
  status text not null default 'DRAFT' check (status in ('DRAFT','CONFIRMED')),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  confirmed_by uuid references auth.users(id) on delete restrict,
  confirmed_at timestamptz,
  source_batch_id uuid,
  draft_key text not null,
  confirm_key text,
  request_hash char(64) not null,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (source_batch_id,chain_id) references public.import_batches(id,chain_id) on delete restrict,
  unique (created_by,draft_key), unique (confirmed_by,confirm_key),
  constraint e4_capture_confirmation_shape check ((status='DRAFT' and confirmed_by is null and confirmed_at is null and source_batch_id is null)
    or (status='CONFIRMED' and confirmed_by is not null and confirmed_at is not null and source_batch_id is not null))
);

create table public.forecast_live_evaluations (
  id uuid primary key default gen_random_uuid(),
  calculation_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  horizon smallint not null check (horizon between 1 and 12),
  target_period date not null,
  candidate text not null check (candidate in ('STATISTICAL','ML','ENSEMBLE','TOWELL_SELECTED')),
  forecast_value numeric(18,3) not null check (forecast_value>=0),
  actual_sale numeric(18,3) not null check (actual_sale>=0),
  absolute_error numeric(18,3) not null check (absolute_error>=0),
  signed_error numeric(18,3) not null,
  ape numeric check (ape>=0),
  sale_observation_id uuid not null,
  sale_version_no integer not null check (sale_version_no>0),
  order_observation_id uuid,
  delivery_observation_id uuid,
  order_value numeric(18,3) check (order_value>=0),
  delivery_value numeric(18,3) check (delivery_value>=0),
  selected_event_id uuid references public.forecast_selection_events(id) on delete restrict,
  evaluated_at timestamptz not null default now(),
  foreign key (calculation_id,chain_id,product_id) references public.forecast_calculations(id,chain_id,product_id) on delete restrict,
  foreign key (calculation_id,horizon) references public.forecast_calculation_horizons(calculation_id,horizon) on delete restrict,
  foreign key (sale_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  foreign key (order_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  foreign key (delivery_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  unique (calculation_id,horizon,candidate,sale_observation_id),
  constraint e4_live_context_shape check ((order_observation_id is null)=(order_value is null) and
    (delivery_observation_id is null)=(delivery_value is null)),
  constraint e4_live_error_arithmetic check (absolute_error=abs(actual_sale-forecast_value) and
    signed_error=actual_sale-forecast_value and
    ((actual_sale=0 and ape is null) or (actual_sale>0 and ape=abs(actual_sale-forecast_value)/actual_sale*100)))
);
create index forecast_live_scope on public.forecast_live_evaluations(chain_id,product_id,horizon,target_period);

create table public.forecast_learning_events (
  id uuid primary key default gen_random_uuid(),
  calculation_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  horizon smallint not null check (horizon between 1 and 12),
  target_period date not null,
  sale_observation_id uuid not null,
  sale_version_no integer not null check (sale_version_no>0),
  suggested_candidate text,
  selected_candidate text,
  best_candidate_actual text check (best_candidate_actual in ('STATISTICAL','ML','ENSEMBLE')),
  order_value numeric(18,3), sale_value numeric(18,3) not null, delivery_value numeric(18,3),
  decision_regret_absolute numeric(18,3),
  decision_value_added_absolute numeric(18,3),
  signals_json jsonb not null check (jsonb_typeof(signals_json)='array'),
  metrics_json jsonb not null check (jsonb_typeof(metrics_json)='object'),
  created_at timestamptz not null default now(),
  foreign key (calculation_id,chain_id,product_id) references public.forecast_calculations(id,chain_id,product_id) on delete restrict,
  foreign key (calculation_id,horizon) references public.forecast_calculation_horizons(calculation_id,horizon) on delete restrict,
  foreign key (sale_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  unique (calculation_id,horizon,sale_observation_id)
);

create table public.forecast_live_closures (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  product_id uuid not null,
  target_period date not null,
  sale_observation_id uuid not null,
  evaluated_by uuid not null references auth.users(id) on delete restrict,
  idempotency_key text not null,
  evaluated_at timestamptz not null default now(),
  result_json jsonb not null check (jsonb_typeof(result_json)='object'),
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (sale_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  unique (evaluated_by,idempotency_key), unique (chain_id,product_id,target_period,sale_observation_id)
);

create function private.e4_horizon_lineage_guard() returns trigger language plpgsql set search_path='' as $$
declare issue date;
begin
  select issue_period into issue from public.forecast_calculations where id=new.calculation_id;
  if issue is null or new.target_period<>(issue+make_interval(months=>new.horizon))::date then
    raise exception 'calculation horizon lineage mismatch' using errcode='23514';
  end if;
  return new;
end $$;
create trigger e4_horizon_lineage before insert on public.forecast_calculation_horizons
  for each row execute function private.e4_horizon_lineage_guard();

create function private.e4_actual_lineage_guard() returns trigger language plpgsql set search_path='' as $$
declare actual public.monthly_observations%rowtype; context_row public.monthly_observations%rowtype;
declare target date; actual_value numeric;
begin
  select * into actual from public.monthly_observations where id=new.sale_observation_id;
  select target_period into target from public.forecast_calculation_horizons
    where calculation_id=new.calculation_id and horizon=new.horizon;
  actual_value:=coalesce((to_jsonb(new)->>'actual_sale')::numeric,(to_jsonb(new)->>'sale_value')::numeric);
  if actual.id is null or actual.chain_id<>new.chain_id or actual.product_id<>new.product_id or
    actual.metric_code<>'SALES' or actual.period<>new.target_period or actual.period<>target or
    actual.value<>actual_value or actual.version_no<>new.sale_version_no then
    raise exception 'LIVE sale lineage mismatch' using errcode='23514';
  end if;
  if tg_table_name='forecast_live_evaluations' then
    if new.order_observation_id is not null then
      select * into context_row from public.monthly_observations where id=new.order_observation_id;
      if context_row.chain_id<>new.chain_id or context_row.product_id<>new.product_id or
        context_row.period<>new.target_period or context_row.metric_code<>'ORDER' or
        context_row.value<>new.order_value then
        raise exception 'LIVE order lineage mismatch' using errcode='23514';
      end if;
    end if;
    if new.delivery_observation_id is not null then
      select * into context_row from public.monthly_observations where id=new.delivery_observation_id;
      if context_row.chain_id<>new.chain_id or context_row.product_id<>new.product_id or
        context_row.period<>new.target_period or context_row.metric_code<>'DELIVERY' or
        context_row.value<>new.delivery_value then
        raise exception 'LIVE delivery lineage mismatch' using errcode='23514';
      end if;
    end if;
  end if;
  return new;
end $$;
create trigger e4_live_lineage before insert on public.forecast_live_evaluations
  for each row execute function private.e4_actual_lineage_guard();
create trigger e4_learning_lineage before insert on public.forecast_learning_events
  for each row execute function private.e4_actual_lineage_guard();

-- RLS is enabled even before flags are turned on. Browser clients can read only
-- authorized chains and cannot write these tables directly.
do $$ declare t text; begin
  foreach t in array array['forecast_calculations','forecast_calculation_horizons',
    'forecast_selection_events','forecast_capture_sessions','forecast_live_evaluations','forecast_learning_events',
    'forecast_live_closures'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on public.%I from public,anon,authenticated',t);
    execute format('grant select on public.%I to authenticated',t);
    execute format('create policy %I on public.%I for select to authenticated using (private.can_chain(chain_id,''view''))',t||'_read',t);
    execute format('create trigger %I before update or delete on public.%I for each row execute function public.contract_insert_only()',t||'_immutable',t);
  end loop;
end $$;
grant select,insert,update on public.forecast_calculations,public.forecast_capture_sessions to service_role;
grant select,insert on public.forecast_calculation_horizons,public.forecast_selection_events,
  public.forecast_live_evaluations,public.forecast_learning_events,public.forecast_live_closures to service_role;
-- Only calculation status and draft confirmation may change; all mathematical
-- columns remain frozen. These two guards replace the generic insert-only ones.
drop trigger forecast_calculations_immutable on public.forecast_calculations;
create function private.e4_calculation_state_guard() returns trigger language plpgsql set search_path='' as $$
begin
  if tg_op='DELETE' or (to_jsonb(old)-'status')<>(to_jsonb(new)-'status') or not (
    (old.status='READY_FOR_DECISION' and new.status='DECIDED') or
    (old.status='DECIDED' and new.status='SUPERSEDED')) then
    raise exception 'calculation snapshot is immutable' using errcode='23514';
  end if;
  return new;
end $$;
create trigger forecast_calculations_state before update or delete on public.forecast_calculations
  for each row execute function private.e4_calculation_state_guard();
drop trigger forecast_capture_sessions_immutable on public.forecast_capture_sessions;
create function private.e4_capture_state_guard() returns trigger language plpgsql set search_path='' as $$
begin
  if tg_op='DELETE' or old.status<>'DRAFT' or new.status<>'CONFIRMED' or
    (to_jsonb(old)-'status'-'confirmed_by'-'confirmed_at'-'source_batch_id'-'confirm_key')<>
    (to_jsonb(new)-'status'-'confirmed_by'-'confirmed_at'-'source_batch_id'-'confirm_key') then
    raise exception 'capture is immutable' using errcode='23514';
  end if;
  return new;
end $$;
create trigger forecast_capture_sessions_confirm before update or delete on public.forecast_capture_sessions
  for each row execute function private.e4_capture_state_guard();

create view public.current_forecast_selection with (security_invoker=true) as
select distinct on (s.chain_id,s.product_id,c.objective)
  s.*,c.objective,c.calculation_no,c.calculation_code,c.issue_period
from public.forecast_selection_events s
join public.forecast_calculations c on c.id=s.calculation_id and c.chain_id=s.chain_id
order by s.chain_id,s.product_id,c.objective,s.selected_at desc,s.id desc;
revoke all on public.current_forecast_selection from public,anon,authenticated;
grant select on public.current_forecast_selection to authenticated;

-- A service-role credential alone is not an actor. Every transaction also checks
-- the already verified auth.users identity and its current chain authorization.
create function private.e4_actor_allowed(p_actor uuid,p_chain uuid,p_action text) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.profiles p where p.id=p_actor and p.status='ACTIVE' and
    (p.global_role='ADMIN' or (p.global_role='EDITOR' and p_action in ('calculate','capture') and
      exists(select 1 from public.user_chain_access a where a.user_id=p_actor and a.chain_id=p_chain and
        a.can_view and case p_action when 'calculate' then a.can_run_forecast when 'capture' then a.can_edit else false end))));
$$;
revoke all on function private.e4_actor_allowed(uuid,uuid,text) from public,anon,authenticated;
grant usage on schema private to service_role;
grant execute on function private.e4_actor_allowed(uuid,uuid,text) to service_role;

-- C1/C2 numbering and twelve rows are committed atomically under a scope lock.
create function public.e4_create_calculation(p jsonb) returns uuid
language plpgsql security invoker set search_path='' as $$
declare actor uuid; chain uuid; product uuid; existing public.forecast_calculations%rowtype;
declare calc uuid; number integer; item jsonb; request_digest text; key text; issue date;
begin
  actor:=(p->>'actor_id')::uuid; chain:=(p->>'chain_id')::uuid; product:=(p->>'product_id')::uuid;
  key:=p->>'idempotency_key'; issue:=(p->>'issue_period')::date;
  request_digest:=encode(sha256(convert_to((p-'idempotency_key')::text,'UTF8')),'hex');
  if not private.e4_actor_allowed(actor,chain,'calculate') or key is null or length(key) not between 1 and 128 or
    issue is null or extract(day from issue)<>1 or p->>'objective'<>'Venta' or
    p->>'data_snapshot_hash' !~ '^[0-9a-f]{64}$' or p->>'content_hash' !~ '^[0-9a-f]{64}$' or
    p->>'evidence_mode' not in ('POINT_IN_TIME','RETROSPECTIVE_TRAINING') or
    jsonb_typeof(p->'horizons')<>'array' or jsonb_array_length(p->'horizons')<>12 or
    not exists(select 1 from public.products where id=product and chain_id=chain) then
    raise exception 'invalid calculation request' using errcode='23514';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(chain::text||product::text||'Venta',0));
  select * into existing from public.forecast_calculations where created_by=actor and idempotency_key=key;
  if found then
    if existing.request_hash<>request_digest then raise exception 'idempotency payload conflict' using errcode='23514'; end if;
    return existing.id;
  end if;
  select coalesce(max(calculation_no),0)+1 into number from public.forecast_calculations
    where chain_id=chain and product_id=product and objective='Venta';
  if number>1 and nullif(btrim(p->>'recalculation_reason'),'') is null then
    raise exception 'recalculation reason required' using errcode='23514';
  end if;
  insert into public.forecast_calculations(chain_id,product_id,objective,calculation_no,calculation_code,
    issue_period,cutoff_at,preview_id,data_snapshot_hash,content_hash,input_snapshot,candidate_metrics,
    research_snapshot,suggested_reference,scope_leader,evidence_mode,engine_version,git_sha,recalculation_reason,
    created_by,idempotency_key,request_hash)
  values(chain,product,'Venta',number,'C'||number,issue,(p->>'cutoff_at')::timestamptz,
    p->>'preview_id',p->>'data_snapshot_hash',p->>'content_hash',
    jsonb_build_object('product',p->'product_snapshot','observation_ids',p->'input_observation_ids'),
    p->'candidates',p->'research_snapshot',p->'suggested_reference',p->'scope_leader',
    p->>'evidence_mode',p->>'engine_version',p->>'git_sha',p->>'recalculation_reason',actor,key,request_digest)
  returning id into calc;
  for item in select value from jsonb_array_elements(p->'horizons') loop
    if (item->>'horizon')::integer not between 1 and 12 or
      (item->>'target_period')::date<>(issue+make_interval(months=>(item->>'horizon')::integer))::date then
      raise exception 'invalid horizon lineage' using errcode='23514';
    end if;
    insert into public.forecast_calculation_horizons(calculation_id,chain_id,product_id,horizon,target_period,
      statistical_value,ml_value,ensemble_value,ensemble_ml_component,statistical_model,ml_model,
      ensemble_ml_model,statistical_weight,ml_weight,
      p10,p50,p90,p95,band_basis,band_observations,band_status)
    values(calc,chain,product,(item->>'horizon')::smallint,(item->>'target_period')::date,
      (item->>'statistical_value')::numeric,(item->>'ml_value')::numeric,(item->>'ensemble_value')::numeric,
      (item->>'ensemble_ml_component')::numeric,item->>'statistical_model',item->>'ml_model',
      item->>'ensemble_ml_model',(item->>'statistical_weight')::numeric,(item->>'ml_weight')::numeric,
      (item->>'p10')::numeric,(item->>'p50')::numeric,(item->>'p90')::numeric,(item->>'p95')::numeric,
      item->>'band_basis',(item->>'band_observations')::integer,item->>'band_status');
  end loop;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(chain,actor,case when number=1 then 'CREATE_CALCULATION' else 'RECALCULATE' end,
      'forecast_calculation',calc,jsonb_build_object('code','C'||number,'content_hash',p->>'content_hash'));
  return calc;
end $$;

create function public.e4_select_forecast(p jsonb) returns uuid
language plpgsql security invoker set search_path='' as $$
declare c public.forecast_calculations%rowtype; prior public.forecast_selection_events%rowtype;
declare event_id uuid; actor uuid; chosen text; suggested text; curve jsonb; key text; request_digest text;
begin
  actor:=(p->>'actor_id')::uuid; chosen:=p->>'selected_candidate'; key:=p->>'idempotency_key';
  select * into c from public.forecast_calculations where id=(p->>'calculation_id')::uuid for update;
  request_digest:=encode(sha256(convert_to((p-'idempotency_key')::text,'UTF8')),'hex');
  select * into prior from public.forecast_selection_events where selected_by=actor and idempotency_key=key;
  if found then
    if prior.request_hash<>request_digest then raise exception 'idempotency payload conflict' using errcode='23514'; end if;
    return prior.id;
  end if;
  if c.id is null or not private.e4_actor_allowed(actor,c.chain_id,'select') or
    c.status not in ('READY_FOR_DECISION','DECIDED') or chosen not in ('STATISTICAL','ML','ENSEMBLE') or key is null then
    raise exception 'selection not authorized or ready' using errcode='23514';
  end if;
  suggested:=upper(c.suggested_reference->>'family');
  if chosen is distinct from suggested and p->>'decision_reason' not in
    ('Información directa del cliente','Promoción comercial','Cambio de precio','Desabasto esperado',
     'Apertura/cierre de tiendas','Cambio de distribución','Pedido extraordinario','Evento especial',
     'Experiencia comercial','Otro') then
    raise exception 'decision reason required' using errcode='23514';
  end if;
  if p->>'decision_reason'='Otro' and nullif(btrim(p->>'comment'),'') is null then
    raise exception 'comment required' using errcode='23514';
  end if;
  select jsonb_agg(jsonb_build_object('horizon',h.horizon,'target_period',h.target_period,
    'value',case chosen when 'STATISTICAL' then h.statistical_value when 'ML' then h.ml_value else h.ensemble_value end)
    order by h.horizon) into curve
  from public.forecast_calculation_horizons h where h.calculation_id=c.id and
    (case chosen when 'STATISTICAL' then h.statistical_value when 'ML' then h.ml_value else h.ensemble_value end) is not null;
  if jsonb_array_length(coalesce(curve,'[]'::jsonb))<>12 then
    raise exception 'selected curve incomplete' using errcode='23514';
  end if;
  select s.* into prior from public.forecast_selection_events s
    join public.forecast_calculations old on old.id=s.calculation_id
    where s.chain_id=c.chain_id and s.product_id=c.product_id and old.objective=c.objective
    order by s.selected_at desc,s.id desc limit 1;
  insert into public.forecast_selection_events(calculation_id,chain_id,product_id,system_suggestion,selected_candidate,
    selected_curve,decision_reason,comment,selected_by,supersedes_selection_id,idempotency_key,request_hash)
  values(c.id,c.chain_id,c.product_id,suggested,chosen,curve,p->>'decision_reason',p->>'comment',actor,prior.id,key,request_digest)
  returning id into event_id;
  if prior.id is not null and prior.calculation_id<>c.id then
    update public.forecast_calculations set status='SUPERSEDED' where id=prior.calculation_id and status='DECIDED';
  end if;
  if c.status='READY_FOR_DECISION' then update public.forecast_calculations set status='DECIDED' where id=c.id; end if;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,old_data,new_data)
    values(c.chain_id,actor,case when prior.id is null then 'SELECT_FORECAST' else 'CHANGE_SELECTION' end,
      'forecast_selection_event',event_id,jsonb_build_object('selection_id',prior.id),
      jsonb_build_object('calculation_id',c.id,'candidate',chosen));
  return event_id;
end $$;

-- Evaluate every calculation whose original target matches the confirmed sale.
-- WAPE/FILL RATE are ratios of sums in the read endpoint; single-row APE is not WAPE.
create function public.e4_close_live(p_chain uuid,p_product uuid,p_period date,p_actor uuid,p_key text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare sale public.monthly_observations%rowtype; ordered public.monthly_observations%rowtype;
declare delivered public.monthly_observations%rowtype; h record; choice public.forecast_selection_events%rowtype;
declare v_candidate text; amount numeric; best text; best_error numeric; suggested_error numeric;
declare selected_error numeric; signals jsonb; event_count integer:=0; eval_count integer:=0;
declare result jsonb; prior public.forecast_live_closures%rowtype; closure_id uuid;
declare first_sale_available_at timestamptz;
begin
  if not private.e4_actor_allowed(p_actor,p_chain,'close') or p_key is null or length(p_key) not between 1 and 128 or
    p_period is null or extract(day from p_period)<>1 then
    raise exception 'live close not authorized' using errcode='23514';
  end if;
  select o.* into sale from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='SALES'
      and b.status='IMPORTED' and o.available_at is not null
    order by o.version_no desc limit 1;
  if sale.id is null then raise exception 'confirmed sale required' using errcode='23514'; end if;
  select min(o.available_at) into first_sale_available_at
    from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period
      and o.metric_code='SALES' and b.status='IMPORTED' and o.available_at is not null;
  perform pg_advisory_xact_lock(hashtextextended(p_chain::text||p_product::text||p_period::text||sale.id::text,2));
  select * into prior from public.forecast_live_closures where evaluated_by=p_actor and idempotency_key=p_key;
  if found then
    if prior.chain_id<>p_chain or prior.product_id<>p_product or prior.target_period<>p_period or
      prior.sale_observation_id<>sale.id then raise exception 'idempotency payload conflict' using errcode='23514'; end if;
    return prior.result_json;
  end if;
  select * into prior from public.forecast_live_closures where chain_id=p_chain and product_id=p_product
    and target_period=p_period and sale_observation_id=sale.id;
  if found then return prior.result_json; end if;
  select o.* into ordered from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='ORDER' and b.status='IMPORTED'
    order by o.version_no desc limit 1;
  select o.* into delivered from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='DELIVERY' and b.status='IMPORTED'
    order by o.version_no desc limit 1;
  for h in select c.id as calculation_id,c.suggested_reference,c.chain_id,c.product_id,
      x.horizon,x.target_period,x.statistical_value,x.ml_value,x.ensemble_value
      from public.forecast_calculations c join public.forecast_calculation_horizons x on x.calculation_id=c.id
      where c.chain_id=p_chain and c.product_id=p_product and c.objective='Venta' and x.target_period=p_period
      and c.status in ('READY_FOR_DECISION','DECIDED','SUPERSEDED') loop
    -- A decision made after the FIRST real version became available is
    -- hindsight, including when the actual is subsequently corrected.
    -- Keep the frozen candidate evaluations but do not attribute Towell to it.
    select * into choice from public.forecast_selection_events where calculation_id=h.calculation_id
      and selected_at <= first_sale_available_at
      order by selected_at desc,id desc limit 1;
    best:=null; best_error:=null; suggested_error:=null; selected_error:=null;
    foreach v_candidate in array array['STATISTICAL','ML','ENSEMBLE','TOWELL_SELECTED'] loop
      amount:=case v_candidate when 'STATISTICAL' then h.statistical_value when 'ML' then h.ml_value
        when 'ENSEMBLE' then h.ensemble_value when 'TOWELL_SELECTED' then
          case choice.selected_candidate when 'STATISTICAL' then h.statistical_value
            when 'ML' then h.ml_value when 'ENSEMBLE' then h.ensemble_value end end;
      if amount is null then continue; end if;
      insert into public.forecast_live_evaluations(calculation_id,chain_id,product_id,horizon,target_period,
        candidate,forecast_value,actual_sale,absolute_error,signed_error,ape,sale_observation_id,
        sale_version_no,order_observation_id,delivery_observation_id,order_value,delivery_value,selected_event_id)
      values(h.calculation_id,p_chain,p_product,h.horizon,p_period,v_candidate,amount,sale.value,
        abs(sale.value-amount),sale.value-amount,case when sale.value>0 then abs(sale.value-amount)/sale.value*100 end,
        sale.id,sale.version_no,ordered.id,delivered.id,ordered.value,delivered.value,
        case when v_candidate='TOWELL_SELECTED' then choice.id end)
      on conflict (calculation_id,horizon,candidate,sale_observation_id) do nothing;
      eval_count:=eval_count+1;
      if v_candidate<>'TOWELL_SELECTED' and (best_error is null or abs(sale.value-amount)<best_error) then
        best:=v_candidate; best_error:=abs(sale.value-amount);
      end if;
      if v_candidate=upper(h.suggested_reference->>'family') then suggested_error:=abs(sale.value-amount); end if;
      if v_candidate='TOWELL_SELECTED' then selected_error:=abs(sale.value-amount); end if;
    end loop;
    signals:='[]'::jsonb;
    if selected_error is not null and selected_error>0 then
      signals:=signals||'"DEMAND_FORECAST_ERROR"'::jsonb;
    end if;
    if ordered.id is not null and ordered.value<>sale.value then
      signals:=signals||'"ORDER_DEVIATION"'::jsonb;
    end if;
    if delivered.id is not null and delivered.value>sale.value then
      signals:=signals||'"SELL_THROUGH_GAP"'::jsonb;
    end if;
    if ordered.id is not null and delivered.id is not null and delivered.value<ordered.value then
      signals:=signals||'"SUPPLY_SHORTFALL"'::jsonb;
    end if;
    if ordered.id is not null and delivered.id is not null and ordered.value>0 and delivered.value/ordered.value<0.95 then
      signals:=signals||'"LOW_FILL_RATE"'::jsonb;
    end if;
    if selected_error is not null and suggested_error is not null and
      choice.selected_candidate is distinct from upper(h.suggested_reference->>'family') then
      signals:=signals||to_jsonb(case when suggested_error>selected_error then
        'DECISION_OUTPERFORMED_RECOMMENDATION' else 'DECISION_UNDERPERFORMED_RECOMMENDATION' end);
    end if;
    insert into public.forecast_learning_events(calculation_id,chain_id,product_id,horizon,target_period,
      sale_observation_id,sale_version_no,suggested_candidate,selected_candidate,best_candidate_actual,
      order_value,sale_value,delivery_value,decision_regret_absolute,decision_value_added_absolute,
      signals_json,metrics_json)
    values(h.calculation_id,p_chain,p_product,h.horizon,p_period,sale.id,sale.version_no,
      upper(h.suggested_reference->>'family'),choice.selected_candidate,best,ordered.value,sale.value,delivered.value,
      case when selected_error is not null then selected_error-best_error end,
      case when choice.selected_candidate is distinct from upper(h.suggested_reference->>'family') and
        suggested_error is not null and selected_error is not null then suggested_error-selected_error end,
      signals,jsonb_build_object('actual_sale',sale.value,'actual_order',ordered.value,
        'actual_delivery',delivered.value,'statistical_forecast',h.statistical_value,
        'ml_forecast',h.ml_value,'ensemble_forecast',h.ensemble_value,
        'towell_forecast',case choice.selected_candidate when 'STATISTICAL' then h.statistical_value
          when 'ML' then h.ml_value when 'ENSEMBLE' then h.ensemble_value end,
        'statistical_absolute_error',abs(sale.value-h.statistical_value),
        'ml_absolute_error',case when h.ml_value is not null then abs(sale.value-h.ml_value) end,
        'ensemble_absolute_error',case when h.ensemble_value is not null then abs(sale.value-h.ensemble_value) end,
        'best_absolute_error',best_error,'selected_absolute_error',selected_error,
        'fill_rate',case when ordered.value>0 and delivered.id is not null then delivered.value/ordered.value*100 end,
        'order_observation_id',ordered.id,'delivery_observation_id',delivered.id))
    on conflict (calculation_id,horizon,sale_observation_id) do nothing;
    event_count:=event_count+1;
  end loop;
  result:=jsonb_build_object('status','CLOSED','period',p_period,'sale_observation_id',sale.id,
    'evaluations',eval_count,'learning_events',event_count);
  insert into public.forecast_live_closures(chain_id,product_id,target_period,sale_observation_id,
    evaluated_by,idempotency_key,result_json)
    values(p_chain,p_product,p_period,sale.id,p_actor,p_key,result) returning id into closure_id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(p_chain,p_actor,'LIVE_EVALUATION','forecast_live_closure',closure_id,result);
  return result;
end $$;

create function public.e4_save_capture(p jsonb) returns uuid
language plpgsql security invoker set search_path='' as $$
declare actor uuid; chain uuid; product uuid; existing public.forecast_capture_sessions%rowtype;
declare session_id uuid; digest_value text; key text; period_value date;
begin
  actor:=(p->>'actor_id')::uuid; chain:=(p->>'chain_id')::uuid; product:=(p->>'product_id')::uuid;
  key:=p->>'idempotency_key'; period_value:=(p->>'period')::date;
  digest_value:=encode(sha256(convert_to((p-'idempotency_key')::text,'UTF8')),'hex');
  if not private.e4_actor_allowed(actor,chain,'capture') or key is null or
    extract(day from period_value)<>1 or period_value>date_trunc('month',now())::date or
    (p->>'order_value')::numeric<0 or (p->>'sale_value')::numeric<0 or (p->>'delivery_value')::numeric<0 or
    not exists(select 1 from public.products where id=product and chain_id=chain) then
    raise exception 'invalid capture' using errcode='23514';
  end if;
  select * into existing from public.forecast_capture_sessions where created_by=actor and draft_key=key;
  if found then
    if existing.request_hash<>digest_value then raise exception 'idempotency payload conflict' using errcode='23514'; end if;
    return existing.id;
  end if;
  insert into public.forecast_capture_sessions(chain_id,product_id,period,order_value,sale_value,delivery_value,
    notes,correction_reason,created_by,draft_key,request_hash)
  values(chain,product,period_value,(p->>'order_value')::numeric,(p->>'sale_value')::numeric,
    (p->>'delivery_value')::numeric,p->>'notes',p->>'correction_reason',actor,key,digest_value)
  returning id into session_id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(chain,actor,'CAPTURE_OBSERVATION','forecast_capture_session',session_id,
      jsonb_build_object('period',period_value,'status','DRAFT'));
  return session_id;
end $$;

create function public.e4_confirm_capture(p_session uuid,p_actor uuid,p_key text) returns uuid
language plpgsql security invoker set search_path='' as $$
declare s public.forecast_capture_sessions%rowtype; v_profile_id uuid; profile_version uuid;
declare batch_id uuid; v_version_no integer; metric text; amount numeric; stamp timestamptz;
begin
  select * into s from public.forecast_capture_sessions where id=p_session for update;
  if s.id is null or not private.e4_actor_allowed(p_actor,s.chain_id,'capture') or p_key is null then
    raise exception 'capture not authorized' using errcode='23514';
  end if;
  if s.status='CONFIRMED' then
    if s.confirm_key<>p_key then raise exception 'capture already confirmed' using errcode='23514'; end if;
    return s.source_batch_id;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(s.chain_id::text||s.product_id::text||s.period::text,1));
  if exists(select 1 from public.monthly_observations where chain_id=s.chain_id and product_id=s.product_id
    and period=s.period and metric_code='SALES') and nullif(btrim(s.correction_reason),'') is null then
    raise exception 'correction reason required' using errcode='23514';
  end if;
  select id into v_profile_id from public.import_profiles where chain_id=s.chain_id and name='FORECAST_TOWELL_CAPTURE';
  if v_profile_id is null then
    insert into public.import_profiles(chain_id,name) values(s.chain_id,'FORECAST_TOWELL_CAPTURE') returning id into v_profile_id;
  end if;
  select ipv.id into profile_version from public.import_profile_versions ipv where ipv.profile_id=v_profile_id and ipv.version=1;
  if profile_version is null then
    insert into public.import_profile_versions(profile_id,chain_id,version,mapping_json,required_columns,created_by)
      values(v_profile_id,s.chain_id,1,'{"ORDER":"order_value","SALES":"sale_value","DELIVERY":"delivery_value"}'::jsonb,
        '["order_value","sale_value","delivery_value"]'::jsonb,p_actor) returning id into profile_version;
  end if;
  insert into public.import_batches(chain_id,profile_version_id,filename,sha256,period,uploaded_by)
    values(s.chain_id,profile_version,'app-capture-'||s.id,encode(sha256(convert_to(s.id::text,'UTF8')),'hex'),s.period,p_actor)
    returning id,uploaded_at into batch_id,stamp;
  update public.import_batches set status='VALIDATING',row_count=3,valid_rows=3 where id=batch_id;
  update public.import_batches set status='VALIDATED' where id=batch_id;
  update public.import_batches set status='CONFIRMED' where id=batch_id;
  foreach metric in array array['ORDER','SALES','DELIVERY'] loop
    select coalesce(max(o.version_no),0)+1 into v_version_no from public.monthly_observations o
      where o.chain_id=s.chain_id and o.product_id=s.product_id and o.period=s.period and o.metric_code=metric;
    amount:=case metric when 'ORDER' then s.order_value when 'SALES' then s.sale_value else s.delivery_value end;
    insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,
      available_at,availability_source,source_batch_id)
    values(s.chain_id,s.product_id,s.period,metric,amount,v_version_no,stamp,'SYSTEM_INGESTION',batch_id);
  end loop;
  update public.import_batches set status='IMPORTED' where id=batch_id;
  update public.forecast_capture_sessions set status='CONFIRMED',confirmed_by=p_actor,
    confirmed_at=stamp,source_batch_id=batch_id,confirm_key=p_key where id=s.id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(s.chain_id,p_actor,'CONFIRM_MONTH','forecast_capture_session',s.id,
      jsonb_build_object('batch_id',batch_id,'period',s.period));
  return batch_id;
end $$;

-- RPCs are never callable by a browser JWT. Service role is the only executor.
do $$ declare fn text; begin
  foreach fn in array array['e4_create_calculation(jsonb)','e4_select_forecast(jsonb)',
    'e4_save_capture(jsonb)','e4_confirm_capture(uuid,uuid,text)',
    'e4_close_live(uuid,uuid,date,uuid,text)'] loop
    execute format('revoke all on function public.%s from public,anon,authenticated',fn);
    execute format('grant execute on function public.%s to service_role',fn);
  end loop;
end $$;
notify pgrst, 'reload schema';
