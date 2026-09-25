-- PRD 06: monthly close, realized accuracy and continuous-learning evidence.
alter type public.period_status add value if not exists 'closing' after 'open';

do $$ begin
  create type public.period_closure_status as enum (
    'requested','closing','validation','closed','completed','completed_with_learning_error','reopened','failed_validation'
  );
exception when duplicate_object then null; end $$;

create table if not exists public.period_closures (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  period_id uuid not null references public.periods(id) on delete restrict,
  revision integer not null check (revision > 0),
  closure_key text not null,
  status public.period_closure_status not null default 'requested',
  source_contract text not null default 'normalized_platform_records',
  forecast_towell_id uuid references public.forecast_towell(id) on delete restrict,
  champion_forecast_towell_id uuid references public.forecast_towell(id) on delete restrict,
  requested_by uuid references public.users(id),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  closed_at timestamptz,
  completed_at timestamptz,
  validation jsonb not null default '{}'::jsonb,
  configuration jsonb not null default '{}'::jsonb,
  error_detail jsonb,
  previous_closure_id uuid references public.period_closures(id) on delete restrict,
  correction_reason text,
  unique(period_id,revision),
  unique(organization_id,closure_key),
  check ((revision=1 and previous_closure_id is null and correction_reason is null) or
         (revision>1 and previous_closure_id is not null and length(trim(correction_reason))>0))
);
create index if not exists period_closures_period_revision on public.period_closures(period_id,revision desc);
create index if not exists period_closures_status_queue on public.period_closures(status,requested_at) where status in ('requested','closing','validation','completed_with_learning_error');

