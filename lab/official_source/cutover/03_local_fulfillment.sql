-- LOCAL ONLY: independently accredited BAASE W/X fulfillment pairs.
-- Not in Supabase's production migrations; never an engine observation source.
do $$ begin
  if current_database() !~ '^gateb_official_[a-z0-9_]+$'
    or host(inet_server_addr()) is distinct from '127.0.0.1'
    or current_setting('listen_addresses') is distinct from '127.0.0.1'
    or coalesce(current_setting('gateb.lab_id',true),'') !~ '^[0-9a-f]{32}$'
    or current_setting('towell.official_database',true) is distinct from current_database()
    or coalesce(current_setting('towell.official_source_hash',true),'') !~ '^[0-9a-f]{64}$'
    or session_user is distinct from (select pg_get_userbyid(relowner) from pg_class where oid='public.monthly_observations'::regclass)
  then raise exception 'LOCAL_FULFILLMENT_TARGET_REQUIRED' using errcode='42501'; end if;
end $$;

create table if not exists public.monthly_fulfillment (
  id uuid primary key, chain_id uuid not null, product_id uuid not null,
  period date not null check(extract(day from period)=1),
  format text not null check(btrim(format)<>''), upc text not null check(upc ~ '^[0-9]+$'),
  purchase_type text not null check(purchase_type='RS'),
  order_quantity numeric, delivery_quantity numeric, order_text text, delivery_text text,
  source_reference text not null check(source_reference ~ '^BAASE!W[0-9]+:X[0-9]+$'),
  order_source_reference text not null check(order_source_reference ~ '^BAASE!W[0-9]+$'),
  delivery_source_reference text not null check(delivery_source_reference ~ '^BAASE!X[0-9]+$'),
  source_sha256 text not null check(source_sha256 ~ '^[0-9a-f]{64}$'),
  partial_period boolean not null, source_batch_id uuid not null,
  check(source_reference=order_source_reference||':X'||substring(order_source_reference from '[0-9]+$')
    and delivery_source_reference='BAASE!X'||substring(order_source_reference from '[0-9]+$')),
  check((order_quantity is null)=(order_text is null)),
  check((delivery_quantity is null)=(delivery_text is null)),
  check(order_quantity is null or (order_quantity>=0 and order_quantity<'Infinity'::numeric and order_text::numeric=order_quantity)),
  check(delivery_quantity is null or (delivery_quantity>=0 and delivery_quantity<'Infinity'::numeric and delivery_text::numeric=delivery_quantity)),
  foreign key(product_id,chain_id) references public.products(id,chain_id),
  foreign key(source_batch_id,chain_id) references public.import_batches(id,chain_id),
  unique(source_batch_id,product_id,period)
);
create index if not exists fulfillment_batch_month on public.monthly_fulfillment(source_batch_id,period,product_id);
alter table public.monthly_fulfillment enable row level security;
revoke all on public.monthly_fulfillment from public,anon,authenticated,service_role;

create or replace function private.validate_local_fulfillment() returns trigger language plpgsql set search_path='' as $$
begin
  if not exists(select 1 from public.import_batches b join public.source_evidence e on e.id=b.evidence_id
      where b.id=new.source_batch_id and b.chain_id=new.chain_id and b.period=new.period and b.status='IMPORTED'
      and b.official_source_hash=current_setting('towell.official_source_hash',true)
      and e.source_hash=new.source_sha256)
    or current_setting('towell.official_database',true) is distinct from current_database()
    or session_user is distinct from (select pg_get_userbyid(relowner) from pg_class where oid='public.monthly_observations'::regclass)
  then raise exception 'FULFILLMENT_LINEAGE_INVALID' using errcode='23514'; end if;
  return new;
end $$;
drop trigger if exists monthly_fulfillment_lineage on public.monthly_fulfillment;
create trigger monthly_fulfillment_lineage before insert on public.monthly_fulfillment
  for each row execute function private.validate_local_fulfillment();
drop trigger if exists monthly_fulfillment_immutable on public.monthly_fulfillment;
create trigger monthly_fulfillment_immutable before update or delete on public.monthly_fulfillment
  for each row execute function public.contract_insert_only();
revoke all on function private.validate_local_fulfillment() from public,anon,authenticated,service_role;

create or replace function public.operational_read_fulfillment(
  p_source_hash text,p_generation bigint,p_chain_id uuid default null,p_product_ids uuid[] default null,
  p_from date default null,p_to date default null,p_format text default null,p_purchase_type text default 'RS'
) returns table(chain_id uuid,product_id uuid,period date,format text,upc text,purchase_type text,
  order_text text,delivery_text text,source_reference text,order_source_reference text,delivery_source_reference text,
  source_sha256 text,partial_period boolean)
language plpgsql stable security definer set search_path='' as $$
begin
  perform private.official_assert_context(p_source_hash,p_generation);
  if p_purchase_type is distinct from 'RS' then raise exception 'OFFICIAL_FILTER_INVALID' using errcode='22023'; end if;
  return query select f.chain_id,f.product_id,f.period,f.format,f.upc,f.purchase_type,
    f.order_text,f.delivery_text,f.source_reference,f.order_source_reference,f.delivery_source_reference,f.source_sha256,f.partial_period
  from public.official_source_current active
  join public.import_batches b on b.official_source_hash=active.source_hash and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
  join public.monthly_fulfillment f on f.source_batch_id=b.id and f.chain_id=b.chain_id
  where active.singleton and private.can_chain(f.chain_id,'view') and f.purchase_type='RS'
    and (p_chain_id is null or f.chain_id=p_chain_id) and (p_product_ids is null or f.product_id=any(p_product_ids))
    and (p_from is null or f.period>=p_from) and (p_to is null or f.period<=p_to)
    and (p_format is null or f.format=p_format);
end $$;
revoke all on function public.operational_read_fulfillment(text,bigint,uuid,uuid[],date,date,text,text) from public,anon,service_role;
grant execute on function public.operational_read_fulfillment(text,bigint,uuid,uuid[],date,date,text,text) to authenticated;
