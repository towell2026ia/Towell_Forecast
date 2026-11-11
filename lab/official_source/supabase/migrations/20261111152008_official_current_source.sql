-- Prepared replacement contract. Outside the automatic production migrations.
-- Execute only on a separately authorized target and in one transaction.
do $$ begin
  if session_user is distinct from (
    select pg_get_userbyid(relowner) from pg_class where oid='public.monthly_observations'::regclass
  ) or current_setting('towell.official_database',true) is distinct from current_database()
    or coalesce(current_setting('towell.official_source_hash',true),'') !~ '^[0-9a-f]{64}$'
  then raise exception 'OFFICIAL_SOURCE_TARGET_REQUIRED' using errcode='42501'; end if;
end $$;

lock table public.monthly_observations,public.import_batches,public.source_evidence in share row exclusive mode;
drop view if exists public.portal_history_periods;
drop view if exists public.portal_monthly_observations_current;
drop view if exists public.monthly_observations_current;
do $$ declare routine regprocedure; begin
  for routine in select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in (
      'operational_read_context','operational_read_observations','operational_read_inventory',
      'operational_read_periods','operational_read_fill_rate','operational_read_catalog'
    )
  loop execute format('drop function %s',routine); end loop;
end $$;
drop trigger if exists unification_publication_required on public.monthly_observations;
alter table public.monthly_observations alter column value type numeric;
alter table public.monthly_observations drop constraint if exists monthly_observations_metric_code_check;
alter table public.monthly_observations add constraint monthly_observations_metric_code_check
  check(metric_code in ('SALES','ORDER','DELIVERY','FCST_TOWELL_HISTORY','FCST_CLIENTE_HISTORY'));
alter table public.monthly_observations add constraint official_observation_finite check(value<'Infinity'::numeric);
alter table public.import_batches add column official_source_hash text
  check(official_source_hash is null or official_source_hash ~ '^[0-9a-f]{64}$');
create index official_import_batches_source on public.import_batches(official_source_hash,evidence_id,chain_id,period)
  where official_source_hash is not null;
create function private.official_batch_hash_immutable() returns trigger language plpgsql set search_path='' as $$
begin
  if tg_op='UPDATE' and new.official_source_hash is distinct from old.official_source_hash
  then raise exception 'OFFICIAL_BATCH_SOURCE_IMMUTABLE'; end if;
  return new;
end $$;
create trigger official_batch_hash_immutable before update on public.import_batches
  for each row execute function private.official_batch_hash_immutable();
revoke all on function private.official_batch_hash_immutable() from public,anon,authenticated,service_role;

