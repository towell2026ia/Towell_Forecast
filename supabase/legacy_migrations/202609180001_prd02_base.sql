create extension if not exists pgcrypto;

do $$ begin
  create type public.period_status as enum ('open','validating','closed','reopened');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.record_state as enum ('current','corrected','cancelled','rejected');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.migration_status as enum ('prepared','validating','approved','published','disabled');
exception when duplicate_object then null; end $$;

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now()
);
create table if not exists public.chains (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  code text not null,
  name text not null,
  unique (organization_id, code)
);
create table if not exists public.formats (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id),
  code text not null,
  name text not null,
  unique (chain_id, code)
);
create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  family text not null,
  description text not null,
  unit text not null default 'piezas',
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists public.product_identifiers (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.products(id),
  identifier_type text not null check (identifier_type in ('UPC','ITEM')),
  identifier_value text not null check (length(identifier_value) > 0),
  valid_from date,
  valid_to date,
  unique (identifier_type, identifier_value)
);
create table if not exists public.product_variants (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.products(id),
  color text,
  presentation text,
  weight text,
  unique (product_id, color, presentation, weight)
);
create unique index if not exists products_org_description_key on public.products(organization_id, description);
create table if not exists public.periods (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  period_start date not null,
  status public.period_status not null default 'open',
  closed_at timestamptz,
  closed_by uuid,
  reopened_at timestamptz,
  reopened_by uuid,
  reopen_reason text,
  unique (organization_id, period_start),
  check (date_trunc('month', period_start)::date = period_start)
);
create table if not exists public.roles (
  id uuid primary key default gen_random_uuid(),
  code text not null unique check (code in ('manager','editor','reader')),
  name text not null
);
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  auth_user_id uuid unique,
  email text not null unique,
  display_name text not null,
  role_id uuid not null references public.roles(id),
  active boolean not null default true,
  access_scope jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  last_activity_at timestamptz
);

alter table public.periods drop constraint if exists periods_closed_by_fkey;
alter table public.periods add constraint periods_closed_by_fkey foreign key (closed_by) references public.users(id);
alter table public.periods drop constraint if exists periods_reopened_by_fkey;
alter table public.periods add constraint periods_reopened_by_fkey foreign key (reopened_by) references public.users(id);

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  period_id uuid not null references public.periods(id),
  order_reference text not null,
  received_date date not null,
  required_date date,
  comments text,
  state public.record_state not null default 'current',
  version integer not null default 1,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (organization_id, order_reference, version)
);
create table if not exists public.order_lines (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.orders(id),
  product_id uuid not null references public.products(id),
  quantity numeric(18,3) not null check (quantity >= 0),
  unit text not null default 'piezas',
  comments text,
  unique (order_id, product_id)
);
create table if not exists public.sales (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  period_id uuid not null references public.periods(id),
  product_id uuid not null references public.products(id),
  quantity numeric(18,3) not null check (quantity >= 0),
  amount numeric(18,2),
  status text not null check (status in ('partial','final')),
  comments text,
  state public.record_state not null default 'current',
  version integer not null default 1,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now()
);
create table if not exists public.deliveries (
  id uuid primary key default gen_random_uuid(),
  order_line_id uuid not null references public.order_lines(id),
  delivery_date date not null,
  quantity numeric(18,3) not null check (quantity >= 0),
  comments text,
  state public.record_state not null default 'current',
  version integer not null default 1,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now()
);
create table if not exists public.customer_forecasts (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  chain_id uuid not null references public.chains(id),
  period_id uuid not null references public.periods(id),
  product_id uuid not null references public.products(id),
  received_at date not null,
  quantity numeric(18,3) not null check (quantity >= 0),
  source_version text not null,
  valid_until date,
  comments text,
  state public.record_state not null default 'current',
  version integer not null default 1,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now()
);

