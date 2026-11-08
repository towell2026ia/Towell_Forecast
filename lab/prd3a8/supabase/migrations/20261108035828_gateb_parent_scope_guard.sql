-- Local additive correction; previously applied migrations remain immutable.
do $$ begin
 if host(inet_server_addr())<>'127.0.0.1' or current_database() !~ '^gateb_'
    or length(coalesce(current_setting('gateb.lab_id',true),''))<>32 then raise exception 'STOP_B0'; end if;
end $$;
create or replace function private.gateb_seal(p_version uuid) returns text language plpgsql security definer set search_path='' as $$
declare v public.operational_data_versions; h text;
begin
 if not private.is_active_admin() then raise exception 'activation_forbidden' using errcode='42501'; end if;
 select * into v from public.operational_data_versions where id=p_version for update;
 if not found then raise exception 'manifest_not_found'; end if;
 if v.sealed then return v.manifest_hash; end if;
 if not (v.document ?& array['chains','products','absences','exclusions','policy']) then raise exception 'incomplete_manifest'; end if;
 if v.base_version_id is not null and not exists(select 1 from public.operational_data_versions where id=v.base_version_id and sealed) then
  raise exception 'parent_not_sealed'; end if;
 if exists(select 1 from public.operational_data_version_observations m where m.version_id=v.id
    and (not(v.document->'chains' ? m.chain_id::text) or not(v.document->'products' ? m.product_id::text))) then
    raise exception 'member_outside_scope'; end if;
 if exists(select 1 from public.operational_data_version_observations old
   where old.version_id=v.base_version_id and not exists(select 1 from public.operational_data_version_observations n
   where n.version_id=v.id and (n.chain_id,n.product_id,n.period,n.metric_code)=(old.chain_id,old.product_id,old.period,old.metric_code))) then
   raise exception 'missing_explicit_carry_forward'; end if;
 if exists(select 1 from public.operational_data_versions parent where parent.id=v.base_version_id
    and not ((v.document->'products') @> (parent.document->'products'))) then raise exception 'catalog_product_removed'; end if;
 if exists(select 1 from public.operational_data_version_observations m where m.version_id=v.id and
   v.document->'absences' ? (m.chain_id::text||'/'||m.product_id::text||'/'||to_char(m.period,'YYYY-MM')||'/'||m.metric_code)) then
   raise exception 'absence_conflicts_with_member'; end if;
 if exists(select 1 from public.operational_data_version_observations m join public.monthly_observations o on o.id=m.observation_id
   left join public.import_batches b on b.id=o.source_batch_id where m.version_id=v.id and (b.id is null or b.status<>'IMPORTED')) then
   raise exception 'batch_not_imported'; end if;
 update public.operational_data_versions set catalog_hash=private.gateb_catalog_hash(),physical_hash=private.gateb_physical_hash() where id=v.id;
 h=private.gateb_manifest_hash(v.id);
 update public.operational_data_versions set manifest_hash=h,sealed=true where id=v.id;
 return h;
end $$;
