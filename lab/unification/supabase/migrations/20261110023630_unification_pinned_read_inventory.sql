-- Isolated proposal only. Not part of the production migration directory.
do $$ begin
 if host(inet_server_addr()) is distinct from '127.0.0.1'
    or current_database() !~ '^gateb_unify_' or current_setting('listen_addresses') <> '127.0.0.1'
    or coalesce(current_setting('gateb.lab_id',true),'') !~ '^[a-f0-9]{32}$'
    or not exists(select 1 from public.operational_data_active where version_id is not null) then
   raise exception 'STOP_UNIFICATION_LAB';
 end if;
end $$;

-- Inventory is an exact aggregate by mapped product/scope and month.
-- Distinct buy types cannot create two facts for the same resolved product/month.
create table public.operational_data_version_inventory (
 version_id uuid not null references public.operational_data_versions(id),
 chain_id uuid not null,
 product_id uuid not null,
 period date not null check(extract(day from period)=1),
 format text not null check(btrim(format)<>''),
 upc text not null check(upc ~ '^[0-9]+$'),
 purchase_type text not null check(purchase_type in ('RS','CE')),
 quantity numeric not null check(quantity>=0 and quantity<'Infinity'::numeric),
 source_reference text not null check(btrim(source_reference)<>''),
 primary key(version_id,chain_id,product_id,period),
 unique(version_id,chain_id,format,upc,period),
 foreign key(product_id,chain_id) references public.products(id,chain_id)
);
create trigger unification_inventory_frozen before insert or update or delete
 on public.operational_data_version_inventory for each row execute function private.gateb_freeze();

-- Existing seals remain byte-identical. Only new manifests with the extension opt in.
alter function private.gateb_payload(uuid) rename to unification_base_payload;
create function private.gateb_payload(p_version uuid) returns jsonb language sql stable set search_path='' as $$
 select private.unification_base_payload(v.id) ||
 case when v.document->>'contract'='unification-1' then jsonb_build_object(
   'inventory_aggregate',coalesce((select jsonb_agg(jsonb_build_object(
     'chain',i.chain_id,'product',i.product_id,'period',i.period,'format',i.format,'upc',i.upc,
     'purchase_type',i.purchase_type,'quantity',i.quantity::text,'source_reference',i.source_reference)
     order by i.chain_id,i.product_id,i.period)
   from public.operational_data_version_inventory i where i.version_id=v.id),'[]'::jsonb)
 ) else '{}'::jsonb end
 from public.operational_data_versions v where v.id=p_version;
$$;
revoke all on function private.gateb_payload(uuid) from public,anon,authenticated,service_role;

create function private.unification_seal_scope() returns trigger language plpgsql set search_path='' as $$
begin
 if exists(select 1 from public.operational_data_version_inventory i where i.version_id=new.id and
   (new.document->>'contract' is distinct from 'unification-1'
    or not(new.document->'chains' ? i.chain_id::text)
    or not(new.document->'products' ? i.product_id::text))) then
   raise exception 'inventory_outside_manifest';
 end if;
 if exists(select 1 from public.operational_data_version_inventory i where i.version_id=new.base_version_id
  and not exists(select 1 from public.operational_data_version_inventory n where n.version_id=new.id
    and (n.chain_id,n.product_id,n.period)=(i.chain_id,i.product_id,i.period))) then
   raise exception 'inventory_carry_forward_required';
 end if;
 if exists(select 1 from public.operational_data_version_observations m
   where m.version_id=new.base_version_id and m.period<'2025-01-01' and m.metric_code in ('SALES','ORDER','DELIVERY')
   and not exists(select 1 from public.operational_data_version_observations n where n.version_id=new.id
     and n.observation_id=m.observation_id)) then
   raise exception 'protected_real_history_changed';
 end if;
 return new;
end $$;
create trigger unification_seal_scope before update on public.operational_data_versions
 for each row when(new.sealed and not old.sealed) execute function private.unification_seal_scope();
revoke all on function private.unification_seal_scope() from public,anon,authenticated,service_role;

-- A draft stays outside public facts. Only an atomic import + seal + pointer
-- transaction can commit new physical rows once this gate is installed.
create function private.unification_publication_required() returns trigger language plpgsql set search_path='' as $$
begin
 if not exists(select 1 from public.operational_data_active a
   join public.operational_data_versions v on v.id=a.version_id and v.sealed
   join public.operational_data_version_observations m on m.version_id=v.id and m.observation_id=new.id
   where a.singleton) then raise exception 'observation_requires_atomic_publication'; end if;
 return null;