create table if not exists public.closure_snapshots (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null unique references public.period_closures(id) on delete restrict,
  snapshot_hash text not null unique check (length(snapshot_hash)=64),
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.realized_metrics (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  product_id uuid references public.products(id),
  series_key text not null,
  category text not null default 'FENDI BD',
  color text,
  sale numeric(18,4) not null check (sale>=0),
  ordered numeric(18,4) not null check (ordered>=0),
  delivered numeric(18,4) not null check (delivered>=0),
  client_forecast numeric(18,4) check (client_forecast>=0),
  client_forecast_not_received boolean not null default false,
  active_at_close boolean not null default true,
  unique(period_closure_id,series_key),
  check (client_forecast is not null or client_forecast_not_received)
);

create table if not exists public.forecast_accuracy (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  engine text not null check (engine in ('client','statistical','ml','towell','challenger')),
  level text not null check (level in ('series','category','chain','total')),
  dimension_key text not null,
  product_id uuid references public.products(id),
  forecast_version text,
  actual numeric(18,4) not null,
  forecast numeric(18,4) not null,
  absolute_error numeric(18,4) not null,
  wape numeric(12,4) not null,
  bias numeric(12,4) not null,
  bias_direction text not null check (bias_direction in ('overforecast','underforecast','neutral')),
  observations integer not null check (observations>0),
  rolling_3_wape numeric(12,4),
  rolling_6_wape numeric(12,4),
  rolling_12_wape numeric(12,4),
  rolling_all_wape numeric(12,4),
  unique(period_closure_id,engine,level,dimension_key)
);

create table if not exists public.fill_rate_metrics (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  level text not null check (level in ('series','category','chain','total')),
  dimension_key text not null,
  product_id uuid references public.products(id),
  ordered numeric(18,4) not null,
  delivered numeric(18,4) not null,
  sale numeric(18,4) not null,
  fill_rate numeric(12,4),
  sale_to_order numeric(12,4),
  cause_attributed boolean not null default false,
  unique(period_closure_id,level,dimension_key),
  check (cause_attributed=false)
);

create table if not exists public.challenger_validation (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  champion_engine text not null,
  challenger_engine text not null,
  champion_wape numeric(12,4) not null,
  challenger_wape numeric(12,4) not null,
  improvement_points numeric(12,4) not null,
  consecutive_wins integer not null default 0,
  required_consecutive_wins integer not null default 3,
  stable boolean not null,
  bias_controlled boolean not null,
  critical_horizons_pass boolean not null,
  sufficient_observations boolean not null,
  state text not null check (state in ('detected','in_validation','evidence_sufficient','candidate_for_promotion','promoted','not_consistent','rejected')),
  automatic_promotion boolean not null default false,
  evidence jsonb not null default '{}'::jsonb,
  unique(period_closure_id,challenger_engine),
  check (automatic_promotion=false)
);

create table if not exists public.horizon_accuracy (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  engine text not null check (engine in ('client','statistical','ml','towell','challenger')),
  horizon integer not null check (horizon between 1 and 12),
  observations integer not null check (observations>0),
  wape numeric(12,4) not null,
  bias numeric(12,4) not null,
  unique(period_closure_id,engine,horizon)
);

create table if not exists public.interval_coverage (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  horizon integer not null check (horizon between 1 and 12),
  observations integer not null check (observations>0),
  coverage_p90 numeric(12,4) not null check (coverage_p90 between 0 and 100),
  coverage_p95 numeric(12,4) not null check (coverage_p95 between 0 and 100),
  below_p10 integer not null default 0,
  at_or_below_p50 integer not null default 0,
  outside_p95 integer not null default 0,
  calibration_state text not null check (calibration_state in ('insufficient','underestimated','calibrated','too_wide')),
  evidence jsonb not null default '{}'::jsonb,
  unique(period_closure_id,horizon)
);

create table if not exists public.learning_events (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  event_type text not null check (event_type in ('actuals_appended','persistent_bias','drift_detected','retraining_requested','bimonthly_review','challenger_state_changed','next_cycle_prepared','closure_corrected','learning_failed','learning_reprocessed')),
  status text not null check (status in ('observed','queued','processing','completed','failed','ready')),
  severity text not null default 'info' check (severity in ('info','warning','significant','blocking')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create table if not exists public.period_reopen_requests (
  id uuid primary key default gen_random_uuid(),
  period_id uuid not null references public.periods(id) on delete restrict,
  closure_id uuid not null references public.period_closures(id) on delete restrict,
  requested_by uuid not null references public.users(id),
  reason text not null check (length(trim(reason))>0),
  requested_at timestamptz not null default now()
);

create table if not exists public.closure_audit_log (
  id bigserial primary key,
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  actor_id uuid references public.users(id),
  action text not null,
  previous_value jsonb,
  new_value jsonb,
  reason text,
  occurred_at timestamptz not null default now()
);

create or replace function public.guard_immutable_closure_evidence()
returns trigger language plpgsql as $$
begin
  raise exception 'closure_evidence_is_immutable';
end $$;

do $$ declare table_name text;
begin
  foreach table_name in array array['closure_snapshots','realized_metrics','forecast_accuracy','fill_rate_metrics','challenger_validation','horizon_accuracy','interval_coverage','closure_audit_log']
  loop
    execute format('drop trigger if exists guard_immutable_closure_evidence on public.%I',table_name);
    execute format('create trigger guard_immutable_closure_evidence before update or delete on public.%I for each row execute function public.guard_immutable_closure_evidence()',table_name);
  end loop;
end $$;

create or replace function public.request_period_closure(p_period_id uuid, p_correction_reason text default null, p_client_forecast_not_received boolean default false)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_user public.users%rowtype; v_role text; v_period public.periods%rowtype; v_chain uuid; v_revision integer; v_previous uuid; v_closure uuid;
begin
  select u.*,r.code into v_user,v_role from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role not in ('manager','editor') then raise exception 'write_role_required'; end if;
  select * into v_period from public.periods where id=p_period_id and organization_id=v_user.organization_id for update;
  if v_period.id is null then raise exception 'period_not_found'; end if;
  if v_period.status not in ('open','reopened') then raise exception 'period_not_available_for_closure'; end if;
  select id into v_chain from public.chains where organization_id=v_user.organization_id order by name limit 1;
  select coalesce(max(revision),0)+1 into v_revision from public.period_closures where period_id=p_period_id;
  select id into v_previous from public.period_closures where period_id=p_period_id and status in ('closed','completed','completed_with_learning_error') order by revision desc limit 1;
  if v_revision>1 and (v_previous is null or p_correction_reason is null or length(trim(p_correction_reason))=0) then raise exception 'correction_reason_required'; end if;
  insert into public.period_closures(organization_id,chain_id,period_id,revision,closure_key,requested_by,previous_closure_id,correction_reason,configuration)
    values(v_user.organization_id,v_chain,p_period_id,v_revision,'CLOSE-FENDI-'||to_char(v_period.period_start,'YYYY-MM')||'-R'||lpad(v_revision::text,2,'0'),v_user.id,v_previous,case when v_revision>1 then p_correction_reason else null end,jsonb_build_object('client_forecast_not_received',p_client_forecast_not_received))
    returning id into v_closure;
  update public.periods set status='closing' where id=p_period_id;
  insert into public.closure_audit_log(period_closure_id,actor_id,action,new_value,reason)
    values(v_closure,v_user.id,'closure.requested',jsonb_build_object('revision',v_revision,'period',v_period.period_start),p_correction_reason);
  insert into public.domain_events(event_type,aggregate_type,aggregate_id,payload,actor_id)
    values('period.closure_requested','period_closure',v_closure,jsonb_build_object('period_id',p_period_id,'revision',v_revision),v_user.id);
  return v_closure;
end $$;
grant execute on function public.request_period_closure(uuid,text,boolean) to authenticated;

create or replace function public.request_period_reopening(p_period_id uuid, p_reason text)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_user public.users%rowtype; v_role text; v_closure uuid; v_request uuid;
begin
  select u.*,r.code into v_user,v_role from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role <> 'manager' then raise exception 'manager_role_required'; end if;
  if p_reason is null or length(trim(p_reason))=0 then raise exception 'reopen_reason_required'; end if;
  select pc.id into v_closure from public.period_closures pc join public.periods p on p.id=pc.period_id
    where pc.period_id=p_period_id and p.organization_id=v_user.organization_id and pc.status in ('closed','completed','completed_with_learning_error') order by pc.revision desc limit 1;
  if v_closure is null then raise exception 'closed_period_not_found'; end if;
  insert into public.period_reopen_requests(period_id,closure_id,requested_by,reason) values(p_period_id,v_closure,v_user.id,p_reason) returning id into v_request;
  update public.periods set status='reopened',reopened_at=now(),reopened_by=v_user.id,reopen_reason=p_reason where id=p_period_id;
  update public.period_closures set status='reopened' where id=v_closure;
  insert into public.closure_audit_log(period_closure_id,actor_id,action,reason) values(v_closure,v_user.id,'period.reopened',p_reason);
  return v_request;
end $$;
grant execute on function public.request_period_reopening(uuid,text) to authenticated;

create or replace view public.current_period_closures with (security_invoker=true) as
select distinct on (pc.period_id) pc.*
from public.period_closures pc
order by pc.period_id,pc.revision desc;
grant select on public.current_period_closures to authenticated;

do $$ declare table_name text;
begin
  foreach table_name in array array['period_closures','closure_snapshots','realized_metrics','forecast_accuracy','fill_rate_metrics','challenger_validation','horizon_accuracy','interval_coverage','learning_events','period_reopen_requests','closure_audit_log']
  loop execute format('alter table public.%I enable row level security',table_name); end loop;
end $$;

drop policy if exists closure_read on public.period_closures;
create policy closure_read on public.period_closures for select using (
  exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active and u.organization_id=period_closures.organization_id));
drop policy if exists closure_read on public.closure_snapshots;
create policy closure_read on public.closure_snapshots for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=closure_snapshots.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.realized_metrics;
create policy closure_read on public.realized_metrics for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=realized_metrics.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.forecast_accuracy;
create policy closure_read on public.forecast_accuracy for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=forecast_accuracy.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.fill_rate_metrics;
create policy closure_read on public.fill_rate_metrics for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=fill_rate_metrics.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.challenger_validation;
create policy closure_read on public.challenger_validation for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=challenger_validation.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.horizon_accuracy;
create policy closure_read on public.horizon_accuracy for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=horizon_accuracy.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.interval_coverage;
create policy closure_read on public.interval_coverage for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=interval_coverage.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.learning_events;
create policy closure_read on public.learning_events for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=learning_events.period_closure_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.period_reopen_requests;
create policy closure_read on public.period_reopen_requests for select using (
  exists(select 1 from public.periods p join public.users u on u.organization_id=p.organization_id where p.id=period_reopen_requests.period_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists closure_read on public.closure_audit_log;
create policy closure_read on public.closure_audit_log for select using (
  exists(select 1 from public.period_closures pc join public.users u on u.organization_id=pc.organization_id where pc.id=closure_audit_log.period_closure_id and u.auth_user_id=auth.uid() and u.active));
