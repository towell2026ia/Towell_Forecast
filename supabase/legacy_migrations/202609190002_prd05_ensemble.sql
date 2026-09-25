-- PRD 05: ensemble, no-degradation and immutable Forecast Towell vintages.
do $$ begin
  create type public.ensemble_run_status as enum ('pending','preparing','calculating','evaluating','challenger','validated','champion','rejected','error');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.forecast_towell_state as enum ('challenger','validated','champion','rejected','retired');
exception when duplicate_object then null; end $$;

create table if not exists public.ensemble_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  status public.ensemble_run_status not null default 'pending',
  cutoff_period date,
  engine_version text not null default 'prd05-1.0.0',
  minimum_improvement numeric(12,4) not null default 0.25,
  requested_by uuid references public.users(id),
  requested_at timestamptz not null default now(),
  started_at timestamptz, completed_at timestamptz,
  configuration jsonb not null default '{}'::jsonb,
  error_detail jsonb
);

create table if not exists public.ensemble_candidates (
  id uuid primary key default gen_random_uuid(),
  ensemble_run_id uuid not null references public.ensemble_runs(id) on delete restrict,
  strategy text not null check (strategy in ('statistical','ml','ensemble')),
  statistical_weight numeric(8,6) not null check (statistical_weight between 0 and 1),
  ml_weight numeric(8,6) not null check (ml_weight between 0 and 1),
  score numeric(14,4) not null, wape numeric(12,4) not null,
  bias numeric(12,4) not null, stability numeric(12,4) not null,
  recent_wape numeric(12,4), historical_wape numeric(12,4), deterioration numeric(12,4),
  observations integer not null, windows integer not null,
  no_degradation_pass boolean not null,
  minimum_improvement_pass boolean not null,
  state text not null check (state in ('champion','challenger','rejected')),
  rejection_reason text,
  unique(ensemble_run_id,statistical_weight,ml_weight),
  check (abs((statistical_weight + ml_weight) - 1.0) < 0.000001)
);

create table if not exists public.ensemble_metrics (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references public.ensemble_candidates(id) on delete restrict,
  level text not null check (level in ('global','horizon','series','series_horizon')),
  dimension_key text not null,
  horizon integer check (horizon between 1 and 12),
  forecast_series_id uuid references public.forecast_series(id),
  observations integer not null, windows integer not null,
  wape numeric(12,4) not null, bias numeric(12,4) not null, stability numeric(12,4) not null,
  recent_wape numeric(12,4), historical_wape numeric(12,4), deterioration numeric(12,4),
  unique(candidate_id,level,dimension_key)
);

create table if not exists public.forecast_towell (
  id uuid primary key default gen_random_uuid(),
  ensemble_run_id uuid not null references public.ensemble_runs(id) on delete restrict,
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  version_label text not null,
  state public.forecast_towell_state not null,
  cutoff_period date not null,
  statistical_run_id uuid references public.forecast_runs(id),
  ml_model_version_id uuid references public.ml_model_versions(id),
  strategy text not null check (strategy in ('statistical','ml','ensemble')),
  statistical_weight numeric(8,6) not null,
  ml_weight numeric(8,6) not null,
  wape numeric(12,4) not null, bias numeric(12,4) not null, stability numeric(12,4) not null,
  minimum_improvement numeric(12,4) not null,
  improvement_points numeric(12,4) not null default 0,
  confidence text not null check (confidence in ('Alta','Media','Baja')),
  source_contract text not null default 'normalized_platform_records',
  created_by uuid references public.users(id), created_at timestamptz not null default now(),
  validated_by uuid references public.users(id), validated_at timestamptz,
  published_by uuid references public.users(id), published_at timestamptz,
  unique(organization_id,version_label),
  check (abs((statistical_weight + ml_weight) - 1.0) < 0.000001)
);
create index if not exists forecast_towell_champion_history
  on public.forecast_towell(organization_id,chain_id,target,created_at desc) where state='champion';

create table if not exists public.forecast_towell_publications (
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  target text not null check (target in ('Venta','Pedido')),
  forecast_towell_id uuid not null references public.forecast_towell(id) on delete restrict,
  published_by uuid references public.users(id), published_at timestamptz not null default now(),
  primary key(organization_id,chain_id,target)
);

