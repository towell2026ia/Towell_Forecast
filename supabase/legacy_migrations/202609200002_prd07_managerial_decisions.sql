-- PRD 07: controlled managerial decisions, approvals and Forecast Value Added.
do $$ begin
  create type public.forecast_decision_state as enum ('available','reviewed','adjusted','approved','frozen','evaluated','reopened','error');
exception when duplicate_object then null; end $$;

create table if not exists public.adjustment_reasons (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  name text not null,
  active boolean not null default true,
  system_only boolean not null default false,
  created_at timestamptz not null default now()
);
insert into public.adjustment_reasons(code,name,system_only) values
  ('promotion','Promoción',false),
  ('customer_store_opening','Alta de cliente/tienda',false),
  ('customer_store_closure','Baja de cliente/tienda',false),
  ('confirmed_commercial_change','Cambio comercial confirmado',false),
  ('extraordinary_order','Pedido extraordinario',false),
  ('special_event','Evento especial',false),
  ('discontinuation','Descontinuación',false),
  ('distribution_change','Cambio de distribución',false),
  ('commercial_restriction','Restricción comercial',false),
  ('direct_customer_information','Información directa del cliente',false),
  ('business_correction','Corrección de negocio',false),
  ('other','Otro',false),
  ('no_adjustment','Sin ajuste humano',true)
on conflict(code) do update set name=excluded.name,system_only=excluded.system_only;

create table if not exists public.forecast_decisions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  forecast_towell_id uuid not null references public.forecast_towell(id) on delete restrict,
  forecast_cycle date not null,
  target text not null check (target in ('Venta','Pedido')),
  created_at timestamptz not null default now(),
  unique(organization_id,chain_id,forecast_towell_id)
);

create table if not exists public.adjustment_versions (
  id uuid primary key default gen_random_uuid(),
  forecast_decision_id uuid not null references public.forecast_decisions(id) on delete restrict,
  previous_version_id uuid references public.adjustment_versions(id) on delete restrict,
  revision integer not null check (revision>0),
  version_label text not null,
  state public.forecast_decision_state not null default 'available',
  significant_adjustment_threshold numeric(12,4) not null default 20 check (significant_adjustment_threshold>0),
  source_contract text not null default 'normalized_platform_records',
  proposed_by uuid not null references public.users(id),
  proposed_at timestamptz not null default now(),
  correction_reason text,
  decision_hash text,
  configuration jsonb not null default '{}'::jsonb,
  unique(forecast_decision_id,revision),
  unique(forecast_decision_id,version_label),
  check ((revision=1 and previous_version_id is null) or (revision>1 and previous_version_id is not null))
);

create table if not exists public.forecast_adjustments (
  id uuid primary key default gen_random_uuid(),
  adjustment_version_id uuid not null references public.adjustment_versions(id) on delete restrict,
  forecast_vintage_id uuid not null references public.forecast_vintages(id) on delete restrict,
  product_id uuid references public.products(id),
  horizon integer not null check (horizon between 1 and 12),
  method text not null check (method in ('absolute','percentage','final_value','none')),
  reason_id uuid not null references public.adjustment_reasons(id),
  explanation text not null,
  evidence_reference text,
  forecast_towell numeric(18,4) not null check (forecast_towell>=0),
  adjustment numeric(18,4) not null,
  adjustment_percent numeric(12,4),
  forecast_adjusted numeric(18,4) not null check (forecast_adjusted>=0),
  within_p90 boolean,
  outside_p95 boolean not null default false,
  below_p10 boolean not null default false,
  significant_adjustment boolean not null default false,
  proposed_by uuid not null references public.users(id),
  proposed_at timestamptz not null default now(),
  unique(adjustment_version_id,forecast_vintage_id)
);

create table if not exists public.forecast_approvals (
  id uuid primary key default gen_random_uuid(),
  adjustment_version_id uuid not null unique references public.adjustment_versions(id) on delete restrict,
  approved_by uuid not null references public.users(id),
  approved_at timestamptz not null default now(),
  approval_comment text,
  frozen boolean not null default true,
  approved_snapshot jsonb not null,
  approved_snapshot_hash text not null check (length(approved_snapshot_hash)=64)
);