create table if not exists public.source_files (
  id uuid primary key default gen_random_uuid(),
  filename text not null,
  file_hash text not null unique,
  source_version text,
  protected_evidence_path text,
  registered_at timestamptz not null default now()
);
create table if not exists public.source_sheets (
  id uuid primary key default gen_random_uuid(),
  source_file_id uuid not null references public.source_files(id),
  sheet_name text not null,
  approved boolean not null default false,
  unique (source_file_id, sheet_name)
);
create table if not exists public.migration_batches (
  id uuid primary key default gen_random_uuid(),
  batch_key text not null unique,
  status public.migration_status not null default 'prepared',
  input_contract_version text not null,
  started_at timestamptz not null default now(),
  approved_at timestamptz,
  approved_by uuid references public.users(id),
  published_at timestamptz,
  import_disabled_at timestamptz,
  reconciliation jsonb not null default '{}'::jsonb
);
create table if not exists public.migration_records (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.migration_batches(id),
  source_file_id uuid not null references public.source_files(id),
  source_sheet text not null,
  source_row text,
  record_hash text not null,
  item text,
  upc text,
  period_start date,
  metric text,
  quantity numeric(18,3),
  source_payload jsonb not null,
  disposition text not null default 'prepared' check (disposition in ('prepared','canonical','quarantined','rejected')),
  unique (batch_id, record_hash)
);
create table if not exists public.migration_issues (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.migration_batches(id),
  migration_record_id uuid references public.migration_records(id),
  issue_code text not null,
  severity text not null check (severity in ('info','warning','blocking')),
  details jsonb not null,
  status text not null default 'open' check (status in ('open','resolved','accepted')),
  reviewed_by uuid references public.users(id),
  reviewed_at timestamptz
);
create unique index if not exists migration_issues_batch_code_key on public.migration_issues(batch_id,issue_code);

create table if not exists public.record_versions (
  id uuid primary key default gen_random_uuid(),
  table_name text not null,
  record_id uuid not null,
  version integer not null,
  previous_data jsonb,
  current_data jsonb not null,
  reason text,
  actor_id uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (table_name, record_id, version)
);
create table if not exists public.approval_records (
  id uuid primary key default gen_random_uuid(),
  object_type text not null,
  object_id uuid not null,
  status text not null check (status in ('pending','approved','rejected')),
  reason text,
  actor_id uuid references public.users(id),
  created_at timestamptz not null default now()
);
create table if not exists public.audit_log (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references public.users(id),
  action text not null,
  table_name text not null,
  record_id uuid,
  previous_value jsonb,
  new_value jsonb,
  reason text,
  period_id uuid references public.periods(id),
  created_at timestamptz not null default now()
);
create table if not exists public.domain_events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  aggregate_type text not null,
  aggregate_id uuid not null,
  payload jsonb not null,
  actor_id uuid references public.users(id),
  occurred_at timestamptz not null default now(),
  consumed_at timestamptz
);

create table if not exists public.forecast_runs (id uuid primary key default gen_random_uuid(), created_at timestamptz not null default now());
create table if not exists public.forecast_versions (id uuid primary key default gen_random_uuid(), forecast_run_id uuid references public.forecast_runs(id), version integer not null);
create table if not exists public.forecast_values (id uuid primary key default gen_random_uuid(), forecast_version_id uuid references public.forecast_versions(id), product_id uuid references public.products(id), period_id uuid references public.periods(id), value numeric(18,3));

insert into public.roles (code, name) values ('manager','Gerente'),('editor','Editor'),('reader','Lector') on conflict (code) do update set name=excluded.name;
insert into public.organizations (name) values ('Towell') on conflict (name) do nothing;
insert into public.chains (organization_id, code, name)
select id, 'WM', 'Walmart' from public.organizations where name='Towell' on conflict (organization_id, code) do update set name=excluded.name;
insert into public.formats (chain_id, code, name)
select id, 'BD', 'BD - definición pendiente' from public.chains where code='WM' on conflict (chain_id, code) do update set name=excluded.name;

insert into public.products (organization_id,family,description,unit)
select o.id,'FENDI',x.description,'piezas' from public.organizations o cross join (values
('TOALLA MB FENDI CHOCOLATE'),('TOALLA MB FENDI MORADO'),('TOALLA MB FENDI AZUL'),('TOALLA MB FENDI AQUA'),
('TOALLA MB FENDI GRIS'),('TOALLA MB FENDI BEIGE'),('TOALLA MB FENDI ROSA'),('TOALLA MB FENDI ROJO'),
('TOALLA MB FENDI NEGRO'),('TOALLA MB FENDI NAVIDAD'),('TOALLA MB FENDI OXFORD')
) as x(description) where o.name='Towell'
on conflict (organization_id,description) do update set family=excluded.family, unit=excluded.unit;

