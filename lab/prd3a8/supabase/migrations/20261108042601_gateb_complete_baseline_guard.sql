-- Additional local completeness guard. Existing applied files are unchanged.
do $$ begin
 if host(inet_server_addr())<>'127.0.0.1' or current_database() !~ '^gateb_'
    or length(coalesce(current_setting('gateb.lab_id',true),''))<>32 then raise exception 'STOP_B0'; end if;
end $$;
create function private.gateb_complete_baseline() returns trigger language plpgsql set search_path='' as $$
begin
 if exists(select 1 from public.products p where not(new.document->'products' ? p.id::text)) then
   raise exception 'incomplete_global_catalog'; end if;
 if exists(select 1 from public.products p where not(new.document->'chains' ? p.chain_id::text)) then
   raise exception 'catalog_chain_outside_release'; end if;
 if new.base_version_id is null then
   if exists(select 1 from public.operational_data_active where version_id is not null) then
     raise exception 'additional_root_not_allowed'; end if;
   if exists(
     select 1 from (
       select distinct on(o.chain_id,o.product_id,o.period,o.metric_code) o.id
       from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id and b.chain_id=o.chain_id
       where b.status='IMPORTED'
       order by o.chain_id,o.product_id,o.period,o.metric_code,o.version_no desc
     ) baseline where not exists(select 1 from public.operational_data_version_observations m
       where m.version_id=new.id and m.observation_id=baseline.id)
   ) then raise exception 'incomplete_initial_selection'; end if;
 end if;
 return new;
end $$;
create trigger gateb_complete_baseline before update on public.operational_data_versions
 for each row when(new.sealed and not old.sealed) execute function private.gateb_complete_baseline();
revoke all on function private.gateb_complete_baseline() from public,anon,authenticated;
