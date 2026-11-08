-- GATE B LAB ONLY. Not part of supabase/migrations, never apply remotely.
do $$ begin
  if current_database() !~ '^gateb_' or host(inet_server_addr()) <> '127.0.0.1'
     or current_setting('listen_addresses') <> '127.0.0.1'
     or length(coalesce(current_setting('gateb.lab_id',true),'')) <> 32 then
    raise exception 'STOP_B0';
  end if;
end $$;

-- Precision is a laboratory alternative, NOT a production choice.
drop view public.portal_history_periods;
drop view public.portal_monthly_observations_current;
drop view public.monthly_observations_current;
alter table public.monthly_observations alter column value type numeric(31,16);
alter table public.monthly_observations drop constraint monthly_observations_metric_code_check;
alter table public.monthly_observations add constraint monthly_observations_metric_code_check
 check(metric_code in ('SALES','ORDER','DELIVERY','FCST_TOWELL_HISTORY','FCST_CLIENTE_HISTORY'));
alter table public.monthly_observations add constraint gateb_observation_exact_identity
 unique(id,chain_id,product_id,period,metric_code);

-- Legacy projections remain three-metric. They are NOT the operational lab reader.
create view public.monthly_observations_current with(security_invoker=true) as
 select distinct on(chain_id,product_id,period,metric_code) * from public.monthly_observations
 where available_at is not null and metric_code in ('SALES','ORDER','DELIVERY')
 order by chain_id,product_id,period,metric_code,version_no desc;
create view public.portal_monthly_observations_current with(security_invoker=true) as
 select distinct on(o.chain_id,o.product_id,o.period,o.metric_code)
 o.id observation_id,o.chain_id,c.code chain_code,c.name chain_name,o.product_id,p.product_code,
 p.variant_code,p.description product_description,p.category_id,o.period,o.metric_code,o.value,
 o.version_no,o.availability_source,o.available_at,o.source_batch_id
 from public.monthly_observations o
 join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
 join public.products p on p.id=o.product_id and p.chain_id=o.chain_id
 join public.chains c on c.id=o.chain_id
 where b.status='IMPORTED' and o.metric_code in ('SALES','ORDER','DELIVERY')
 order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc;
create view public.portal_history_periods with(security_invoker=true) as
 select distinct chain_id,period from public.portal_monthly_observations_current;
revoke all on public.monthly_observations_current,public.portal_monthly_observations_current,
 public.portal_history_periods from public,anon,authenticated;
grant select on public.monthly_observations_current,public.portal_monthly_observations_current,
 public.portal_history_periods to authenticated;

create table public.operational_data_versions(
 id uuid primary key,
 base_version_id uuid references public.operational_data_versions(id),
 document jsonb not null check(jsonb_typeof(document)='object'),
 sealed boolean not null default false,
 manifest_hash text,
 catalog_hash text,
 physical_hash text,
 created_at timestamptz not null default now(),
 check(not sealed or (manifest_hash ~ '^[0-9a-f]{64}$' and catalog_hash is not null and physical_hash is not null))
);
create table public.operational_data_version_observations(
 version_id uuid not null references public.operational_data_versions(id),
 chain_id uuid not null,product_id uuid not null,period date not null,metric_code text not null,
 observation_id uuid not null,
 primary key(version_id,chain_id,product_id,period,metric_code),
 foreign key(observation_id,chain_id,product_id,period,metric_code)
 references public.monthly_observations(id,chain_id,product_id,period,metric_code)
);
create table public.operational_data_active(
 singleton boolean primary key default true check(singleton),
 version_id uuid references public.operational_data_versions(id),
 generation bigint not null default 0 check(generation>=0)
);
insert into public.operational_data_active(singleton) values(true);
create index gateb_members_by_observation on public.operational_data_version_observations(observation_id);
create unique index gateb_transition_request on public.audit_log(request_id)
 where action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED');

create function private.gateb_freeze() returns trigger language plpgsql set search_path='' as $$
declare v uuid;
begin
 if tg_table_name='operational_data_versions' then
   if old.sealed then raise exception 'sealed_manifest_immutable'; end if;
 else
   v=case when tg_op='DELETE' then old.version_id else new.version_id end;
   if exists(select 1 from public.operational_data_versions where id=v and sealed)
     or (tg_op='UPDATE' and exists(select 1 from public.operational_data_versions where id=old.version_id and sealed)) then
     raise exception 'sealed_members_immutable';
   end if;
 end if;
 return case when tg_op='DELETE' then old else new end;
end $$;
create trigger gateb_version_frozen before update or delete on public.operational_data_versions
 for each row execute function private.gateb_freeze();