end $$;
create constraint trigger unification_publication_required after insert on public.monthly_observations
 deferrable initially deferred for each row execute function private.unification_publication_required();
revoke all on function private.unification_publication_required() from public,anon,authenticated,service_role;

-- Take the same singleton lock before importing any new rows, so two
-- promotions do not hold physical writes while competing for the pointer.
create function private.unification_lock_active(p_version uuid,p_generation bigint) returns void
 language plpgsql security definer set search_path='' as $$
declare a public.operational_data_active;
begin
 if not private.is_active_admin() then raise exception 'activation_forbidden' using errcode='42501'; end if;
 select * into a from public.operational_data_active where singleton for update;
 if not found or a.version_id is null or a.version_id is distinct from p_version
    or a.generation is distinct from p_generation then
    raise exception 'stale_active_generation' using errcode='40001';
 end if;
end $$;
revoke all on function private.unification_lock_active(uuid,bigint) from public,anon,authenticated,service_role;

-- Draft membership/inventory cannot be fetched by guessing a version ID.
-- Retained releases that were active remain usable for readers pinned earlier.
create index unification_published_versions on public.audit_log(entity_id)
 where entity_type='operational_data_version' and action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED');
create function private.unification_was_published(p_version uuid) returns boolean
 language sql stable security definer set search_path='' as $$
 select auth.uid() is not null and exists(select 1 from public.audit_log
   where entity_id=p_version and entity_type='operational_data_version'
     and action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED'));
$$;
revoke all on function private.unification_was_published(uuid) from public,anon,authenticated,service_role;
grant execute on function private.unification_was_published(uuid) to authenticated;
create policy unification_members_published on public.operational_data_version_observations as restrictive
 for select to authenticated using(private.unification_was_published(version_id));
create policy unification_versions_published on public.operational_data_versions as restrictive
 for select to authenticated using(private.unification_was_published(id));
alter table public.operational_data_version_inventory enable row level security;
revoke all on public.operational_data_version_inventory from public,anon,authenticated,service_role;
grant select on public.operational_data_version_inventory to authenticated;
create policy unification_inventory_read on public.operational_data_version_inventory for select to authenticated
 using(private.can_chain(chain_id,'view') and private.unification_was_published(version_id));

-- The existing names and column contracts follow the pointer. New historical
-- forecasts are exposed to the versioned reader, not as legacy actuals.
create or replace view public.portal_monthly_observations_current with(security_invoker=true) as
 select o.id observation_id,o.chain_id,c.code chain_code,c.name chain_name,o.product_id,p.product_code,
   p.variant_code,p.description product_description,p.category_id,o.period,o.metric_code,o.value,
   o.version_no,o.availability_source,o.available_at,o.source_batch_id
 from public.operational_data_active a
 join public.operational_data_version_observations m on m.version_id=a.version_id
 join public.monthly_observations o on o.id=m.observation_id and o.chain_id=m.chain_id
 join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
 join public.products p on p.id=o.product_id and p.chain_id=o.chain_id
 join public.chains c on c.id=o.chain_id
 where a.singleton and b.status='IMPORTED' and o.metric_code in ('SALES','ORDER','DELIVERY');

create or replace view public.monthly_observations_current with(security_invoker=true) as
 select o.* from public.operational_data_active a
 join public.operational_data_version_observations m on m.version_id=a.version_id
 join public.monthly_observations o on o.id=m.observation_id and o.chain_id=m.chain_id
 where a.singleton and o.available_at is not null and o.metric_code in ('SALES','ORDER','DELIVERY');

-- Exact denominator and numerator are returned alongside the ratio.
create function private.unification_fill_rate(p_version uuid)
 returns table(chain_id uuid,period date,delivery numeric,ordered numeric,fill_rate numeric)
 language sql stable security invoker set search_path='' as $$
 select x.chain_id,x.period,x.delivery,x.ordered,x.delivery/nullif(x.ordered,0) from (
   select m.chain_id,m.period,sum(o.value) filter(where m.metric_code='DELIVERY') delivery,
     sum(o.value) filter(where m.metric_code='ORDER') ordered
   from public.operational_data_version_observations m
   join public.monthly_observations o on o.id=m.observation_id and o.chain_id=m.chain_id
   where m.version_id=p_version and m.metric_code in ('DELIVERY','ORDER')
   group by m.chain_id,m.period
 ) x;
$$;
revoke all on function private.unification_fill_rate(uuid) from public,anon,authenticated,service_role;
grant execute on function private.unification_fill_rate(uuid) to authenticated;
