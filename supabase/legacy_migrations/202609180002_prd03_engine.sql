-- PRD 03: immutable, auditable statistical forecast runs.
do $$ begin
  create type public.forecast_run_status as enum (
    'pending','preparing','analyzing','models','backtesting','evaluating',
    'generating','completed','completed_with_alerts','error'
  );
exception when duplicate_object then null; end $$;

create table if not exists public.forecast_series (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  grain text not null check (grain in ('total','product','color','variant')),
  product_id uuid references public.products(id),
  label text not null,
  dimensions jsonb not null default '{}'::jsonb,
  active boolean not null default true,
  unique (organization_id, chain_id, target, grain, product_id)
);

create table if not exists public.forecast_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  version_label text,
  status public.forecast_run_status not null default 'pending',
  cutoff_period date,
  horizon_months integer not null default 12 check (horizon_months = 12),
  engine_version text not null default 'prd03-1.0.0',
  requested_by uuid references public.users(id),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  frozen_at timestamptz,
  configuration jsonb not null default '{}'::jsonb,
  error_detail jsonb,
  unique (organization_id, version_label)
);

-- PRD 02 created minimal placeholder tables; extend them without discarding history.
alter table public.forecast_runs add column if not exists organization_id uuid references public.organizations(id);
alter table public.forecast_runs add column if not exists chain_id uuid references public.chains(id);
alter table public.forecast_runs add column if not exists target text;
alter table public.forecast_runs add column if not exists version_label text;
alter table public.forecast_runs add column if not exists status public.forecast_run_status not null default 'pending';
alter table public.forecast_runs add column if not exists cutoff_period date;
alter table public.forecast_runs add column if not exists horizon_months integer not null default 12;
alter table public.forecast_runs add column if not exists engine_version text not null default 'prd03-1.0.0';
alter table public.forecast_runs add column if not exists requested_by uuid references public.users(id);
alter table public.forecast_runs add column if not exists requested_at timestamptz not null default now();
alter table public.forecast_runs add column if not exists started_at timestamptz;
alter table public.forecast_runs add column if not exists completed_at timestamptz;
alter table public.forecast_runs add column if not exists frozen_at timestamptz;
alter table public.forecast_runs add column if not exists configuration jsonb not null default '{}'::jsonb;
alter table public.forecast_runs add column if not exists error_detail jsonb;
create unique index if not exists forecast_runs_org_version_key on public.forecast_runs(organization_id, version_label) where version_label is not null;

create table if not exists public.forecast_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.forecast_runs(id) on delete restrict,
  series_id uuid not null references public.forecast_series(id),
  result_status text not null check (result_status in ('completed','completed_with_alerts','insufficient','error')),
  classification text,
  winning_model text,
  wape numeric(12,4),
  bias numeric(12,4),
  stability numeric(12,4),
  diagnostics jsonb not null default '{}'::jsonb,
  explanation text,
  reason text,
  created_at timestamptz not null default now(),
  unique (run_id, series_id)
);

create table if not exists public.forecast_model_results (
  id uuid primary key default gen_random_uuid(),
  forecast_result_id uuid not null references public.forecast_results(id) on delete restrict,
  model_name text not null,
  applicable boolean not null,
  wape numeric(12,4), bias numeric(12,4), stability numeric(12,4), score numeric(12,4),
  parameters jsonb not null default '{}'::jsonb,
  rejection_reason text,
  unique (forecast_result_id, model_name)
);

create table if not exists public.backtest_windows (
  id uuid primary key default gen_random_uuid(),
  model_result_id uuid not null references public.forecast_model_results(id) on delete restrict,
  origin_period date not null,
  horizon integer not null check (horizon in (1,3,6,12)),
  actual_values jsonb not null,
  predicted_values jsonb not null,
  wape numeric(12,4), bias numeric(12,4), stability numeric(12,4),
  unique (model_result_id, origin_period, horizon)
);

create table if not exists public.forecast_values (
  id uuid primary key default gen_random_uuid(),
  forecast_result_id uuid not null references public.forecast_results(id) on delete restrict,
  period_start date not null,
  horizon integer not null check (horizon between 1 and 12),
  value numeric(18,3) not null check (value >= 0),
  unique (forecast_result_id, period_start)
);

alter table public.forecast_values add column if not exists forecast_result_id uuid references public.forecast_results(id) on delete restrict;
alter table public.forecast_values add column if not exists period_start date;
alter table public.forecast_values add column if not exists horizon integer;
create unique index if not exists forecast_values_result_period_key on public.forecast_values(forecast_result_id, period_start) where forecast_result_id is not null;

create table if not exists public.statistical_alerts (
  id uuid primary key default gen_random_uuid(),
  forecast_result_id uuid not null references public.forecast_results(id) on delete restrict,
  alert_type text not null check (alert_type in ('behavior_change','wape_deterioration','pattern_change','anomaly','loss_of_movement','data_quality')),
  severity text not null check (severity in ('info','warning','blocking')),
  message text not null,
  evidence jsonb not null default '{}'::jsonb,
  acknowledged_by uuid references public.users(id),
  acknowledged_at timestamptz,
  created_at timestamptz not null default now()
);

