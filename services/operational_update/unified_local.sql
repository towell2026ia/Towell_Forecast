-- LOCAL ONLY: additive unified-source lineage and real client-sales metric.
-- Guarded by the same owner/marker as the approved official-source writer.
do $$ begin
 if current_database() not like 'gateb_official_%'
    or current_setting('towell.official_database',true) is distinct from current_database()
    or inet_server_addr() is distinct from '127.0.0.1'::inet
 then raise exception 'UNIFIED_LOCAL_LAB_REQUIRED'; end if;
end $$;
create table if not exists private.unified_source_rows(
 source_hash text not null, sheet text not null, source_row integer not null,
 payload jsonb not null, row_sha256 text not null,
 primary key(source_hash,sheet,source_row));
create table if not exists private.unified_catalog(
 source_hash text not null, chain_id uuid not null, product_id uuid not null,
 format text not null, upc text not null, attributes jsonb not null,
 source_identity_key text not null, source_row integer not null,
 primary key(source_hash,chain_id,product_id), unique(source_hash,source_identity_key),
 foreign key(product_id,chain_id) references public.products(id,chain_id));
alter table private.unified_source_rows enable row level security;
alter table private.unified_catalog enable row level security;
revoke all on private.unified_source_rows,private.unified_catalog from public,anon,authenticated,service_role;
alter table public.monthly_observations drop constraint if exists monthly_observations_metric_code_check;
alter table public.monthly_observations add constraint monthly_observations_metric_code_check
 check(metric_code in ('SALES','ORDER','DELIVERY','FCST_TOWELL_HISTORY','FCST_CLIENTE_HISTORY','SALES_CLIENTE'));
create or replace function public.operational_read_catalog(p_source_hash text,p_generation bigint,p_chain_id uuid default null,p_as_of date default null)
returns table(chain_id uuid,product_id uuid,format text,upc text,purchase_type text,
 model text,presentation text,color text,product_type text,commercial_code text,account text,attributes jsonb)
language plpgsql stable security definer set search_path='' as $$
begin
 perform private.official_assert_context(p_source_hash,p_generation);
 return query
 with raw as (
  select o.chain_id,o.product_id,o.period,s.format,s.upc,s.attributes
  from public.official_source_current active
  join public.import_batches b on b.official_source_hash=active.source_hash and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
  join public.monthly_observations o on o.source_batch_id=b.id
  join private.official_observation_source s on s.observation_id=o.id
  where active.singleton and private.can_chain(o.chain_id,'view') and (p_chain_id is null or o.chain_id=p_chain_id)
   and o.period<=coalesce(p_as_of,active.last_period)
  union all
  select i.chain_id,i.product_id,i.period,i.format,i.upc,i.attributes
  from public.official_source_current active
  join public.import_batches b on b.official_source_hash=active.source_hash and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
  join public.monthly_inventory i on i.source_batch_id=b.id
  where active.singleton and private.can_chain(i.chain_id,'view') and (p_chain_id is null or i.chain_id=p_chain_id)
   and i.period<=coalesce(p_as_of,active.last_period)
 ), latest as (select raw.*,max(raw.period) over(partition by raw.chain_id,raw.product_id) latest_period from raw),
 canonical as (
  select u.chain_id,u.product_id,u.format,u.upc,u.attributes from private.unified_catalog u
  where u.source_hash=p_source_hash and private.can_chain(u.chain_id,'view') and (p_chain_id is null or u.chain_id=p_chain_id)
  union all
  select distinct x.chain_id,x.product_id,x.format,x.upc,x.attributes from latest x where x.period=x.latest_period
   and not exists(select 1 from private.unified_catalog u where u.source_hash=p_source_hash and u.chain_id=x.chain_id and u.product_id=x.product_id)
 ) select x.chain_id,x.product_id,x.format,x.upc,'RS'::text,
 x.attributes->>'model',x.attributes->>'presentation',x.attributes->>'color',x.attributes->>'product_type',
 x.attributes->>'commercial_code',x.attributes->>'account',x.attributes from canonical x;
end $$;
revoke all on function public.operational_read_catalog(text,bigint,uuid,date) from public,anon,service_role;
grant execute on function public.operational_read_catalog(text,bigint,uuid,date) to authenticated;
