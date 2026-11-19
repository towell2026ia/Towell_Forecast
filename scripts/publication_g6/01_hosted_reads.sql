-- Additive publication. The previous portal and all its physical records stay intact.
-- Installed as a single transaction; no release is visible upon installation.
create table private.ft_g6_release (
  run_id uuid primary key,
  context jsonb not null,
  metadata jsonb not null,
  file_sha256 text not null check(file_sha256 ~ '^[a-f0-9]{64}$'),
  content_digest text not null check(content_digest ~ '^[a-f0-9]{64}$'),
  active boolean not null default false,
  check(context ?& array['forecast_contract','objective','target_metric','unit','generation','training_cutoff',
    'observed_cutoff','planning_start','planning_end','source_hash','authorized_source_hash']),
  check (context->>'forecast_contract' = 'pedido-unificado-v1' and context->>'objective' = 'Pedido'
    and context->>'target_metric' = 'ORDER' and context->>'unit' = 'PIEZAS'
    and context->>'generation' = '6' and context->>'training_cutoff' = '2026-12'
    and context->>'observed_cutoff' = '2026-09' and context->>'planning_start' = '2027-01'
    and context->>'planning_end' = '2027-12' and context->>'source_hash' = context->>'authorized_source_hash'),
  check (not metadata ?| array['products','models','audits'])
);
create unique index ft_g6_single_active on private.ft_g6_release(active) where active;
-- Scope UUIDs come from the sealed catalog. Authorization still uses the hosted
-- ACL for precisely that UUID; a new scope never implies an inherited permission.
create table private.ft_g6_scope (
  run_id uuid not null references private.ft_g6_release, chain_id uuid not null,
  primary key(run_id,chain_id)
);
alter table private.ft_g6_scope enable row level security;
revoke all on private.ft_g6_scope from public,anon,authenticated,service_role;
create table private.ft_g6_product (
  run_id uuid not null references private.ft_g6_release, chain_id uuid not null,
  product_id uuid not null, payload jsonb not null, primary key(run_id,product_id),
  foreign key(run_id,chain_id) references private.ft_g6_scope,
  check(payload ?& array['product_id','chain_id'] and payload->>'product_id' = product_id::text and payload->>'chain_id' = chain_id::text)
);
create table private.ft_g6_row (
  run_id uuid not null references private.ft_g6_release, resource text not null,
  record_key text not null, chain_id uuid not null, product_id uuid,
  period text, payload jsonb not null, primary key(run_id,resource,record_key),
  foreign key(run_id,chain_id) references private.ft_g6_scope,
  check(jsonb_typeof(payload)='object'),
  check(payload->>'chain_id' is null or payload->>'chain_id'=chain_id::text),
  check(payload->>'product_id' is null or payload->>'product_id'=product_id::text),
  check(resource in ('chains','categories','products','import_profile_versions','catalog','observations','inventory','fulfillment','periods','calculations','horizons','decisions','live'))
);
create index ft_g6_scope_rows on private.ft_g6_row(run_id,resource,chain_id,product_id,period);
create index ft_g6_product_rows on private.ft_g6_row(run_id,resource,product_id,period);
-- Owner-only, byte-exact archive of the signed release inputs. Never exposed by the Data API.
create table private.ft_g6_archive (
  run_id uuid not null references private.ft_g6_release, part integer not null check(part>=0),
  data bytea not null, sha256 text not null check(encode(pg_catalog.sha256(data),'hex')=sha256),
  primary key(run_id,part)
);
alter table private.ft_g6_archive enable row level security;
revoke all on private.ft_g6_archive from public,anon,authenticated,service_role;
-- Only the loader can access these hashes. Each receipt is committed with its batch.
create table private.ft_g6_receipt (
  run_id uuid not null references private.ft_g6_release, resource text not null, batch_no integer not null,
  content_sha256 text not null check(content_sha256 ~ '^[a-f0-9]{64}$'), row_count integer not null check(row_count>0),
  primary key(run_id,resource,batch_no)
);
alter table private.ft_g6_release enable row level security;
alter table private.ft_g6_product enable row level security;
alter table private.ft_g6_row enable row level security;
alter table private.ft_g6_receipt enable row level security;
revoke all on private.ft_g6_release,private.ft_g6_product,private.ft_g6_row,private.ft_g6_receipt from public,anon,authenticated,service_role;
grant select on private.ft_g6_release,private.ft_g6_product,private.ft_g6_row to authenticated;
create policy ft_g6_active_release on private.ft_g6_release for select to authenticated using
  (active and exists(select 1 from public.profiles p where p.id=(select auth.uid()) and p.status='ACTIVE'));
create policy ft_g6_visible_product on private.ft_g6_product for select to authenticated using (
  run_id in (select r.run_id from private.ft_g6_release r where r.active) and private.can_chain(chain_id,'view'));