create table if not exists public.forecast_vintages (
  id uuid primary key default gen_random_uuid(),
  forecast_towell_id uuid not null references public.forecast_towell(id) on delete restrict,
  forecast_series_id uuid references public.forecast_series(id),
  product_id uuid references public.products(id),
  issued_at timestamptz not null default now(),
  forecast_date date not null, horizon integer not null check (horizon between 1 and 12),
  statistical_value numeric(18,4) not null,
  ml_value numeric(18,4),
  statistical_weight numeric(8,6) not null,
  ml_weight numeric(8,6) not null,
  raw_value numeric(18,4) not null,
  value numeric(18,4) not null check (value >= 0),
  operational_value bigint not null check (operational_value >= 0),
  divergence numeric(12,4), confidence text not null check (confidence in ('Alta','Media','Baja')),
  adjustment jsonb not null default '{}'::jsonb,
  unique(forecast_towell_id,product_id,forecast_date)
);

create table if not exists public.probability_bands (
  id uuid primary key default gen_random_uuid(),
  forecast_vintage_id uuid not null references public.forecast_vintages(id) on delete restrict,
  p10 numeric(18,4), p50 numeric(18,4) not null, p90 numeric(18,4) not null, p95 numeric(18,4) not null,
  method text not null default 'empirical_oos_residuals', residual_count integer not null,
  unique(forecast_vintage_id),
  check (p10 is null or (p10 <= p50 and p50 <= p90 and p90 <= p95))
);

create table if not exists public.ensemble_alerts (
  id uuid primary key default gen_random_uuid(),
  ensemble_run_id uuid not null references public.ensemble_runs(id) on delete restrict,
  forecast_towell_id uuid references public.forecast_towell(id),
  alert_type text not null check (alert_type in ('high_divergence','ml_error','ensemble_error','insufficient_data','negative_clamp','bimonthly_review')),
  severity text not null check (severity in ('info','warning','blocking')),
  message text not null, evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(), acknowledged_by uuid references public.users(id), acknowledged_at timestamptz
);

create table if not exists public.ensemble_audit_log (
  id bigserial primary key,
  ensemble_run_id uuid not null references public.ensemble_runs(id) on delete restrict,
  forecast_towell_id uuid references public.forecast_towell(id),
  stage text not null, status text not null, message text not null,
  details jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create or replace function public.request_ensemble_run(p_target text, p_minimum_improvement numeric default 0.25)
returns uuid language plpgsql security invoker as $$
declare v_org uuid; v_chain uuid; v_user uuid; v_role text; v_run uuid;
begin
  if p_target not in ('Venta','Pedido') then raise exception 'invalid_target'; end if;
  if p_minimum_improvement < 0 or p_minimum_improvement > 10 then raise exception 'invalid_minimum_improvement'; end if;
  select u.organization_id,u.id,r.code into v_org,v_user,v_role
    from public.users u join public.roles r on r.id=u.role_id
    where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role not in ('manager','editor') then raise exception 'write_role_required'; end if;
  select id into v_chain from public.chains where organization_id=v_org order by name limit 1;
  insert into public.ensemble_runs(organization_id,chain_id,target,minimum_improvement,requested_by)
    values(v_org,v_chain,p_target,p_minimum_improvement,v_user) returning id into v_run;
  insert into public.domain_events(event_type,aggregate_type,aggregate_id,payload,actor_id)
    values('ensemble.run_requested','ensemble_run',v_run,jsonb_build_object('target',p_target,'minimum_improvement',p_minimum_improvement),v_user);
  return v_run;
end $$;
grant execute on function public.request_ensemble_run(text,numeric) to authenticated;

create or replace function public.guard_published_forecast_towell()
returns trigger language plpgsql as $$
declare v_version uuid; v_state public.forecast_towell_state; v_published timestamptz;
begin
  if tg_table_name='forecast_towell' then
    if tg_op in ('UPDATE','DELETE') and (old.state in ('validated','champion','rejected','retired') or old.published_at is not null) then
      raise exception 'forecast_towell_version_immutable';
    end if;
    return case when tg_op='DELETE' then old else new end;
  end if;
  if tg_table_name='forecast_vintages' then
    v_version := coalesce(new.forecast_towell_id,old.forecast_towell_id);
  elsif tg_table_name='probability_bands' then
    select forecast_towell_id into v_version from public.forecast_vintages where id=coalesce(new.forecast_vintage_id,old.forecast_vintage_id);
  end if;
  select state,published_at into v_state,v_published from public.forecast_towell where id=v_version;
  if v_state in ('validated','champion','rejected','retired') or v_published is not null then
    raise exception 'forecast_towell_version_immutable';
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;
drop trigger if exists guard_forecast_towell on public.forecast_towell;
create trigger guard_forecast_towell before update or delete on public.forecast_towell for each row execute function public.guard_published_forecast_towell();
drop trigger if exists guard_forecast_vintage on public.forecast_vintages;
create trigger guard_forecast_vintage before insert or update or delete on public.forecast_vintages for each row execute function public.guard_published_forecast_towell();
drop trigger if exists guard_probability_band on public.probability_bands;
create trigger guard_probability_band before insert or update or delete on public.probability_bands for each row execute function public.guard_published_forecast_towell();

create or replace view public.current_forecast_towell with (security_invoker=true) as
select ft.id forecast_towell_id,ft.organization_id,ft.chain_id,ft.target,ft.version_label,ft.strategy,
       ft.statistical_weight,ft.ml_weight,ft.wape,ft.bias,ft.stability,
       fv.product_id,fv.forecast_date,fv.horizon,fv.value,fv.operational_value,fv.confidence,
       pb.p10,pb.p50,pb.p90,pb.p95
from public.forecast_towell_publications publication
join public.forecast_towell ft on ft.id=publication.forecast_towell_id
join public.forecast_vintages fv on fv.forecast_towell_id=ft.id
left join public.probability_bands pb on pb.forecast_vintage_id=fv.id
where ft.state='champion';
grant select on public.current_forecast_towell to authenticated;

alter table public.ensemble_runs enable row level security;
alter table public.ensemble_candidates enable row level security;
alter table public.ensemble_metrics enable row level security;
alter table public.forecast_towell enable row level security;
alter table public.forecast_towell_publications enable row level security;
alter table public.forecast_vintages enable row level security;
alter table public.probability_bands enable row level security;
alter table public.ensemble_alerts enable row level security;
alter table public.ensemble_audit_log enable row level security;
drop policy if exists ensemble_read on public.ensemble_runs;
create policy ensemble_read on public.ensemble_runs for select using (
  exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active and u.organization_id=ensemble_runs.organization_id));