insert into public.product_identifiers(product_id,identifier_type,identifier_value)
select p.id,x.identifier_type,x.identifier_value from public.products p join (values
('TOALLA MB FENDI CHOCOLATE','ITEM','101285353'),('TOALLA MB FENDI CHOCOLATE','UPC','7501897813122'),
('TOALLA MB FENDI MORADO','ITEM','101285349'),('TOALLA MB FENDI MORADO','UPC','7501897813139'),
('TOALLA MB FENDI AZUL','ITEM','101285357'),('TOALLA MB FENDI AZUL','UPC','7501897813146'),
('TOALLA MB FENDI AQUA','ITEM','101285351'),('TOALLA MB FENDI AQUA','UPC','7501897813153'),
('TOALLA MB FENDI GRIS','ITEM','101285355'),('TOALLA MB FENDI GRIS','UPC','7501897813160'),
('TOALLA MB FENDI BEIGE','ITEM','101285348'),('TOALLA MB FENDI BEIGE','UPC','7501897813177'),
('TOALLA MB FENDI ROSA','ITEM','101285354'),('TOALLA MB FENDI ROSA','UPC','7501897813184'),
('TOALLA MB FENDI ROJO','ITEM','101285356'),('TOALLA MB FENDI ROJO','UPC','7501897813191'),
('TOALLA MB FENDI NEGRO','ITEM','101558813'),('TOALLA MB FENDI NEGRO','UPC','7501897816260'),
('TOALLA MB FENDI NAVIDAD','ITEM','101558815'),('TOALLA MB FENDI NAVIDAD','UPC','7501897816277'),
('TOALLA MB FENDI OXFORD','ITEM','101558814'),('TOALLA MB FENDI OXFORD','UPC','7501897816314')
) as x(description,identifier_type,identifier_value) on x.description=p.description
on conflict (identifier_type,identifier_value) do update set product_id=excluded.product_id;

insert into public.product_variants(product_id,color,presentation,weight)
select p.id,replace(p.description,'TOALLA MB FENDI ',''),'75X150','480g' from public.products p where p.family='FENDI'
on conflict (product_id,color,presentation,weight) do nothing;

insert into public.users (organization_id,email,display_name,role_id,active)
select o.id, x.email, x.display_name, r.id, x.active from public.organizations o cross join public.roles r cross join (values
('gerencia1@towell.local','Gerencia 1',false),('gerencia2@towell.local','Gerencia 2',false),('gerencia3@towell.local','Gerencia 3',false)
) as x(email,display_name,active) where o.name='Towell' and r.code='manager'
on conflict (email) do update set display_name=excluded.display_name, role_id=excluded.role_id;

insert into public.periods (organization_id,period_start,status)
select o.id, d::date, case when d::date='2026-09-01' then 'open'::public.period_status else 'closed'::public.period_status end
from public.organizations o cross join generate_series('2024-01-01'::date,'2026-09-01'::date,'1 month') d where o.name='Towell'
on conflict (organization_id,period_start) do nothing;