create or replace view public.forecast_monthly_observations as
select s.organization_id, s.chain_id, 'Venta'::text as target, s.product_id,
       p.description as label, pe.period_start,
       sum(s.quantity)::numeric(18,3) as value
from public.sales s
join public.periods pe on pe.id=s.period_id and pe.status='closed'
join public.products p on p.id=s.product_id
where s.state='current' and s.status='final'
group by s.organization_id,s.chain_id,s.product_id,p.description,pe.period_start
union all
select o.organization_id, o.chain_id, 'Pedido'::text as target, ol.product_id,
       p.description as label, pe.period_start,
       sum(ol.quantity)::numeric(18,3) as value
from public.orders o
join public.order_lines ol on ol.order_id=o.id
join public.periods pe on pe.id=o.period_id and pe.status='closed'
join public.products p on p.id=ol.product_id
where o.state='current'
group by o.organization_id,o.chain_id,ol.product_id,p.description,pe.period_start;

grant select on public.forecast_monthly_observations to authenticated;

create or replace function public.guard_frozen_forecast()
returns trigger language plpgsql as $$
declare v_run uuid; v_frozen timestamptz;
begin
  if tg_table_name='forecast_runs' then
    if tg_op in ('UPDATE','DELETE') and old.frozen_at is not null then raise exception 'forecast_version_frozen'; end if;
    return case when tg_op='DELETE' then old else new end;
  elsif tg_table_name='forecast_results' then
    v_run := coalesce(new.run_id, old.run_id);
  elsif tg_table_name in ('forecast_values','statistical_alerts','forecast_model_results') then
    select fr.run_id into v_run from public.forecast_results fr where fr.id=coalesce(new.forecast_result_id,old.forecast_result_id);
  elsif tg_table_name='backtest_windows' then
    select fr.run_id into v_run from public.forecast_model_results mr join public.forecast_results fr on fr.id=mr.forecast_result_id where mr.id=coalesce(new.model_result_id,old.model_result_id);
  end if;
  select frozen_at into v_frozen from public.forecast_runs where id=v_run;
  if v_frozen is not null then raise exception 'forecast_version_frozen'; end if;
  return case when tg_op='DELETE' then old else new end;
end $$;

drop trigger if exists guard_frozen_run on public.forecast_runs;
create trigger guard_frozen_run before update or delete on public.forecast_runs for each row execute function public.guard_frozen_forecast();
drop trigger if exists guard_frozen_result on public.forecast_results;
create trigger guard_frozen_result before insert or update or delete on public.forecast_results for each row execute function public.guard_frozen_forecast();
drop trigger if exists guard_frozen_model on public.forecast_model_results;
create trigger guard_frozen_model before insert or update or delete on public.forecast_model_results for each row execute function public.guard_frozen_forecast();
drop trigger if exists guard_frozen_window on public.backtest_windows;
create trigger guard_frozen_window before insert or update or delete on public.backtest_windows for each row execute function public.guard_frozen_forecast();
drop trigger if exists guard_frozen_value on public.forecast_values;
create trigger guard_frozen_value before insert or update or delete on public.forecast_values for each row execute function public.guard_frozen_forecast();
drop trigger if exists guard_frozen_alert on public.statistical_alerts;
create trigger guard_frozen_alert before insert or update or delete on public.statistical_alerts for each row execute function public.guard_frozen_forecast();

create or replace function public.request_forecast_run(p_target text)
returns uuid language plpgsql security invoker as $$
declare
  v_org uuid;
  v_chain uuid;
  v_user uuid;
  v_run uuid;
begin
  if p_target not in ('Venta','Pedido') then raise exception 'invalid target'; end if;
  select id into v_org from public.organizations order by created_at limit 1;
  select id into v_chain from public.chains where organization_id = v_org order by name limit 1;
  select id into v_user from public.users where auth_user_id = auth.uid() limit 1;
  if v_org is null or v_chain is null then raise exception 'pilot not configured'; end if;
  insert into public.forecast_runs (organization_id, chain_id, target, requested_by)
  values (v_org, v_chain, p_target, v_user) returning id into v_run;
  insert into public.domain_events (event_type, aggregate_type, aggregate_id, payload, actor_id)
  values ('forecast.run_requested','forecast_run',v_run,jsonb_build_object('target',p_target),v_user);
  return v_run;
end $$;

alter table public.forecast_series enable row level security;
alter table public.forecast_runs enable row level security;
alter table public.forecast_results enable row level security;
alter table public.forecast_model_results enable row level security;
alter table public.backtest_windows enable row level security;
alter table public.forecast_values enable row level security;
alter table public.statistical_alerts enable row level security;

do $$
declare t text;
begin
  foreach t in array array['forecast_series','forecast_runs','forecast_results','forecast_model_results','backtest_windows','forecast_values','statistical_alerts'] loop
    execute format('drop policy if exists forecast_read on public.%I', t);
    execute format('create policy forecast_read on public.%I for select using (exists (select 1 from public.users u where u.auth_user_id = auth.uid() and u.active))', t);
  end loop;
end $$;

drop policy if exists forecast_run_request on public.forecast_runs;
create policy forecast_run_request on public.forecast_runs for insert with check (
  exists (select 1 from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active and r.code in ('manager','editor'))
);

grant execute on function public.request_forecast_run(text) to authenticated;