drop policy if exists ensemble_read on public.ensemble_candidates;
create policy ensemble_read on public.ensemble_candidates for select using (
  exists(select 1 from public.ensemble_runs er join public.users u on u.organization_id=er.organization_id where er.id=ensemble_candidates.ensemble_run_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_read on public.ensemble_metrics;
create policy ensemble_read on public.ensemble_metrics for select using (
  exists(select 1 from public.ensemble_candidates ec join public.ensemble_runs er on er.id=ec.ensemble_run_id join public.users u on u.organization_id=er.organization_id where ec.id=ensemble_metrics.candidate_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_read on public.forecast_towell;
create policy ensemble_read on public.forecast_towell for select using (
  exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active and u.organization_id=forecast_towell.organization_id));
drop policy if exists ensemble_read on public.forecast_towell_publications;
create policy ensemble_read on public.forecast_towell_publications for select using (
  exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active and u.organization_id=forecast_towell_publications.organization_id));
drop policy if exists ensemble_read on public.forecast_vintages;
create policy ensemble_read on public.forecast_vintages for select using (
  exists(select 1 from public.forecast_towell ft join public.users u on u.organization_id=ft.organization_id where ft.id=forecast_vintages.forecast_towell_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_read on public.probability_bands;
create policy ensemble_read on public.probability_bands for select using (
  exists(select 1 from public.forecast_vintages fv join public.forecast_towell ft on ft.id=fv.forecast_towell_id join public.users u on u.organization_id=ft.organization_id where fv.id=probability_bands.forecast_vintage_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_read on public.ensemble_alerts;
create policy ensemble_read on public.ensemble_alerts for select using (
  exists(select 1 from public.ensemble_runs er join public.users u on u.organization_id=er.organization_id where er.id=ensemble_alerts.ensemble_run_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_read on public.ensemble_audit_log;
create policy ensemble_read on public.ensemble_audit_log for select using (
  exists(select 1 from public.ensemble_runs er join public.users u on u.organization_id=er.organization_id where er.id=ensemble_audit_log.ensemble_run_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists ensemble_run_request on public.ensemble_runs;
create policy ensemble_run_request on public.ensemble_runs for insert with check(
  exists(select 1 from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active and r.code in ('manager','editor'))
);