create or replace function public.capture_operational_record(p_record_type text, p_payload jsonb, p_actor_email text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare
  v_user public.users%rowtype; v_period public.periods%rowtype; v_product_id uuid; v_chain_id uuid;
  v_id uuid; v_order_id uuid; v_order_line_id uuid; v_order_qty numeric; v_delivered numeric;
  v_qty numeric; v_type text; v_event text; v_table text;
begin
  select u.* into v_user from public.users u where lower(u.email)=lower(p_actor_email) and u.active;
  if v_user.id is null then raise exception 'user_not_authorized'; end if;
  if not exists (select 1 from public.roles r where r.id=v_user.role_id and r.code in ('manager','editor')) then raise exception 'write_role_required'; end if;
  select * into v_period from public.periods where organization_id=v_user.organization_id and period_start=((p_payload->>'period')||'-01')::date;
  if v_period.id is null then raise exception 'period_not_found'; end if;
  if v_period.status not in ('open','reopened') then raise exception 'period_not_editable'; end if;
  select pi.product_id into v_product_id from public.product_identifiers pi where pi.identifier_type='UPC' and pi.identifier_value=p_payload->>'product';
  if v_product_id is null then raise exception 'product_not_in_pilot'; end if;
  select c.id into v_chain_id from public.chains c where c.organization_id=v_user.organization_id and c.code='WM';
  v_qty := (p_payload->>'quantity')::numeric;
  if v_qty < 0 then raise exception 'quantity_must_be_nonnegative'; end if;
  v_type := lower(p_record_type);
  if v_type='pedido' then
    insert into public.orders(organization_id,chain_id,period_id,order_reference,received_date,required_date,comments,created_by)
    values(v_user.organization_id,v_chain_id,v_period.id,p_payload->>'reference',(p_payload->>'event_date')::date,nullif(p_payload->>'required_date','')::date,p_payload->>'comments',v_user.id)
    returning id into v_order_id;
    insert into public.order_lines(order_id,product_id,quantity,unit,comments)
    values(v_order_id,v_product_id,v_qty,coalesce(nullif(p_payload->>'unit',''),'piezas'),p_payload->>'comments') returning id into v_id;
    v_event:='order.created'; v_table:='order_lines';
  elsif v_type='venta' then
    insert into public.sales(organization_id,chain_id,period_id,product_id,quantity,amount,status,comments,created_by)
    values(v_user.organization_id,v_chain_id,v_period.id,v_product_id,v_qty,nullif(p_payload->>'amount','')::numeric,coalesce(nullif(p_payload->>'status',''),'partial'),p_payload->>'comments',v_user.id) returning id into v_id;
    v_event:='sale.created'; v_table:='sales';
  elsif v_type='entrega' then
    select ol.id, ol.quantity into v_order_line_id, v_order_qty
    from public.order_lines ol join public.orders o on o.id=ol.order_id
    where o.organization_id=v_user.organization_id and o.order_reference=p_payload->>'order_reference' and ol.product_id=v_product_id and o.state='current'
    order by o.version desc limit 1;
    if v_order_line_id is null then raise exception 'order_line_not_found'; end if;
    select coalesce(sum(d.quantity),0) into v_delivered from public.deliveries d where d.order_line_id=v_order_line_id and d.state='current';
    if v_delivered + v_qty > v_order_qty then raise exception 'delivery_exceeds_pending_balance'; end if;
    insert into public.deliveries(order_line_id,delivery_date,quantity,comments,created_by)
    values(v_order_line_id,(p_payload->>'delivery_date')::date,v_qty,p_payload->>'comments',v_user.id) returning id into v_id;
    v_event:='delivery.created'; v_table:='deliveries';
  elsif v_type='fcst' then
    insert into public.customer_forecasts(organization_id,chain_id,period_id,product_id,received_at,quantity,source_version,valid_until,comments,created_by)
    values(v_user.organization_id,v_chain_id,v_period.id,v_product_id,(p_payload->>'received_at')::date,v_qty,p_payload->>'version',nullif(p_payload->>'valid_until','')::date,p_payload->>'comments',v_user.id) returning id into v_id;
    v_event:='customer_forecast.created'; v_table:='customer_forecasts';
  else
    raise exception 'record_type_requires_transactional_endpoint';
  end if;
  insert into public.record_versions(table_name,record_id,version,current_data,actor_id) values(v_table,v_id,1,p_payload,v_user.id);
  insert into public.audit_log(actor_id,action,table_name,record_id,new_value,period_id) values(v_user.id,'created',v_table,v_id,p_payload,v_period.id);
  insert into public.domain_events(event_type,aggregate_type,aggregate_id,payload,actor_id) values(v_event,v_type,v_id,p_payload,v_user.id);
  update public.users set last_activity_at=now() where id=v_user.id;
  return jsonb_build_object('id',v_id,'event',v_event,'version',1);
end $$;

alter table public.organizations enable row level security;
alter table public.chains enable row level security;
alter table public.formats enable row level security;
alter table public.products enable row level security;
alter table public.product_identifiers enable row level security;
alter table public.product_variants enable row level security;
alter table public.periods enable row level security;
alter table public.orders enable row level security;
alter table public.order_lines enable row level security;
alter table public.sales enable row level security;
alter table public.deliveries enable row level security;
alter table public.customer_forecasts enable row level security;
alter table public.audit_log enable row level security;
alter table public.domain_events enable row level security;

do $$ begin
  create policy authenticated_read_products on public.products for select to authenticated using (true);
exception when duplicate_object then null; end $$;
do $$ begin
  create policy authenticated_read_identifiers on public.product_identifiers for select to authenticated using (true);
exception when duplicate_object then null; end $$;
do $$ begin
  create policy authenticated_read_periods on public.periods for select to authenticated using (true);
exception when duplicate_object then null; end $$;

revoke all on function public.capture_operational_record(text,jsonb,text) from public, anon, authenticated;
grant execute on function public.capture_operational_record(text,jsonb,text) to service_role;

insert into storage.buckets (id,name,public,file_size_limit,allowed_mime_types)
values ('migration-evidence','migration-evidence',false,52428800,array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
on conflict (id) do update set public=false;

do $$ begin
  create policy manager_read_migration_evidence on storage.objects for select to authenticated
  using (bucket_id='migration-evidence' and exists (
    select 1 from public.users u join public.roles r on r.id=u.role_id
    where u.auth_user_id=auth.uid() and u.active and r.code='manager'
  ));
exception when duplicate_object then null; end $$;