create policy ft_g6_visible_row on private.ft_g6_row for select to authenticated using (
  run_id in (select r.run_id from private.ft_g6_release r where r.active) and private.can_chain(chain_id,'view'));

create function private.ft_g6_pinned(p_source_hash text,p_generation bigint) returns uuid
language plpgsql stable security invoker set search_path='' as $$
declare rid uuid;
begin
  select run_id into rid from private.ft_g6_release r where r.active
    and r.context->>'source_hash'=p_source_hash and (r.context->>'generation')::bigint=p_generation;
  if rid is null then raise exception 'OFFICIAL_SOURCE_MISMATCH' using errcode='40001'; end if;
  return rid;
end $$;
revoke all on function private.ft_g6_pinned(text,bigint) from public,anon,service_role;
grant execute on function private.ft_g6_pinned(text,bigint) to authenticated;

create function public.operational_read_context() returns setof jsonb
language sql stable security invoker set search_path='' as $$
  select context from private.ft_g6_release where active
$$;
create function public.operational_read_observations(
  p_source_hash text,p_generation bigint,p_mode text default 'OPERATIONAL', p_available_through timestamptz default null,
  p_chain_id uuid default null,p_product_ids uuid[] default null,p_from date default null,p_to date default null,
  p_metric_codes text[] default null,p_observation_ids uuid[] default null,p_format text default null,p_purchase_type text default 'RS'
) returns table(id uuid,observation_id uuid,chain_id uuid,chain_code text,chain_name text,
  product_id uuid,product_code text,variant_code text,product_description text,category_id uuid,
  period date,metric_code text,value numeric,value_text text,version_no integer,availability_source text,
  available_at timestamptz,source_batch_id uuid,availability_evidence_id uuid,source_reference text,
  source_value_literal text,format text,upc text,purchase_type text)
language sql stable security invoker set search_path='' as $$
  select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(
    id uuid,observation_id uuid,chain_id uuid,chain_code text,chain_name text,product_id uuid,product_code text,
    variant_code text,product_description text,category_id uuid,period date,metric_code text,value numeric,
    value_text text,version_no integer,availability_source text,available_at timestamptz,source_batch_id uuid,
    availability_evidence_id uuid,source_reference text,source_value_literal text,format text,upc text,purchase_type text)
  where r.resource='observations' and r.run_id=private.ft_g6_pinned(p_source_hash,p_generation)
    and (p_chain_id is null or r.chain_id=p_chain_id) and (p_product_ids is null or r.product_id=any(p_product_ids))
    and (p_from is null or v.period>=p_from) and (p_to is null or v.period<=p_to)
    and (p_metric_codes is null or v.metric_code=any(p_metric_codes)) and (p_observation_ids is null or v.id=any(p_observation_ids))
    and (p_format is null or v.format=p_format) and v.purchase_type=p_purchase_type and p_purchase_type='RS'
    and (p_mode in ('AUDIT','OPERATIONAL') or (p_mode='TEMPORAL' and v.available_at<=p_available_through))
$$;
create function public.operational_read_inventory(p_source_hash text,p_generation bigint,p_chain_id uuid default null,
  p_product_ids uuid[] default null,p_from date default null,p_to date default null,p_format text default null,p_purchase_type text default 'RS')
returns table(chain_id uuid,product_id uuid,period date,format text,upc text,purchase_type text,quantity numeric,quantity_text text,source_reference text)
language sql stable security invoker set search_path='' as $$
  select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(chain_id uuid,product_id uuid,
    period date,format text,upc text,purchase_type text,quantity numeric,quantity_text text,source_reference text)
  where r.resource='inventory' and r.run_id=private.ft_g6_pinned(p_source_hash,p_generation)
    and (p_chain_id is null or r.chain_id=p_chain_id) and (p_product_ids is null or r.product_id=any(p_product_ids))
    and (p_from is null or v.period>=p_from) and (p_to is null or v.period<=p_to)
    and (p_format is null or v.format=p_format) and v.purchase_type=p_purchase_type and p_purchase_type='RS'
$$;
create function public.operational_read_fulfillment(p_source_hash text,p_generation bigint,p_chain_id uuid default null,
  p_product_ids uuid[] default null,p_from date default null,p_to date default null,p_format text default null,p_purchase_type text default 'RS')
returns table(chain_id uuid,product_id uuid,period date,format text,upc text,purchase_type text,order_text text,delivery_text text,
  source_reference text,order_source_reference text,delivery_source_reference text,source_sha256 text,partial_period boolean,
  reported_fill_rate_pct numeric,reported_fill_rate_pct_text text)