create trigger gateb_members_frozen before insert or update or delete on public.operational_data_version_observations
 for each row execute function private.gateb_freeze();

create function private.gateb_catalog_hash() returns text language sql stable set search_path='' as $$
 select encode(sha256(convert_to(coalesce(string_agg(to_jsonb(p)::text,E'\n' order by p.id),''),'UTF8')),'hex') from public.products p;
$$;
create function private.gateb_physical_hash() returns text language sql stable set search_path='' as $$
 select encode(sha256(convert_to(coalesce(string_agg(to_jsonb(o)::text,E'\n' order by o.id),''),'UTF8')),'hex') from public.monthly_observations o;
$$;
create function private.gateb_payload(p_version uuid) returns jsonb language sql stable set search_path='' as $$
 select jsonb_build_object('schema','gateb-release-1','id',v.id,'parent',v.base_version_id,'document',v.document,
 'created_at',v.created_at,'catalog_hash',v.catalog_hash,'physical_hash',v.physical_hash,
 'members',coalesce((select jsonb_agg(jsonb_build_object('chain',m.chain_id,'product',m.product_id,
 'period',m.period,'metric',m.metric_code,'id',o.id,'value',o.value::text,'version',o.version_no,
 'batch',o.source_batch_id,'available_at',o.available_at,'availability_source',o.availability_source)
 order by m.chain_id,m.product_id,m.period,m.metric_code)
 from public.operational_data_version_observations m join public.monthly_observations o on o.id=m.observation_id
 where m.version_id=v.id),'[]'::jsonb),
 'batches',coalesce((select jsonb_agg(to_jsonb(b) order by b.id) from public.import_batches b
 where b.id in(select o.source_batch_id from public.operational_data_version_observations m
 join public.monthly_observations o on o.id=m.observation_id where m.version_id=v.id)),'[]'::jsonb))
 from public.operational_data_versions v where v.id=p_version;
$$;
create function private.gateb_manifest_hash(p_version uuid) returns text language sql stable set search_path='' as $$
 select encode(sha256(convert_to(private.gateb_payload(p_version)::text,'UTF8')),'hex');
$$;

create function private.gateb_seal(p_version uuid) returns text language plpgsql security definer set search_path='' as $$
declare v public.operational_data_versions; h text;
begin
 if not private.is_active_admin() then raise exception 'activation_forbidden' using errcode='42501'; end if;
 select * into v from public.operational_data_versions where id=p_version for update;
 if not found then raise exception 'manifest_not_found'; end if;
 if v.sealed then return v.manifest_hash; end if;
 if not (v.document ?& array['chains','products','absences','exclusions','policy']) then raise exception 'incomplete_manifest'; end if;
 if v.base_version_id is not null and not exists(select 1 from public.operational_data_versions where id=v.base_version_id and sealed) then
  raise exception 'parent_not_sealed';
 end if;
 if exists(select 1 from public.operational_data_version_observations m where m.version_id=v.id
    and (not(v.document->'chains' ? m.chain_id::text) or not(v.document->'products' ? m.product_id::text))) then
    raise exception 'member_outside_scope';
 end if;
 if exists(select 1 from public.operational_data_version_observations old
   where old.version_id=v.base_version_id and not exists(select 1 from public.operational_data_version_observations n
   where n.version_id=v.id and (n.chain_id,n.product_id,n.period,n.metric_code)=(old.chain_id,old.product_id,old.period,old.metric_code))) then
   raise exception 'missing_explicit_carry_forward';
 end if;
 if exists(select 1 from public.operational_data_versions parent where parent.id=v.base_version_id
    and not(v.document->'products' @> parent.document->'products')) then raise exception 'catalog_product_removed'; end if;
 if exists(select 1 from public.operational_data_version_observations m where m.version_id=v.id and
   v.document->'absences' ? (m.chain_id::text||'/'||m.product_id::text||'/'||to_char(m.period,'YYYY-MM')||'/'||m.metric_code)) then
   raise exception 'absence_conflicts_with_member';
 end if;
 if exists(select 1 from public.operational_data_version_observations m join public.monthly_observations o on o.id=m.observation_id
   left join public.import_batches b on b.id=o.source_batch_id where m.version_id=v.id and (b.id is null or b.status<>'IMPORTED')) then
   raise exception 'batch_not_imported';
 end if;
 update public.operational_data_versions set catalog_hash=private.gateb_catalog_hash(),physical_hash=private.gateb_physical_hash() where id=v.id;
 h=private.gateb_manifest_hash(v.id);
 update public.operational_data_versions set manifest_hash=h,sealed=true where id=v.id;
 return h;
end $$;

