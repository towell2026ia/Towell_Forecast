-- Portal user administration. Auth identities are created by the server-side
-- Edge Function; this transaction only grants/revokes application access.
-- No historical or forecast tables are modified.
create function public.portal_manage_user(
  p_actor_id uuid,
  p_target_id uuid,
  p_full_name text,
  p_role text,
  p_status text,
  p_access jsonb
) returns void
language plpgsql security invoker set search_path = '' as $$
declare
  previous public.profiles%rowtype;
  grant_row jsonb;
  grant_chain uuid;
  seen_chains uuid[] := array[]::uuid[];
  can_edit boolean;
  can_import boolean;
  can_run_forecast boolean;
  can_approve boolean;
begin
  if p_actor_id is null or p_target_id is null or p_actor_id = p_target_id or
     not exists(select 1 from public.profiles where id = p_actor_id and status = 'ACTIVE' and global_role = 'ADMIN') then
    raise exception 'admin authorization required' using errcode = '42501';
  end if;
  if p_full_name is null or length(btrim(p_full_name)) not between 2 and 150 or
     p_role is null or p_role not in ('ADMIN','EDITOR','VIEWER') or
     p_status is null or p_status not in ('ACTIVE','INACTIVE') or
     p_access is null or jsonb_typeof(p_access) <> 'array' or jsonb_array_length(p_access) > 1000 then
    raise exception 'invalid user management request' using errcode = '23514';
  end if;
  if (p_role = 'ADMIN' or p_status = 'INACTIVE') and jsonb_array_length(p_access) <> 0 then
    raise exception 'admin or inactive access must be empty' using errcode = '23514';
  end if;
  if p_role <> 'ADMIN' and p_status = 'ACTIVE' and jsonb_array_length(p_access) = 0 then
    raise exception 'active non-admin needs explicit chain access' using errcode = '23514';
  end if;

  -- Serialize role changes so concurrent requests cannot remove the last admin.
  perform pg_advisory_xact_lock(hashtextextended('portal-active-admins', 0));
  select * into previous from public.profiles where id = p_target_id for update;
  if previous.id is not null and previous.status = 'ACTIVE' and previous.global_role = 'ADMIN' and
     (p_status <> 'ACTIVE' or p_role <> 'ADMIN') and
     (select count(*) from public.profiles where status = 'ACTIVE' and global_role = 'ADMIN') <= 1 then
    raise exception 'last active admin cannot be removed' using errcode = '23514';
  end if;

  for grant_row in select value from jsonb_array_elements(p_access) loop
    if jsonb_typeof(grant_row) <> 'object' or
       (select count(*) from jsonb_object_keys(grant_row)) <> 5 or
       not (grant_row ?& array['chain_id','can_edit','can_import','can_run_forecast','can_approve']) or
       jsonb_typeof(grant_row->'chain_id') <> 'string' or
       jsonb_typeof(grant_row->'can_edit') <> 'boolean' or
       jsonb_typeof(grant_row->'can_import') <> 'boolean' or
       jsonb_typeof(grant_row->'can_run_forecast') <> 'boolean' or
       jsonb_typeof(grant_row->'can_approve') <> 'boolean' then
      raise exception 'invalid chain grant' using errcode = '23514';
    end if;
    grant_chain := (grant_row->>'chain_id')::uuid;
    if grant_chain = any(seen_chains) or
       not exists(select 1 from public.chains where id = grant_chain and status = 'ACTIVE') then
      raise exception 'duplicate or inactive chain grant' using errcode = '23514';
    end if;
    seen_chains := array_append(seen_chains, grant_chain);
    can_edit := (grant_row->>'can_edit')::boolean;
    can_import := (grant_row->>'can_import')::boolean;
    can_run_forecast := (grant_row->>'can_run_forecast')::boolean;
    can_approve := (grant_row->>'can_approve')::boolean;
    if p_role = 'VIEWER' and (can_edit or can_import or can_run_forecast or can_approve) then
      raise exception 'viewer cannot receive write permissions' using errcode = '23514';
    end if;
  end loop;

  insert into public.profiles(id, full_name, global_role, status)
    values(p_target_id, btrim(p_full_name), p_role, p_status)
    on conflict (id) do update set full_name = excluded.full_name,
      global_role = excluded.global_role, status = excluded.status, updated_at = now();
  delete from public.user_chain_access where user_id = p_target_id;
  for grant_row in select value from jsonb_array_elements(p_access) loop
    insert into public.user_chain_access(
      user_id, chain_id, can_view, can_edit, can_import, can_run_forecast, can_approve
    ) values(
      p_target_id, (grant_row->>'chain_id')::uuid, true,
      (grant_row->>'can_edit')::boolean, (grant_row->>'can_import')::boolean,
      (grant_row->>'can_run_forecast')::boolean, (grant_row->>'can_approve')::boolean
    );
  end loop;
  insert into public.audit_log(actor_id, action, entity_type, entity_id, old_data, new_data)
    values(p_actor_id,
      case when p_status = 'INACTIVE' then 'PORTAL_USER_DEACTIVATED'
           when previous.id is null then 'PORTAL_USER_CREATED' else 'PORTAL_USER_UPDATED' end,
      'profile', p_target_id,
      case when previous.id is null then null else jsonb_build_object(
        'role', previous.global_role, 'status', previous.status) end,
      jsonb_build_object('role', p_role, 'status', p_status, 'chain_count', jsonb_array_length(p_access)));
end $$;

revoke all on function public.portal_manage_user(uuid,uuid,text,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.portal_manage_user(uuid,uuid,text,text,text,jsonb) to service_role;
