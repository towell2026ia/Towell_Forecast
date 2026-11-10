-- Proposed cutover SQL. Kept outside the automated migration directory.
-- Run after A is sealed and active, with legacy writers frozen and B outside the DB.
lock table public.monthly_observations,public.products,public.chains,public.import_batches,
  public.import_profile_versions,public.source_evidence in share row exclusive mode;
do $$ begin
  if not exists (
    select 1 from public.operational_data_active a
    join public.operational_data_versions v on v.id=a.version_id
    where a.singleton and a.generation=1 and v.sealed and v.base_version_id is null
      and v.physical_hash=private.gateb_physical_hash()
      and v.catalog_hash=private.gateb_catalog_hash()
      and v.document->>'archive_manifest_sha256'=current_setting('towell.cutover_manifest_sha256',true)
  ) then raise exception 'active_baseline_required'; end if;
  if exists(
    with imported as (
      select distinct on(o.chain_id,o.product_id,o.period,o.metric_code) o.id
      from public.monthly_observations o join public.import_batches b
        on b.id=o.source_batch_id and b.chain_id=o.chain_id
      where b.status='IMPORTED'
      order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc
    ), pinned as (
      select m.observation_id id from public.operational_data_version_observations m
      join public.operational_data_active a on a.version_id=m.version_id where a.singleton
    )
    select 1 from imported full join pinned using(id) where imported.id is null or pinned.id is null
  ) then raise exception 'imported_baseline_drift'; end if;
end $$;

-- Older imported physical versions are legitimate audit history even if A
-- selects a newer version of the same key. Capture their IDs exactly once.
create table private.operational_audit_baseline (
  observation_id uuid primary key references public.monthly_observations(id)
);
insert into private.operational_audit_baseline
select o.id from public.monthly_observations o join public.import_batches b
  on b.id=o.source_batch_id and b.chain_id=o.chain_id
where b.status='IMPORTED';
revoke all on private.operational_audit_baseline from public,anon,authenticated,service_role;