create function private.gateb_activate(p_target uuid,p_hash text,p_expected uuid,p_generation bigint,p_request text,p_fail boolean default false)
 returns jsonb language plpgsql security definer set search_path='' as $$
declare a public.operational_data_active; v public.operational_data_versions; e public.audit_log;
 request_hash text; result jsonb; transition text; already_used boolean;
begin
 if host(inet_server_addr())<>'127.0.0.1' or current_database() !~ '^gateb_' or length(coalesce(current_setting('gateb.lab_id',true),''))<>32 then
   raise exception 'STOP_B0'; end if;
 if not private.is_active_admin() then raise exception 'activation_forbidden' using errcode='42501'; end if;
 if p_request is null or length(p_request) not between 8 and 150 then raise exception 'invalid_request_id'; end if;
 request_hash=encode(sha256(convert_to(jsonb_build_array(p_target,p_hash,p_expected,p_generation,auth.uid())::text,'UTF8')),'hex');
 select * into a from public.operational_data_active where singleton for update;
 select * into e from public.audit_log where request_id=p_request and action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED');
 if found then
   if e.new_data->>'request_hash'<>request_hash then raise exception 'idempotency_conflict'; end if;
   return e.new_data||jsonb_build_object('already_applied',true,'active_now',a.version_id,'generation_now',a.generation);
 end if;
 if a.version_id is distinct from p_expected or a.generation<>p_generation then raise exception 'stale_active_generation' using errcode='40001'; end if;
 select * into v from public.operational_data_versions where id=p_target;
 if not found or not v.sealed or v.manifest_hash is distinct from p_hash or private.gateb_manifest_hash(v.id) is distinct from p_hash then
   raise exception 'manifest_integrity_failure'; end if;
 -- Serialize all local writers during the short fingerprint/CAS/audit transaction.
 lock table public.monthly_observations,public.products,public.import_batches,public.import_profile_versions,public.source_evidence in share mode;
 if private.gateb_catalog_hash() is distinct from v.catalog_hash then raise exception 'catalog_changed_since_seal'; end if;
 select exists(select 1 from public.audit_log where action in ('DATA_VERSION_ACTIVATED','DATA_VERSION_REVERTED') and entity_id=v.id) into already_used;
 -- Returning to a previously used immutable set must tolerate B's retained new facts.
 if not already_used and private.gateb_physical_hash() is distinct from v.physical_hash then raise exception 'unexpected_physical_delta'; end if;
 if a.version_id=p_target then return jsonb_build_object('version_id',v.id,'generation',a.generation,'unchanged',true); end if;
 transition=case when exists(select 1 from public.operational_data_versions where id=a.version_id and base_version_id=v.id)
   then 'DATA_VERSION_REVERTED' else 'DATA_VERSION_ACTIVATED' end;
 update public.operational_data_active set version_id=v.id,generation=generation+1 where singleton;
 result=jsonb_build_object('version_id',v.id,'previous_id',a.version_id,'generation',a.generation+1,
   'manifest_hash',p_hash,'request_hash',request_hash,'actor',auth.uid(),'activated_at',clock_timestamp());
 insert into public.audit_log(actor_id,action,entity_type,entity_id,old_data,new_data,request_id)
 values(auth.uid(),transition,'operational_data_version',v.id,to_jsonb(a),result,p_request);
 if p_fail then raise exception 'injected_activation_failure'; end if;
 return result;
end $$;

alter table public.operational_data_versions enable row level security;
alter table public.operational_data_version_observations enable row level security;
alter table public.operational_data_active enable row level security;
create policy gateb_members_read on public.operational_data_version_observations for select to authenticated
 using(private.can_chain(chain_id,'view'));
create policy gateb_versions_read on public.operational_data_versions for select to authenticated
 using(private.is_active_admin() or exists(select 1 from public.operational_data_version_observations m
 where m.version_id=id and private.can_chain(m.chain_id,'view')));
create policy gateb_active_read on public.operational_data_active for select to authenticated using(auth.uid() is not null);
revoke all on public.operational_data_versions,public.operational_data_version_observations,public.operational_data_active from public,anon,authenticated;
grant select(id,base_version_id,sealed,manifest_hash,created_at) on public.operational_data_versions to authenticated;
grant select on public.operational_data_version_observations,public.operational_data_active to authenticated;
revoke all on function private.gateb_payload(uuid),private.gateb_catalog_hash(),private.gateb_physical_hash(),private.gateb_manifest_hash(uuid),
 private.gateb_freeze(),private.gateb_seal(uuid),private.gateb_activate(uuid,text,uuid,bigint,text,boolean) from public,anon,authenticated;
grant execute on function private.gateb_seal(uuid),private.gateb_activate(uuid,text,uuid,bigint,text,boolean) to authenticated;
