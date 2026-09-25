-- PRD 09.2C: private buckets and verified chain-scoped object access.
-- Path: chain_uuid/YYYY-MM/batch_uuid/safe_filename
create function private.storage_chain_id(p_name text) returns uuid
language plpgsql immutable set search_path = '' as $$
declare parts text[];
begin
  parts := string_to_array(p_name, '/');
  if array_length(parts, 1) <> 4
    or parts[1] !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    or parts[2] !~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
    or parts[3] !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    or parts[4] !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    or parts[4] in ('.', '..') then
    return null;
  end if;
  return parts[1]::uuid;
end;
$$;
revoke all on function private.storage_chain_id(text) from public, anon;
grant execute on function private.storage_chain_id(text) to authenticated;

insert into storage.buckets (id, name, public) values
  ('source-files', 'source-files', false),
  ('research-evidence', 'research-evidence', false),
  ('model-artifacts', 'model-artifacts', false),
  ('exports', 'exports', false)
on conflict (id) do update set public = false;

-- Storage owns the object table; its standard grants stay intact. With no
-- policy for anon, UPDATE or DELETE, these operations fail closed.
create policy towell_private_storage_read on storage.objects for select to authenticated
  using (bucket_id in ('source-files','research-evidence','model-artifacts','exports')
    and private.storage_chain_id(name) is not null
    and private.can_chain(private.storage_chain_id(name), 'view'));

create policy towell_private_source_upload on storage.objects for insert to authenticated
  with check (bucket_id = 'source-files'
    and private.storage_chain_id(name) is not null
    and private.can_chain(private.storage_chain_id(name), 'import'));

create policy towell_private_research_upload on storage.objects for insert to authenticated
  with check (bucket_id = 'research-evidence'
    and private.storage_chain_id(name) is not null
    and private.can_chain(private.storage_chain_id(name), 'import'));