create table if not exists public.human_fva_metrics (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid not null references public.period_closures(id) on delete restrict,
  adjustment_version_id uuid not null references public.adjustment_versions(id) on delete restrict,
  forecast_adjustment_id uuid references public.forecast_adjustments(id) on delete restrict,
  level text not null check (level in ('series','reason','user','horizon','total')),
  dimension_key text not null,
  horizon integer check (horizon between 1 and 12),
  reason_id uuid references public.adjustment_reasons(id),
  actor_id uuid references public.users(id),
  observations integer not null check (observations>0),
  towell_wape numeric(12,4),
  approved_wape numeric(12,4),
  towell_bias numeric(12,4),
  approved_bias numeric(12,4),
  towell_absolute_error numeric(18,4),
  approved_absolute_error numeric(18,4),
  fva_points numeric(12,4),
  fva_absolute numeric(18,4),
  classification text not null check (classification in ('positive','neutral','negative','no_intervention')),
  created_at timestamptz not null default now(),
  unique(period_closure_id,adjustment_version_id,level,dimension_key)
);

create table if not exists public.decision_learning_events (
  id uuid primary key default gen_random_uuid(),
  period_closure_id uuid references public.period_closures(id) on delete restrict,
  adjustment_version_id uuid not null references public.adjustment_versions(id) on delete restrict,
  event_type text not null check (event_type in ('decision_created','decision_approved','decision_frozen','human_decision_evaluated','negative_fva_pattern','fva_evaluation_failed','fva_reprocessed')),
  status text not null check (status in ('observed','queued','processing','completed','failed','ready')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create table if not exists public.decision_governance_alerts (
  id uuid primary key default gen_random_uuid(),
  adjustment_version_id uuid not null references public.adjustment_versions(id) on delete restrict,
  forecast_adjustment_id uuid references public.forecast_adjustments(id) on delete restrict,
  alert_type text not null check (alert_type in ('significant_adjustment','outside_probability_range','missing_approval','negative_fva_pattern')),
  severity text not null check (severity in ('info','warning','significant','blocking')),
  message text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  acknowledged_by uuid references public.users(id),
  acknowledged_at timestamptz
);
create unique index if not exists decision_version_level_alert_key
  on public.decision_governance_alerts(adjustment_version_id,alert_type) where forecast_adjustment_id is null;

create table if not exists public.decision_audit_log (
  id bigserial primary key,
  forecast_decision_id uuid not null references public.forecast_decisions(id) on delete restrict,
  adjustment_version_id uuid references public.adjustment_versions(id) on delete restrict,
  actor_id uuid references public.users(id),
  action text not null,
  previous_value jsonb,
  new_value jsonb,
  reason text,
  occurred_at timestamptz not null default now()
);

create or replace function public.guard_frozen_decision()
returns trigger language plpgsql as $$
declare v_state public.forecast_decision_state;
begin
  if tg_table_name='adjustment_versions' then
    if tg_op in ('UPDATE','DELETE') and old.state in ('frozen','evaluated') then raise exception 'frozen_decision_is_immutable'; end if;
    return case when tg_op='DELETE' then old else new end;
  end if;
  if tg_table_name='forecast_adjustments' then
    select state into v_state from public.adjustment_versions where id=coalesce(new.adjustment_version_id,old.adjustment_version_id);
    if v_state in ('approved','frozen','evaluated') then raise exception 'approved_adjustment_is_immutable'; end if;
  else
    raise exception 'decision_evidence_is_immutable';
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;
drop trigger if exists guard_frozen_decision on public.adjustment_versions;
create trigger guard_frozen_decision before update or delete on public.adjustment_versions for each row execute function public.guard_frozen_decision();
drop trigger if exists guard_approved_adjustment on public.forecast_adjustments;
create trigger guard_approved_adjustment before update or delete on public.forecast_adjustments for each row execute function public.guard_frozen_decision();
do $$ declare table_name text;
begin
  foreach table_name in array array['forecast_approvals','human_fva_metrics','decision_learning_events','decision_audit_log'] loop
    execute format('drop trigger if exists guard_decision_evidence on public.%I',table_name);
    execute format('create trigger guard_decision_evidence before update or delete on public.%I for each row execute function public.guard_frozen_decision()',table_name);
  end loop;
end $$;

create or replace function public.request_forecast_decision(
  p_forecast_towell_id uuid,
  p_adjustments jsonb default '[]'::jsonb,
  p_significant_threshold numeric default 20,
  p_correction_reason text default null
) returns uuid language plpgsql security definer set search_path=public as $$
declare
  v_user public.users%rowtype; v_role text; v_ft public.forecast_towell%rowtype; v_decision uuid; v_version uuid;
  v_previous public.adjustment_versions%rowtype; v_revision integer; v_state public.forecast_decision_state := 'reviewed';
  v_vintage public.forecast_vintages%rowtype; v_item jsonb; v_method text; v_reason_code text; v_reason uuid;
  v_explanation text; v_evidence text; v_input numeric; v_adjusted numeric; v_delta numeric; v_percent numeric;
  v_p10 numeric; v_p90 numeric; v_p95 numeric; v_adjustment_id uuid; v_period_status public.period_status;
begin
  if jsonb_typeof(p_adjustments)<>'array' then raise exception 'adjustments_must_be_array'; end if;
  if p_significant_threshold<=0 then raise exception 'invalid_significant_threshold'; end if;
  select u.*,r.code into v_user,v_role from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role not in ('manager','editor') then raise exception 'proposal_role_required'; end if;
  select * into v_ft from public.forecast_towell where id=p_forecast_towell_id and organization_id=v_user.organization_id and state='champion' and published_at is not null;
  if v_ft.id is null then raise exception 'published_forecast_towell_required'; end if;
  insert into public.forecast_decisions(organization_id,chain_id,forecast_towell_id,forecast_cycle,target)
    values(v_ft.organization_id,v_ft.chain_id,v_ft.id,v_ft.cutoff_period,v_ft.target)
    on conflict(organization_id,chain_id,forecast_towell_id) do update set target=excluded.target returning id into v_decision;
  select * into v_previous from public.adjustment_versions where forecast_decision_id=v_decision order by revision desc limit 1;
  v_revision := coalesce(v_previous.revision,0)+1;
  if v_previous.state in ('frozen','evaluated') then
    select status into v_period_status from public.periods where organization_id=v_ft.organization_id and period_start=v_ft.cutoff_period;
    if v_period_status is distinct from 'reopened' then raise exception 'frozen_decision_requires_reopening'; end if;
    if p_correction_reason is null or length(trim(p_correction_reason))=0 then raise exception 'correction_reason_required'; end if;
  end if;
  insert into public.adjustment_versions(forecast_decision_id,previous_version_id,revision,version_label,significant_adjustment_threshold,proposed_by,correction_reason,configuration)
    values(v_decision,v_previous.id,v_revision,'DEC-FENDI-'||to_char(v_ft.cutoff_period,'YYYY-MM')||'-V'||lpad(v_revision::text,2,'0'),p_significant_threshold,v_user.id,p_correction_reason,jsonb_build_object('generative_ai_used',false,'automatic_distribution',false))
    returning id into v_version;

  for v_vintage in select * from public.forecast_vintages where forecast_towell_id=v_ft.id order by horizon,product_id loop
    select value into v_item from jsonb_array_elements(p_adjustments) where value->>'forecast_vintage_id'=v_vintage.id::text limit 1;
    v_method := coalesce(v_item->>'method','none');
    v_reason_code := coalesce(v_item->>'reason','no_adjustment');
    v_explanation := coalesce(nullif(trim(v_item->>'explanation'),''),'Revisado sin ajuste.');
    v_evidence := nullif(trim(v_item->>'evidence_reference'),'');
    v_input := coalesce((v_item->>'value')::numeric,0);
    if v_method not in ('absolute','percentage','final_value','none') then raise exception 'invalid_adjustment_method'; end if;
    select id into v_reason from public.adjustment_reasons where code=v_reason_code and active;
    if v_reason is null then raise exception 'invalid_adjustment_reason'; end if;
    if v_method<>'none' and (v_item->>'explanation' is null or length(trim(v_item->>'explanation'))=0) then raise exception 'adjustment_explanation_required'; end if;
    if v_method='none' and v_reason_code<>'no_adjustment' then raise exception 'no_adjustment_reason_required'; end if;
    v_adjusted := case v_method when 'absolute' then v_vintage.value+v_input when 'percentage' then v_vintage.value*(1+v_input/100) when 'final_value' then v_input else v_vintage.value end;
    if v_adjusted<0 then raise exception 'negative_adjusted_forecast'; end if;
    v_delta := v_adjusted-v_vintage.value;
    v_percent := case when v_vintage.value=0 and v_delta=0 then 0 when v_vintage.value=0 then null else v_delta/v_vintage.value*100 end;
    select p10,p90,p95 into v_p10,v_p90,v_p95 from public.probability_bands where forecast_vintage_id=v_vintage.id;
    if (v_p95 is not null and v_adjusted>v_p95) or (v_p10 is not null and v_adjusted<v_p10) then
      if v_role<>'manager' then raise exception 'outside_band_authorization_required'; end if;
    end if;
    if v_delta<>0 then v_state := 'adjusted'; end if;
    insert into public.forecast_adjustments(adjustment_version_id,forecast_vintage_id,product_id,horizon,method,reason_id,explanation,evidence_reference,forecast_towell,adjustment,adjustment_percent,forecast_adjusted,within_p90,outside_p95,below_p10,significant_adjustment,proposed_by)
      values(v_version,v_vintage.id,v_vintage.product_id,v_vintage.horizon,v_method,v_reason,v_explanation,v_evidence,v_vintage.value,v_delta,v_percent,v_adjusted,
             case when v_p90 is null then null else v_adjusted between coalesce(v_p10,0) and v_p90 end,
             v_p95 is not null and v_adjusted>v_p95,v_p10 is not null and v_adjusted<v_p10,
             v_delta<>0 and (v_percent is null or abs(v_percent)>=p_significant_threshold),v_user.id)
      returning id into v_adjustment_id;
    if v_p95 is not null and v_adjusted>v_p95 or v_p10 is not null and v_adjusted<v_p10 then
      insert into public.decision_governance_alerts(adjustment_version_id,forecast_adjustment_id,alert_type,severity,message,evidence)
        values(v_version,v_adjustment_id,'outside_probability_range','significant','Ajuste fuera del rango probabilístico calculado.',jsonb_build_object('p10',v_p10,'p95',v_p95,'adjusted',v_adjusted));
    end if;
    if v_delta<>0 and (v_percent is null or abs(v_percent)>=p_significant_threshold) then
      insert into public.decision_governance_alerts(adjustment_version_id,forecast_adjustment_id,alert_type,severity,message,evidence)
        values(v_version,v_adjustment_id,'significant_adjustment','warning','Ajuste superior al umbral configurable.',jsonb_build_object('threshold',p_significant_threshold,'adjustment_percent',v_percent));
    end if;
  end loop;
  update public.adjustment_versions set state=v_state where id=v_version;
  insert into public.decision_learning_events(adjustment_version_id,event_type,status,payload) values(v_version,'decision_created','observed',jsonb_build_object('state',v_state,'forecast_towell_changed',false));
  insert into public.decision_audit_log(forecast_decision_id,adjustment_version_id,actor_id,action,new_value,reason)
    values(v_decision,v_version,v_user.id,'decision.version_created',jsonb_build_object('revision',v_revision,'state',v_state,'forecast_towell_id',v_ft.id),p_correction_reason);
  return v_version;
end $$;
grant execute on function public.request_forecast_decision(uuid,jsonb,numeric,text) to authenticated;

create or replace function public.approve_forecast_decision(p_adjustment_version_id uuid,p_comment text default null)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_user public.users%rowtype; v_role text; v_version public.adjustment_versions%rowtype; v_decision uuid; v_approval uuid; v_snapshot jsonb; v_hash text;
begin
  select u.*,r.code into v_user,v_role from public.users u join public.roles r on r.id=u.role_id where u.auth_user_id=auth.uid() and u.active limit 1;
  if v_role<>'manager' then raise exception 'manager_approval_required'; end if;
  select av.* into v_version from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id where av.id=p_adjustment_version_id and fd.organization_id=v_user.organization_id for update;
  if v_version.id is null or v_version.state not in ('reviewed','adjusted','approved') then raise exception 'decision_not_approvable'; end if;
  select jsonb_build_object('version',v_version.version_label,'adjustments',jsonb_agg(jsonb_build_object('id',fa.id,'forecast_towell',fa.forecast_towell,'adjustment',fa.adjustment,'forecast_approved',fa.forecast_adjusted,'reason_id',fa.reason_id,'horizon',fa.horizon) order by fa.horizon,fa.product_id))
    into v_snapshot from public.forecast_adjustments fa where fa.adjustment_version_id=v_version.id;
  v_hash := encode(digest(v_snapshot::text,'sha256'),'hex');
  insert into public.forecast_approvals(adjustment_version_id,approved_by,approval_comment,frozen,approved_snapshot,approved_snapshot_hash)
    values(v_version.id,v_user.id,p_comment,true,v_snapshot,v_hash) returning id into v_approval;
  update public.adjustment_versions set state='frozen',decision_hash=v_hash where id=v_version.id;
  v_decision := v_version.forecast_decision_id;
  insert into public.decision_learning_events(adjustment_version_id,event_type,status,payload) values(v_version.id,'decision_frozen','completed',jsonb_build_object('approved_by',v_user.id,'forecast_towell_changed',false));
  insert into public.decision_audit_log(forecast_decision_id,adjustment_version_id,actor_id,action,new_value)
    values(v_decision,v_version.id,v_user.id,'decision.approved_and_frozen',jsonb_build_object('approval_id',v_approval,'snapshot_hash',v_hash));
  return v_approval;
end $$;
grant execute on function public.approve_forecast_decision(uuid,text) to authenticated;

create or replace view public.current_forecast_decisions with (security_invoker=true) as
select distinct on (fd.id) fd.id forecast_decision_id,fd.organization_id,fd.chain_id,fd.forecast_towell_id,fd.forecast_cycle,fd.target,
       av.id adjustment_version_id,av.version_label,av.revision,av.state,av.proposed_by,av.proposed_at,fa.approved_by,fa.approved_at,fa.frozen
from public.forecast_decisions fd join public.adjustment_versions av on av.forecast_decision_id=fd.id
left join public.forecast_approvals fa on fa.adjustment_version_id=av.id
order by fd.id,av.revision desc;
grant select on public.current_forecast_decisions to authenticated;

do $$ declare table_name text;
begin
  foreach table_name in array array['adjustment_reasons','forecast_decisions','adjustment_versions','forecast_adjustments','forecast_approvals','human_fva_metrics','decision_learning_events','decision_governance_alerts','decision_audit_log']
  loop execute format('alter table public.%I enable row level security',table_name); end loop;
end $$;
drop policy if exists decision_reason_read on public.adjustment_reasons;
create policy decision_reason_read on public.adjustment_reasons for select using (true);
drop policy if exists decision_read on public.forecast_decisions;
create policy decision_read on public.forecast_decisions for select using (exists(select 1 from public.users u where u.auth_user_id=auth.uid() and u.active and u.organization_id=forecast_decisions.organization_id));
drop policy if exists decision_read on public.adjustment_versions;
create policy decision_read on public.adjustment_versions for select using (exists(select 1 from public.forecast_decisions fd join public.users u on u.organization_id=fd.organization_id where fd.id=adjustment_versions.forecast_decision_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.forecast_adjustments;
create policy decision_read on public.forecast_adjustments for select using (exists(select 1 from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id join public.users u on u.organization_id=fd.organization_id where av.id=forecast_adjustments.adjustment_version_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.forecast_approvals;
create policy decision_read on public.forecast_approvals for select using (exists(select 1 from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id join public.users u on u.organization_id=fd.organization_id where av.id=forecast_approvals.adjustment_version_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.human_fva_metrics;
create policy decision_read on public.human_fva_metrics for select using (exists(select 1 from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id join public.users u on u.organization_id=fd.organization_id where av.id=human_fva_metrics.adjustment_version_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.decision_learning_events;
create policy decision_read on public.decision_learning_events for select using (exists(select 1 from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id join public.users u on u.organization_id=fd.organization_id where av.id=decision_learning_events.adjustment_version_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.decision_governance_alerts;
create policy decision_read on public.decision_governance_alerts for select using (exists(select 1 from public.adjustment_versions av join public.forecast_decisions fd on fd.id=av.forecast_decision_id join public.users u on u.organization_id=fd.organization_id where av.id=decision_governance_alerts.adjustment_version_id and u.auth_user_id=auth.uid() and u.active));
drop policy if exists decision_read on public.decision_audit_log;
create policy decision_read on public.decision_audit_log for select using (exists(select 1 from public.forecast_decisions fd join public.users u on u.organization_id=fd.organization_id where fd.id=decision_audit_log.forecast_decision_id and u.auth_user_id=auth.uid() and u.active));