create table public.official_source_current (
  singleton boolean primary key default true check(singleton),
  source_hash text not null check(source_hash ~ '^[0-9a-f]{64}$'),
  generation bigint not null check(generation>0),
  first_period date not null check(extract(day from first_period)=1),
  last_period date not null check(extract(day from last_period)=1 and last_period>=first_period),
  evidence_ids uuid[] not null check(cardinality(evidence_ids)=2),
  activated_at timestamptz not null default clock_timestamp()
);
alter table public.official_source_current enable row level security;
revoke all on public.official_source_current from public,anon,authenticated,service_role;
grant select(source_hash,generation,first_period,last_period) on public.official_source_current to authenticated;
create policy official_source_metadata_read on public.official_source_current for select to authenticated
  using(exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE'));

create table private.official_observation_source (
  observation_id uuid primary key references public.monthly_observations(id),
  format text not null check(btrim(format)<>''),
  upc text not null check(upc ~ '^[0-9]+$'),
  purchase_type text not null check(purchase_type='RS'),
  source_reference text not null check(btrim(source_reference)<>''),
  value_literal text not null check(btrim(value_literal)<>''),
  attributes jsonb not null default '{}'::jsonb check(jsonb_typeof(attributes)='object'),
  attribute_references jsonb not null default '{}'::jsonb check(jsonb_typeof(attribute_references)='object')
);
alter table private.official_observation_source enable row level security;
revoke all on private.official_observation_source from public,anon,authenticated,service_role;
create trigger official_observation_source_immutable before update or delete
  on private.official_observation_source for each row execute function public.contract_insert_only();

create table public.monthly_inventory (
  id uuid primary key,
  chain_id uuid not null,
  product_id uuid not null,
  period date not null check(extract(day from period)=1),
  format text not null check(btrim(format)<>''),
  upc text not null check(upc ~ '^[0-9]+$'),
  purchase_type text not null check(purchase_type='RS'),
  quantity numeric not null check(quantity>=0 and quantity<'Infinity'::numeric),
  value_literal text not null check(btrim(value_literal)<>''),
  source_reference text not null check(btrim(source_reference)<>''),
  attributes jsonb not null default '{}'::jsonb check(jsonb_typeof(attributes)='object'),
  attribute_references jsonb not null default '{}'::jsonb check(jsonb_typeof(attribute_references)='object'),
  source_batch_id uuid not null,
  foreign key(product_id,chain_id) references public.products(id,chain_id),
  foreign key(source_batch_id,chain_id) references public.import_batches(id,chain_id),
  unique(source_batch_id,product_id,period)
);
create index official_inventory_batch_period on public.monthly_inventory(source_batch_id,period,product_id);
alter table public.monthly_inventory enable row level security;
revoke all on public.monthly_inventory from public,anon,authenticated,service_role;
create trigger monthly_inventory_immutable before update or delete
  on public.monthly_inventory for each row execute function public.contract_insert_only();

create function private.official_validate_lineage() returns trigger language plpgsql set search_path='' as $$
begin
  if tg_table_name='official_observation_source' then
    if not exists(select 1 from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
      where o.id=new.observation_id and o.value=new.value_literal::numeric and b.status='IMPORTED'
        and b.evidence_id is not null and o.available_at is null and o.availability_source='UNKNOWN')
    then raise exception 'OFFICIAL_LINEAGE_INVALID'; end if;
  elsif new.quantity is distinct from new.value_literal::numeric
    or not exists(select 1 from public.import_batches b where b.id=new.source_batch_id
      and b.chain_id=new.chain_id and b.period=new.period and b.evidence_id is not null and b.status='IMPORTED')
  then raise exception 'OFFICIAL_INVENTORY_LINEAGE_INVALID'; end if;
  return new;
end $$;
create trigger official_observation_source_valid before insert on private.official_observation_source
  for each row execute function private.official_validate_lineage();
create trigger official_inventory_source_valid before insert on public.monthly_inventory
  for each row execute function private.official_validate_lineage();
revoke all on function private.official_validate_lineage() from public,anon,authenticated,service_role;

create function private.official_validate_current() returns trigger language plpgsql set search_path='' as $$
declare beginning date; ending date;
begin
  if session_user is distinct from (
    select pg_get_userbyid(relowner) from pg_class where oid='public.monthly_observations'::regclass
  ) or current_setting('towell.official_database',true) is distinct from current_database()
    or current_setting('towell.official_source_hash',true) is distinct from new.source_hash
  then raise exception 'OFFICIAL_SOURCE_TARGET_REQUIRED' using errcode='42501'; end if;
  if (tg_op='INSERT' and new.generation<>1) or (tg_op='UPDATE' and new.generation<>old.generation+1)
    or new.evidence_ids[1] is null or new.evidence_ids[2] is null or new.evidence_ids[1]=new.evidence_ids[2]
    or (select count(*) from public.source_evidence e where e.id=any(new.evidence_ids))<>2
  then raise exception 'OFFICIAL_SOURCE_REGISTER_INVALID'; end if;
  if exists(
    select 1 from public.monthly_observations o
    join private.official_observation_source s on s.observation_id=o.id
    join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
    where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
    group by o.chain_id,o.product_id,o.period,o.metric_code having count(*)>1
  ) or exists(
    select 1 from public.monthly_inventory i join public.import_batches b on b.id=i.source_batch_id and b.chain_id=i.chain_id
    where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
    group by i.chain_id,i.product_id,i.period having count(*)>1
  ) then raise exception 'OFFICIAL_SOURCE_DUPLICATE_KEY'; end if;
  if exists(
    select 1 from (
      select raw.*,max(raw.period) over(partition by raw.chain_id,raw.product_id) latest_period from (
      select o.chain_id,o.product_id,o.period,s.format,s.upc,s.attributes
      from public.monthly_observations o join private.official_observation_source s on s.observation_id=o.id
      join public.import_batches b on b.id=o.source_batch_id
      where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
      union all
      select i.chain_id,i.product_id,i.period,i.format,i.upc,i.attributes from public.monthly_inventory i
      join public.import_batches b on b.id=i.source_batch_id
      where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
      ) raw
    ) x where x.period=x.latest_period group by chain_id,product_id having count(distinct (format,upc,attributes))>1
  ) then raise exception 'OFFICIAL_SOURCE_PRODUCT_ATTRIBUTES_CONFLICT'; end if;
  select min(x.period),max(x.period) into beginning,ending from (
    select o.period from public.monthly_observations o join private.official_observation_source s on s.observation_id=o.id
    join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
    where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
    union all
    select i.period from public.monthly_inventory i join public.import_batches b on b.id=i.source_batch_id and b.chain_id=i.chain_id
    where b.official_source_hash=new.source_hash and b.evidence_id=any(new.evidence_ids) and b.status='IMPORTED'
  ) x;
  if beginning is null or beginning<>new.first_period or ending<>new.last_period
  then raise exception 'OFFICIAL_SOURCE_PERIODS_INVALID'; end if;
  return new;
end $$;
create trigger official_source_current_valid before insert or update on public.official_source_current
  for each row execute function private.official_validate_current();
revoke all on function private.official_validate_current() from public,anon,authenticated,service_role;

create function private.official_import_requires_current() returns trigger language plpgsql set search_path='' as $$
declare batch uuid; expected bigint;
begin
  if tg_table_name='official_observation_source' then
    select o.source_batch_id into batch from public.monthly_observations o where o.id=new.observation_id;
  else batch=new.source_batch_id; end if;
  begin expected=current_setting('towell.official_expected_generation',true)::bigint;
  exception when invalid_text_representation then expected=null; end;
  if expected is null or not exists(
    select 1 from public.official_source_current s join public.import_batches b
      on b.official_source_hash=s.source_hash and b.evidence_id=any(s.evidence_ids)
    where s.singleton and s.generation=expected+1
      and s.source_hash=current_setting('towell.official_source_hash',true) and b.id=batch and b.status='IMPORTED'
  ) then raise exception 'OFFICIAL_IMPORT_REQUIRES_CURRENT_TRANSACTION'; end if;
  return null;
end $$;
create constraint trigger official_observation_requires_current after insert on private.official_observation_source
  deferrable initially deferred for each row execute function private.official_import_requires_current();
create constraint trigger official_inventory_requires_current after insert on public.monthly_inventory
  deferrable initially deferred for each row execute function private.official_import_requires_current();
revoke all on function private.official_import_requires_current() from public,anon,authenticated,service_role;

create function private.official_assert_context(p_source_hash text,p_generation bigint) returns void
language plpgsql stable security definer set search_path='' as $$
begin
  if not exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE')
    then raise exception 'OFFICIAL_SOURCE_FORBIDDEN' using errcode='42501'; end if;
  if not exists(select 1 from public.official_source_current s
    where s.singleton and s.source_hash=p_source_hash and s.generation=p_generation)
    then raise exception 'OFFICIAL_SOURCE_CHANGED' using errcode='40001'; end if;
end $$;
revoke all on function private.official_assert_context(text,bigint) from public,anon,authenticated,service_role;

create function public.operational_read_context()
returns table(source_hash text,generation bigint,first_period text,last_period text)
language sql stable security definer set search_path='' as $$
  select s.source_hash,s.generation,to_char(s.first_period,'YYYY-MM'),to_char(s.last_period,'YYYY-MM')
  from public.official_source_current s
  where s.singleton and exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE');
$$;

create function public.operational_read_observations(
  p_source_hash text,p_generation bigint,p_mode text default 'OPERATIONAL',
  p_available_through timestamptz default null,p_chain_id uuid default null,
  p_product_ids uuid[] default null,p_from date default null,p_to date default null,
  p_metric_codes text[] default null,p_observation_ids uuid[] default null,
  p_format text default null,p_purchase_type text default 'RS'
) returns table(
  id uuid,observation_id uuid,chain_id uuid,chain_code text,chain_name text,
  product_id uuid,product_code text,variant_code text,product_description text,category_id uuid,
  period date,metric_code text,value numeric,value_text text,version_no integer,
  availability_source text,available_at timestamptz,source_batch_id uuid,availability_evidence_id uuid,
  source_reference text,source_value_literal text,format text,upc text,purchase_type text
) language plpgsql stable security definer set search_path='' as $$
begin
  perform private.official_assert_context(p_source_hash,p_generation);
  if p_mode is null or p_mode not in ('OPERATIONAL','AUDIT','TEMPORAL')
    or p_purchase_type is distinct from 'RS' then raise exception 'OFFICIAL_FILTER_INVALID' using errcode='22023'; end if;
  return query select o.id,o.id,o.chain_id,c.code,c.name,o.product_id,p.product_code,p.variant_code,
    p.description,p.category_id,o.period,o.metric_code,o.value,s.value_literal,o.version_no,
    o.availability_source,o.available_at,o.source_batch_id,o.availability_evidence_id,
    s.source_reference,s.value_literal,s.format,s.upc,s.purchase_type
  from public.official_source_current active
  join public.import_batches b on b.official_source_hash=active.source_hash and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
  join public.monthly_observations o on o.source_batch_id=b.id and o.chain_id=b.chain_id
  join private.official_observation_source s on s.observation_id=o.id
  join public.products p on p.id=o.product_id and p.chain_id=o.chain_id
  join public.chains c on c.id=o.chain_id
  where active.singleton and private.can_chain(o.chain_id,'view') and s.purchase_type='RS'
    and (p_chain_id is null or o.chain_id=p_chain_id)
    and (p_product_ids is null or o.product_id=any(p_product_ids))
    and (p_from is null or o.period>=p_from) and (p_to is null or o.period<=p_to)
    and (p_metric_codes is null or o.metric_code=any(p_metric_codes))
    and (p_observation_ids is null or o.id=any(p_observation_ids))
    and (p_format is null or s.format=p_format)
    and (p_mode<>'TEMPORAL' or (o.available_at is not null and p_available_through is not null
      and o.available_at<=p_available_through));
end $$;

create function public.operational_read_inventory(
  p_source_hash text,p_generation bigint,p_chain_id uuid default null,p_product_ids uuid[] default null,
  p_from date default null,p_to date default null,p_format text default null,p_purchase_type text default 'RS'
) returns table(chain_id uuid,product_id uuid,period date,format text,upc text,
  purchase_type text,quantity numeric,quantity_text text,source_reference text)
language plpgsql stable security definer set search_path='' as $$
begin
  perform private.official_assert_context(p_source_hash,p_generation);
  if p_purchase_type is distinct from 'RS' then raise exception 'OFFICIAL_FILTER_INVALID' using errcode='22023'; end if;
  return query select i.chain_id,i.product_id,i.period,i.format,i.upc,i.purchase_type,i.quantity,i.value_literal,i.source_reference
  from public.official_source_current active
  join public.import_batches b on b.official_source_hash=active.source_hash and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
  join public.monthly_inventory i on i.source_batch_id=b.id and i.chain_id=b.chain_id
  where active.singleton and private.can_chain(i.chain_id,'view') and i.purchase_type='RS'
    and (p_chain_id is null or i.chain_id=p_chain_id)
    and (p_product_ids is null or i.product_id=any(p_product_ids))
    and (p_from is null or i.period>=p_from) and (p_to is null or i.period<=p_to)
    and (p_format is null or i.format=p_format);
end $$;

create function public.operational_read_periods(
  p_source_hash text,p_generation bigint,p_chain_id uuid default null
) returns table(period date) language sql stable security definer set search_path='' as $$
  select distinct o.period from public.operational_read_observations(
    p_source_hash,p_generation,p_chain_id=>p_chain_id
  ) o where o.metric_code in ('SALES','ORDER','DELIVERY');
$$;
create function public.operational_read_catalog(p_source_hash text,p_generation bigint,p_chain_id uuid default null,p_as_of date default null)
returns table(chain_id uuid,product_id uuid,format text,upc text,purchase_type text,
  model text,presentation text,color text,product_type text,commercial_code text,account text,attributes jsonb)
language plpgsql stable security definer set search_path='' as $$
begin
  perform private.official_assert_context(p_source_hash,p_generation);
  return query select distinct x.chain_id,x.product_id,x.format,x.upc,'RS'::text,
    x.attributes->>'model',x.attributes->>'presentation',x.attributes->>'color',
    x.attributes->>'product_type',x.attributes->>'commercial_code',x.attributes->>'account',x.attributes
  from (
    select raw.*,max(raw.period) over(partition by raw.chain_id,raw.product_id) latest_period from (
    select o.chain_id,o.product_id,o.period,s.format,s.upc,s.attributes from public.official_source_current active
    join public.import_batches b on b.official_source_hash=active.source_hash
      and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
    join public.monthly_observations o on o.source_batch_id=b.id
    join private.official_observation_source s on s.observation_id=o.id
    where active.singleton and private.can_chain(o.chain_id,'view') and (p_chain_id is null or o.chain_id=p_chain_id)
      and o.period<=coalesce(p_as_of,active.last_period)
    union all
    select i.chain_id,i.product_id,i.period,i.format,i.upc,i.attributes from public.official_source_current active
    join public.import_batches b on b.official_source_hash=active.source_hash
      and b.evidence_id=any(active.evidence_ids) and b.status='IMPORTED'
    join public.monthly_inventory i on i.source_batch_id=b.id
    where active.singleton and private.can_chain(i.chain_id,'view') and (p_chain_id is null or i.chain_id=p_chain_id)
      and i.period<=coalesce(p_as_of,active.last_period)
    ) raw
  ) x where x.period=x.latest_period;
end $$;
create function public.operational_read_fill_rate(
  p_source_hash text,p_generation bigint,p_chain_id uuid default null
) returns table(chain_id uuid,period date,delivery numeric,ordered numeric,fill_rate numeric,
  paired_products bigint,total_products bigint) language sql stable security definer set search_path='' as $$
  with pairs as (
    select o.chain_id,o.period,o.product_id,
      max(o.value) filter(where o.metric_code='DELIVERY') delivery,
      max(o.value) filter(where o.metric_code='ORDER') ordered
    from public.operational_read_observations(p_source_hash,p_generation,p_chain_id=>p_chain_id) o
    where o.metric_code in ('DELIVERY','ORDER') group by o.chain_id,o.period,o.product_id
  ), totals as (
    select p.chain_id,p.period,
      sum(p.delivery) filter(where p.delivery is not null and p.ordered is not null) delivery,
      sum(p.ordered) filter(where p.delivery is not null and p.ordered is not null) ordered,
      count(*) filter(where p.delivery is not null and p.ordered is not null) paired_products,
      count(*) total_products from pairs p group by p.chain_id,p.period
  ) select t.chain_id,t.period,t.delivery,t.ordered,t.delivery/nullif(t.ordered,0),
    t.paired_products,t.total_products from totals t;
$$;

-- Legacy paths are denied. Every operational reader must supply the current
-- source generation; service-role access cannot silently bypass this contract.
revoke all on public.monthly_observations from public,anon,authenticated,service_role;
do $$ declare signature text; routine regprocedure; relation regclass; begin
  foreach signature in array array[
    'public.e3_close_target_period(uuid,date,uuid)','public.e4_close_live(uuid,uuid,date,uuid,text)',
    'private.e4_live_temporally_eligible(uuid,date,uuid)','public.e4_save_capture(jsonb)',
    'public.e4_confirm_capture(uuid,uuid,text)','private.gateb_seal(uuid)',
    'private.gateb_activate(uuid,text,uuid,bigint,text,boolean)','private.unification_fill_rate(uuid)'
  ] loop
    routine=to_regprocedure(signature);
    if routine is not null then execute format('revoke all on function %s from public,anon,authenticated,service_role',routine); end if;
  end loop;
  foreach signature in array array[
    'public.operational_data_versions','public.operational_data_version_observations',
    'public.operational_data_active','public.operational_data_version_inventory',
    'public.forecast_monthly_observations'
  ] loop
    relation=to_regclass(signature);
    if relation is not null then execute format('revoke all on table %s from public,anon,authenticated,service_role',relation); end if;
  end loop;
  if to_regclass('public.operational_data_versions') is not null then
    revoke select(id,base_version_id,sealed,manifest_hash,created_at) on public.operational_data_versions from authenticated;
  end if;
end $$;
revoke all on function public.operational_read_context(),
  public.operational_read_observations(text,bigint,text,timestamptz,uuid,uuid[],date,date,text[],uuid[],text,text),
  public.operational_read_inventory(text,bigint,uuid,uuid[],date,date,text,text),
  public.operational_read_periods(text,bigint,uuid),public.operational_read_fill_rate(text,bigint,uuid),
  public.operational_read_catalog(text,bigint,uuid,date)
from public,anon,authenticated,service_role;
grant execute on function public.operational_read_context(),
  public.operational_read_observations(text,bigint,text,timestamptz,uuid,uuid[],date,date,text[],uuid[],text,text),
  public.operational_read_inventory(text,bigint,uuid,uuid[],date,date,text,text),
  public.operational_read_periods(text,bigint,uuid),public.operational_read_fill_rate(text,bigint,uuid),
  public.operational_read_catalog(text,bigint,uuid,date) to authenticated;