language sql stable security invoker set search_path='' as $$
  select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(chain_id uuid,product_id uuid,
    period date,format text,upc text,purchase_type text,order_text text,delivery_text text,source_reference text,
    order_source_reference text,delivery_source_reference text,source_sha256 text,partial_period boolean,
    reported_fill_rate_pct numeric,reported_fill_rate_pct_text text)
  where r.resource='fulfillment' and r.run_id=private.ft_g6_pinned(p_source_hash,p_generation)
    and (p_chain_id is null or r.chain_id=p_chain_id) and (p_product_ids is null or r.product_id=any(p_product_ids))
    and (p_from is null or v.period>=p_from) and (p_to is null or v.period<=p_to)
    and (p_format is null or v.format=p_format) and v.purchase_type=p_purchase_type and p_purchase_type='RS'
$$;
create function public.operational_read_periods(p_source_hash text,p_generation bigint,p_chain_id uuid default null)
returns table(period date) language sql stable security invoker set search_path='' as $$
  select distinct r.period::date from private.ft_g6_row r where r.resource='periods'
    and r.run_id=private.ft_g6_pinned(p_source_hash,p_generation) and (p_chain_id is null or r.chain_id=p_chain_id)
$$;
create function public.operational_read_catalog(p_source_hash text,p_generation bigint,p_chain_id uuid default null,p_as_of date default null)
returns table(chain_id uuid,product_id uuid,format text,upc text,purchase_type text,model text,presentation text,
  color text,product_type text,commercial_code text,account text,attributes jsonb)
language sql stable security invoker set search_path='' as $$
  select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(chain_id uuid,product_id uuid,
    format text,upc text,purchase_type text,model text,presentation text,color text,product_type text,commercial_code text,account text,attributes jsonb)
  where r.resource='catalog' and r.run_id=private.ft_g6_pinned(p_source_hash,p_generation) and (p_chain_id is null or r.chain_id=p_chain_id)
    and (p_as_of is null or exists(select 1 from private.ft_g6_row h where h.run_id=r.run_id and h.product_id=r.product_id
      and h.chain_id=r.chain_id and h.resource in ('observations','inventory') and h.period::date<=p_as_of))
$$;

-- Named read projections preserve the existing PostgREST column selection, pagination and types.
create view public.ft_g6_products with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(
 id uuid,chain_id uuid,category_id uuid,product_code text,variant_code text,description text,status text)
 where r.resource='products';
create view public.ft_g6_chains with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(id uuid,code text,name text,status text)
 where r.resource='chains';
create view public.ft_g6_categories with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(id uuid,chain_id uuid,name text)
 where r.resource='categories';
create view public.ft_g6_import_profile_versions with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(id uuid,chain_id uuid,version integer,mapping_json jsonb)
 where r.resource='import_profile_versions';
create view public.ft_g6_forecast_calculations with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(
 id uuid,chain_id uuid,product_id uuid,objective text,calculation_no integer,calculation_code text,issue_period date,
 cutoff_at timestamptz,status text,created_at timestamptz,created_by uuid,preview_id text,data_snapshot_hash text,
 content_hash text,input_snapshot jsonb,candidate_metrics jsonb,research_snapshot jsonb,suggested_reference jsonb,
 scope_leader jsonb,evidence_mode text,engine_version text,git_sha text,recalculation_reason text)
 where r.resource='calculations';
create view public.ft_g6_forecast_calculation_horizons with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_to_record(r.payload) as v(
 id uuid,calculation_id uuid,chain_id uuid,product_id uuid,horizon integer,target_period date,statistical_value numeric,
 ml_value numeric,ensemble_value numeric,ensemble_ml_component numeric,statistical_model text,ml_model text,
 ensemble_ml_model text,statistical_weight numeric,ml_weight numeric,p10 numeric,p50 numeric,p90 numeric,p95 numeric,
 band_basis text,band_observations integer,band_status text) where r.resource='horizons';
create view public.ft_g6_forecast_selection_events with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_populate_record(null::public.forecast_selection_events,r.payload) v
 where r.resource='decisions';
create view public.ft_g6_forecast_live_evaluations with(security_invoker=true) as
 select v.* from private.ft_g6_row r cross join lateral jsonb_populate_record(null::public.forecast_live_evaluations,r.payload) v
 where r.resource='live';

do $permissions$
declare v text; f regprocedure;
begin
  foreach v in array array['products','chains','categories','import_profile_versions','forecast_calculations',
    'forecast_calculation_horizons','forecast_selection_events','forecast_live_evaluations'] loop
    execute format('revoke all on public.%I from public,anon,authenticated,service_role','ft_g6_'||v);
    execute format('grant select on public.%I to authenticated','ft_g6_'||v);
  end loop;
  for f in select p.oid::regprocedure from pg_catalog.pg_proc p where p.pronamespace='public'::regnamespace
    and p.proname in ('operational_read_context','operational_read_catalog','operational_read_periods',
    'operational_read_observations','operational_read_inventory','operational_read_fulfillment') loop
    execute format('revoke all on function %s from public,anon,authenticated,service_role',f);
    execute format('grant execute on function %s to authenticated',f);
  end loop;
end $permissions$;
