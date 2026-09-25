-- PRD 04: Global ML engine, independent from the statistical engine.
do $$ begin
  create type public.ml_run_status as enum ('pending','preparing_data','training','backtesting','evaluating','completed','completed_with_alerts','error');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.ml_model_state as enum ('training','evaluating','challenger','approved','champion','rejected','retired');
exception when duplicate_object then null; end $$;

create table if not exists public.ml_training_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  status public.ml_run_status not null default 'pending',
  requested_by uuid references public.users(id),
  requested_at timestamptz not null default now(),
  started_at timestamptz, completed_at timestamptz,
  cutoff_period date, engine_version text not null default 'prd04-1.0.0',
  error_detail jsonb
);

create table if not exists public.ml_model_versions (
  id uuid primary key default gen_random_uuid(),
  training_run_id uuid not null references public.ml_training_runs(id) on delete restrict,
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  version_label text not null,
  state public.ml_model_state not null,
  algorithm text not null,
  strategy text not null check (strategy in ('direct','recursive')),
  training_start date, training_end date not null,
  feature_schema jsonb not null,
  hyperparameters jsonb not null default '{}'::jsonb,
  dataset_fingerprint text not null,
  observation_count integer not null,
  series_count integer not null,
  training_duration_ms integer,
  wape numeric(12,4), bias numeric(12,4), stability numeric(12,4), mae numeric(18,4), rmse numeric(18,4),
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  approved_by uuid references public.users(id), approved_at timestamptz,
  unique (organization_id, version_label)
);
create unique index if not exists ml_one_champion_per_target on public.ml_model_versions(organization_id,chain_id,target) where state='champion';

create table if not exists public.ml_horizon_metrics (
  id uuid primary key default gen_random_uuid(),
  model_version_id uuid not null references public.ml_model_versions(id) on delete restrict,
  horizon integer not null check (horizon between 1 and 12),
  observations integer not null,
  wape numeric(12,4), bias numeric(12,4), stability numeric(12,4), mae numeric(18,4), rmse numeric(18,4),
  unique(model_version_id,horizon)
);

create table if not exists public.ml_predictions (
  id uuid primary key default gen_random_uuid(),
  model_version_id uuid not null references public.ml_model_versions(id) on delete restrict,
  forecast_series_id uuid references public.forecast_series(id),
  product_id uuid references public.products(id),
  color text, category text,
  forecast_date date not null, horizon integer not null check (horizon between 1 and 12),
  value numeric(18,3) not null check (value >= 0),
  historical_wape numeric(12,4), historical_bias numeric(12,4), confidence_level text,
  generated_at timestamptz not null default now(),
  unique(model_version_id,product_id,forecast_date)
);

create table if not exists public.ml_feature_importance (
  id uuid primary key default gen_random_uuid(),
  model_version_id uuid not null references public.ml_model_versions(id) on delete restrict,
  feature_name text not null, importance numeric(12,6) not null,
  method text not null check (method in ('native','permutation','shap')),
  interpretation_note text not null default 'Influencia predictiva; no implica causalidad.',
  unique(model_version_id,feature_name,method)
);

create table if not exists public.ml_alerts (
  id uuid primary key default gen_random_uuid(),
  training_run_id uuid not null references public.ml_training_runs(id) on delete restrict,
  model_version_id uuid references public.ml_model_versions(id),
  alert_type text not null check (alert_type in ('performance_drift','data_drift','challenger_better','missing_data','out_of_range','behavior_change','engine_divergence','low_evidence')),
  severity text not null check (severity in ('info','warning','blocking')),
  message text not null, evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(), acknowledged_by uuid references public.users(id), acknowledged_at timestamptz
);

create table if not exists public.ml_series_state (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  product_id uuid not null references public.products(id),
  state text not null check (state in ('active','discontinued','temporarily_inactive','no_information','intermittent')),
  evidence_level text not null default 'standard' check (evidence_level in ('low','standard','strong')),
  reason text, updated_by uuid references public.users(id), updated_at timestamptz not null default now(),
  unique(organization_id,chain_id,product_id)
);