-- Provenance is separate from availability evidence. Preserve the cell
-- reference and decimal literal without inventing an observation timestamp.
create table private.operational_source_lineage (
  observation_id uuid primary key references public.monthly_observations(id),
  archive_manifest_sha256 text not null check(archive_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  source_reference text not null check(btrim(source_reference)<>''),
  value_literal text not null check(btrim(value_literal)<>'')
);
revoke all on private.operational_source_lineage from public,anon,authenticated,service_role;
create trigger operational_source_lineage_immutable before update or delete
  on private.operational_source_lineage for each row execute function public.contract_insert_only();
create function private.operational_validate_source_lineage() returns trigger
language plpgsql set search_path='' as $$
begin
  if exists(select 1 from public.operational_data_version_observations m
    join public.operational_data_versions v on v.id=m.version_id
    where m.observation_id=new.observation_id and v.sealed)
  or not exists(select 1 from public.monthly_observations o
    where o.id=new.observation_id and o.available_at is null and o.availability_source='UNKNOWN'
      and o.value=new.value_literal::numeric)
  then raise exception 'invalid_operational_source_lineage'; end if;
  return new;
end $$;
create trigger operational_source_lineage_valid before insert on private.operational_source_lineage
  for each row execute function private.operational_validate_source_lineage();
revoke all on function private.operational_validate_source_lineage() from public,anon,authenticated,service_role;
create function private.operational_validate_archive_seal() returns trigger
language plpgsql set search_path='' as $$
begin
  if new.document ? 'archive_candidate_id' and exists(
    select 1 from public.operational_data_version_observations m
    where m.version_id=new.id and not exists(
      select 1 from public.operational_data_version_observations a
      where a.version_id=new.base_version_id and a.observation_id=m.observation_id
    ) and not exists(
      select 1 from private.operational_source_lineage l
      where l.observation_id=m.observation_id and l.archive_manifest_sha256=new.document->>'archive_manifest_sha256'
    )
  ) then raise exception 'archive_member_missing_lineage'; end if;
  return new;
end $$;
create trigger operational_archive_seal before update on public.operational_data_versions
  for each row when(new.sealed and not old.sealed) execute function private.operational_validate_archive_seal();
revoke all on function private.operational_validate_archive_seal() from public,anon,authenticated,service_role;
alter function private.gateb_payload(uuid) rename to operational_payload_before_lineage;
create function private.gateb_payload(p_version uuid) returns jsonb language sql stable set search_path='' as $$
  select private.operational_payload_before_lineage(v.id) ||
    case when v.document ? 'archive_candidate_id' then jsonb_build_object(
      'source_lineage',coalesce((select jsonb_agg(to_jsonb(l) order by l.observation_id)
        from public.operational_data_version_observations m
        join private.operational_source_lineage l on l.observation_id=m.observation_id
        where m.version_id=v.id),'[]'::jsonb)
    ) else '{}'::jsonb end
  from public.operational_data_versions v where v.id=p_version;
$$;
revoke all on function private.gateb_payload(uuid) from public,anon,authenticated,service_role;

create index operational_transition_generation on public.audit_log
  (((new_data->>'generation')::bigint),entity_id)
  where entity_type='operational_data_version'
    and action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED');

create function private.operational_assert_pin(
  p_version_id uuid,p_manifest_hash text,p_generation bigint
) returns void language plpgsql stable security definer set search_path='' as $$
begin
  if not exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE')
     or not exists(
       select 1 from public.audit_log e
       join public.operational_data_versions v on v.id=e.entity_id
       where e.entity_type='operational_data_version'
         and e.action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED')
         and e.entity_id=p_version_id and v.sealed and v.manifest_hash=p_manifest_hash
         and (e.new_data->>'generation')::bigint=p_generation
     ) then
    raise exception 'invalid_operational_pin' using errcode='42501';
  end if;
end $$;
revoke all on function private.operational_assert_pin(uuid,text,bigint) from public,anon,authenticated,service_role;

create function public.operational_read_context()
returns table(version_id uuid,manifest_hash text,generation bigint)
language sql stable security definer set search_path='' as $$
  select v.id,v.manifest_hash,a.generation
  from public.operational_data_active a join public.operational_data_versions v on v.id=a.version_id
  where a.singleton and v.sealed
    and exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE');
$$;

create function public.operational_read_observations(
  p_version_id uuid,p_manifest_hash text,p_generation bigint,
  p_mode text default 'OPERATIONAL',p_available_through timestamptz default null,
  p_chain_id uuid default null,p_product_ids uuid[] default null,
  p_from date default null,p_to date default null,p_metric_codes text[] default null,
  p_observation_ids uuid[] default null
) returns table(
  id uuid,observation_id uuid,chain_id uuid,chain_code text,chain_name text,
  product_id uuid,product_code text,variant_code text,product_description text,category_id uuid,
  period date,metric_code text,value numeric,value_text text,version_no integer,
  availability_source text,available_at timestamptz,source_batch_id uuid,availability_evidence_id uuid,
  source_reference text,source_value_literal text
) language plpgsql stable security definer set search_path='' as $$
begin
  perform private.operational_assert_pin(p_version_id,p_manifest_hash,p_generation);
  if p_mode is null or p_mode not in ('OPERATIONAL','TEMPORAL','AUDIT') then
    raise exception 'invalid_operational_mode' using errcode='22023';
  end if;
  return query
  with recursive ancestry(version_id) as (
    select p_version_id where p_mode='TEMPORAL'
    union all
    select v.base_version_id from ancestry a
    join public.operational_data_versions v on v.id=a.version_id where v.base_version_id is not null
  ), audit_releases(version_id) as (
    select e.entity_id from public.audit_log e
    where p_mode='AUDIT' and e.entity_type='operational_data_version'
      and e.action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED')
      and (e.new_data->>'generation')::bigint<=p_generation
  ), candidates(observation_id) as (
    select m.observation_id from public.operational_data_version_observations m
    where p_mode='OPERATIONAL' and m.version_id=p_version_id
      and (p_chain_id is null or m.chain_id=p_chain_id)
      and (p_product_ids is null or m.product_id=any(p_product_ids))
      and (p_metric_codes is null or m.metric_code=any(p_metric_codes))
      and (p_from is null or m.period>=p_from) and (p_to is null or m.period<=p_to)
    union
    select m.observation_id from public.operational_data_version_observations m
    where m.version_id in (select r.version_id from audit_releases r)
       or m.version_id in (select a.version_id from ancestry a)
    union
    select b.observation_id from private.operational_audit_baseline b where p_mode in ('AUDIT','TEMPORAL')
  ), visible as (
    select o.* from candidates k join public.monthly_observations o on o.id=k.observation_id
    where private.can_chain(o.chain_id,'view')
      and (p_chain_id is null or o.chain_id=p_chain_id)
      and (p_product_ids is null or o.product_id=any(p_product_ids))
      and (p_metric_codes is null or o.metric_code=any(p_metric_codes))
      and (p_observation_ids is null or o.id=any(p_observation_ids))
      and (p_from is null or o.period>=p_from) and (p_to is null or o.period<=p_to)
      and (p_mode<>'TEMPORAL' or (o.available_at is not null
           and (p_available_through is null or o.available_at<=p_available_through)))
  ), selected as (
    select v.* from visible v where p_mode<>'TEMPORAL'
    union all
    select v.* from (
      select distinct on(o.chain_id,o.product_id,o.period,o.metric_code) o.* from visible o
      order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc
    ) v where p_mode='TEMPORAL'
  )
  select o.id,o.id,o.chain_id,c.code,c.name,o.product_id,p.product_code,p.variant_code,
    p.description,p.category_id,o.period,o.metric_code,o.value,o.value::text,o.version_no,
    o.availability_source,o.available_at,o.source_batch_id,o.availability_evidence_id,
    l.source_reference,l.value_literal
  from selected o
  join public.products p on p.id=o.product_id and p.chain_id=o.chain_id
  join public.chains c on c.id=o.chain_id
  left join private.operational_source_lineage l on l.observation_id=o.id;
end $$;

create function public.operational_read_inventory(
  p_version_id uuid,p_manifest_hash text,p_generation bigint
) returns table(chain_id uuid,product_id uuid,period date,format text,upc text,
  purchase_type text,quantity numeric,quantity_text text,source_reference text)
language plpgsql stable security definer set search_path='' as $$
begin
  perform private.operational_assert_pin(p_version_id,p_manifest_hash,p_generation);
  return query select i.chain_id,i.product_id,i.period,i.format,i.upc,i.purchase_type,
    i.quantity,i.quantity::text,i.source_reference
  from public.operational_data_version_inventory i
  where i.version_id=p_version_id and private.can_chain(i.chain_id,'view');
end $$;

create function public.operational_read_periods(
  p_version_id uuid,p_manifest_hash text,p_generation bigint,p_chain_id uuid default null
) returns table(period date) language plpgsql stable security definer set search_path='' as $$
begin
  perform private.operational_assert_pin(p_version_id,p_manifest_hash,p_generation);
  return query select distinct m.period from public.operational_data_version_observations m
  where m.version_id=p_version_id and private.can_chain(m.chain_id,'view')
    and m.metric_code in ('SALES','ORDER','DELIVERY') and (p_chain_id is null or m.chain_id=p_chain_id);
end $$;

create function public.operational_read_fill_rate(
  p_version_id uuid,p_manifest_hash text,p_generation bigint,p_chain_id uuid default null
) returns table(chain_id uuid,period date,delivery numeric,ordered numeric,fill_rate numeric,
  paired_products bigint,total_products bigint) language plpgsql stable security definer set search_path='' as $$
begin
  perform private.operational_assert_pin(p_version_id,p_manifest_hash,p_generation);
  return query
  with paired as (
    select m.chain_id,m.period,m.product_id,
      max(o.value) filter(where m.metric_code='DELIVERY') as delivery,
      max(o.value) filter(where m.metric_code='ORDER') as ordered
    from public.operational_data_version_observations m
    join public.monthly_observations o on o.id=m.observation_id
    where m.version_id=p_version_id and m.metric_code in ('DELIVERY','ORDER')
      and (p_chain_id is null or m.chain_id=p_chain_id) and private.can_chain(m.chain_id,'view')
    group by m.chain_id,m.period,m.product_id
  ), totals as (
    select p.chain_id,p.period,
      sum(p.delivery) filter(where p.delivery is not null and p.ordered is not null) as delivery,
      sum(p.ordered) filter(where p.delivery is not null and p.ordered is not null) as ordered,
      count(*) filter(where p.delivery is not null and p.ordered is not null) as pairs,
      count(*) as products
    from paired p group by p.chain_id,p.period
  )
  select t.chain_id,t.period,t.delivery,t.ordered,t.delivery/nullif(t.ordered,0),t.pairs,t.products from totals t;
end $$;

-- Default public grants can include BYPASSRLS service_role. Deny direct reads
-- rather than treating RLS as a service-role boundary.
revoke all on public.monthly_observations,public.operational_data_versions,
  public.operational_data_version_observations,public.operational_data_active,
  public.operational_data_version_inventory from public,anon,authenticated,service_role;
revoke select(id,base_version_id,sealed,manifest_hash,created_at)
  on public.operational_data_versions from authenticated;
revoke execute on function private.gateb_seal(uuid),
  private.gateb_activate(uuid,text,uuid,bigint,text,boolean) from public,anon,authenticated,service_role;
revoke execute on function private.unification_fill_rate(uuid) from public,anon,authenticated,service_role;
do $$ declare signature text; routine regprocedure;
begin
  foreach signature in array array[
    'public.e3_close_target_period(uuid,date,uuid)',
    'public.e4_close_live(uuid,uuid,date,uuid,text)',
    'private.e4_live_temporally_eligible(uuid,date,uuid)',
    'public.e4_save_capture(jsonb)',
    'public.e4_confirm_capture(uuid,uuid,text)'
  ] loop
    routine=to_regprocedure(signature);
    if routine is not null then
      execute format('revoke all on function %s from public,anon,authenticated,service_role',routine);
    end if;
  end loop;
  if to_regclass('public.forecast_monthly_observations') is not null then
    revoke all on public.forecast_monthly_observations from public,anon,authenticated,service_role;
  end if;
end $$;

-- Compatibility for single-statement legacy authenticated readers. Multi-page
-- readers must use a pinned RPC. The explicit checks also deny anonymous access.
create or replace view public.portal_monthly_observations_current with(security_invoker=false) as
  select o.observation_id,o.chain_id,o.chain_code,o.chain_name,o.product_id,o.product_code,
    o.variant_code,o.product_description,o.category_id,o.period,o.metric_code,
    o.value::numeric(31,16) as value,o.version_no,o.availability_source,o.available_at,o.source_batch_id
  from public.operational_read_context() a
  cross join lateral public.operational_read_observations(a.version_id,a.manifest_hash,a.generation) o
  where o.metric_code in ('SALES','ORDER','DELIVERY');
create or replace view public.monthly_observations_current with(security_invoker=false) as
  select o.* from public.operational_read_context() a
  cross join lateral public.operational_read_observations(a.version_id,a.manifest_hash,a.generation,'TEMPORAL') k
  join public.monthly_observations o on o.id=k.id
  where k.metric_code in ('SALES','ORDER','DELIVERY');
revoke all on public.portal_monthly_observations_current,public.monthly_observations_current,
  public.portal_history_periods from public,anon,service_role;
grant select on public.portal_monthly_observations_current,public.monthly_observations_current,
  public.portal_history_periods to authenticated;

revoke all on function public.operational_read_context(),
  public.operational_read_observations(uuid,text,bigint,text,timestamptz,uuid,uuid[],date,date,text[],uuid[]),
  public.operational_read_inventory(uuid,text,bigint),public.operational_read_periods(uuid,text,bigint,uuid),
  public.operational_read_fill_rate(uuid,text,bigint,uuid) from public,anon,authenticated,service_role;
grant execute on function public.operational_read_context(),
  public.operational_read_observations(uuid,text,bigint,text,timestamptz,uuid,uuid[],date,date,text[],uuid[]),
  public.operational_read_inventory(uuid,text,bigint),public.operational_read_periods(uuid,text,bigint,uuid),
  public.operational_read_fill_rate(uuid,text,bigint,uuid) to authenticated;