create table if not exists public.ml_training_log (
  id bigserial primary key, training_run_id uuid not null references public.ml_training_runs(id) on delete restrict,
  stage text not null, status text not null, message text, details jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create or replace view public.ml_training_observations as
with sale as (
  select s.organization_id,s.chain_id,s.period_id,s.product_id,sum(s.quantity) sale
  from public.sales s where s.state='current' and s.status='final' group by 1,2,3,4
), orders as (
  select o.organization_id,o.chain_id,o.period_id,ol.product_id,sum(ol.quantity) orders
  from public.orders o join public.order_lines ol on ol.order_id=o.id where o.state='current' group by 1,2,3,4
), delivery as (
  select o.organization_id,o.chain_id,o.period_id,ol.product_id,sum(d.quantity) delivery
  from public.deliveries d join public.order_lines ol on ol.id=d.order_line_id join public.orders o on o.id=ol.order_id
  where d.state='current' and o.state='current' group by 1,2,3,4
), client as (
  select f.organization_id,f.chain_id,f.period_id,f.product_id,sum(f.quantity) client_forecast
  from public.customer_forecasts f where f.state='current' group by 1,2,3,4
)
select pe.organization_id,c.id chain_id,p.id product_id,p.family category,p.description,
       pv.color,pe.period_start,sale.sale,orders.orders,delivery.delivery,client.client_forecast
from public.periods pe
join public.chains c on c.organization_id=pe.organization_id
cross join public.products p
left join public.product_variants pv on pv.product_id=p.id
left join sale on sale.organization_id=pe.organization_id and sale.chain_id=c.id and sale.period_id=pe.id and sale.product_id=p.id
left join orders on orders.organization_id=pe.organization_id and orders.chain_id=c.id and orders.period_id=pe.id and orders.product_id=p.id
left join delivery on delivery.organization_id=pe.organization_id and delivery.chain_id=c.id and delivery.period_id=pe.id and delivery.product_id=p.id
left join client on client.organization_id=pe.organization_id and client.chain_id=c.id and client.period_id=pe.id and client.product_id=p.id
where pe.status='closed' and p.active;
grant select on public.ml_training_observations to authenticated;

create or replace function public.request_ml_training(p_target text)
returns uuid language plpgsql security invoker as $$
declare v_org uuid; v_chain uuid; v_user uuid; v_role text; v_run uuid;
begin
  if p_target not in ('Venta','Pedido') then raise exception 'invalid_target'; end if;
  select u.organization_id,u.id,r.code into v_org,v_user,v_role from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role not in ('manager','editor') then raise exception 'write_role_required'; end if;
  select id into v_chain from public.chains where organization_id=v_org order by name limit 1;
  insert into public.ml_training_runs(organization_id,chain_id,target,requested_by) values(v_org,v_chain,p_target,v_user) returning id into v_run;
  insert into public.domain_events(event_type,aggregate_type,aggregate_id,payload,actor_id) values('ml.training_requested','ml_training_run',v_run,jsonb_build_object('target',p_target),v_user);
  return v_run;
end $$;
grant execute on function public.request_ml_training(text) to authenticated;

create or replace function public.guard_published_ml_version()
returns trigger language plpgsql as $$
declare v_state public.ml_model_state; v_version uuid;
begin
  if tg_table_name='ml_model_versions' then
    if tg_op in ('UPDATE','DELETE') and old.state in ('approved','champion','rejected','retired') then raise exception 'ml_version_immutable'; end if;
    return case when tg_op='DELETE' then old else new end;
  end if;
  v_version := coalesce(new.model_version_id,old.model_version_id);
  select state into v_state from public.ml_model_versions where id=v_version;
  if v_state in ('approved','champion','rejected','retired') then raise exception 'ml_version_immutable'; end if;
  return case when tg_op='DELETE' then old else new end;
end $$;
drop trigger if exists guard_ml_version on public.ml_model_versions;
create trigger guard_ml_version before update or delete on public.ml_model_versions for each row execute function public.guard_published_ml_version();
drop trigger if exists guard_ml_prediction on public.ml_predictions;
create trigger guard_ml_prediction before insert or update or delete on public.ml_predictions for each row execute function public.guard_published_ml_version();
drop trigger if exists guard_ml_metric on public.ml_horizon_metrics;
create trigger guard_ml_metric before insert or update or delete on public.ml_horizon_metrics for each row execute function public.guard_published_ml_version();
drop trigger if exists guard_ml_importance on public.ml_feature_importance;
create trigger guard_ml_importance before insert or update or delete on public.ml_feature_importance for each row execute function public.guard_published_ml_version();

alter table public.ml_training_runs enable row level security;
alter table public.ml_model_versions enable row level security;
alter table public.ml_horizon_metrics enable row level security;
alter table public.ml_predictions enable row level security;
alter table public.ml_feature_importance enable row level security;
alter table public.ml_alerts enable row level security;
alter table public.ml_series_state enable row level security;
alter table public.ml_training_log enable row level security;
do $$ declare t text; begin
  foreach t in array array['ml_training_runs','ml_model_versions','ml_horizon_metrics','ml_predictions','ml_feature_importance','ml_alerts','ml_series_state','ml_training_log'] loop
    execute format('drop policy if exists ml_read on public.%I',t);
    execute format('create policy ml_read on public.%I for select using (exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active))',t);
  end loop;
end $$;
drop policy if exists ml_run_request on public.ml_training_runs;
create policy ml_run_request on public.ml_training_runs for insert with check(exists(select 1 from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active and r.code in ('manager','editor')));
