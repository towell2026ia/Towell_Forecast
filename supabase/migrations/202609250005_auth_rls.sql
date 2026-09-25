-- PRD 09.2C: verified Supabase Auth identity and fail-closed per-chain access.
-- The four 09.2B migrations are intentionally not changed.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;
alter default privileges in schema private revoke execute on functions from public;

create function private.is_active_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid() and p.status = 'ACTIVE' and p.global_role = 'ADMIN'
  );
$$;

create function private.can_chain(p_chain uuid, p_permission text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid() and p.status = 'ACTIVE'
      and (
        p.global_role = 'ADMIN'
        or (
          p.global_role in ('EDITOR', 'VIEWER')
          and (p_permission = 'view' or p.global_role = 'EDITOR')
          and exists (
            select 1 from public.user_chain_access a
            where a.user_id = p.id and a.chain_id = p_chain and a.can_view
              and case p_permission
                when 'view' then a.can_view
                when 'edit' then a.can_edit
                when 'import' then a.can_import
                when 'run_forecast' then a.can_run_forecast
                when 'approve' then a.can_approve
                else false
              end
          )
        )
      )
  );
$$;

create function private.can_read_run_log(p_job uuid, p_run uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select private.is_active_admin() or (
    (p_job is null or exists (
      select 1 from public.jobs j where j.id = p_job
        and j.chain_id is not null and private.can_chain(j.chain_id, 'view')
    ))
    and (p_run is null or exists (
      select 1 from public.forecast_runs r where r.id = p_run
        and private.can_chain(r.chain_id, 'view')
    ))
    and (p_job is not null or p_run is not null)
  );
$$;

create function private.has_open_decision(
  p_vintage uuid, p_product uuid, p_target date, p_version integer, p_forecast numeric
) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.forecast_decisions d
    where d.vintage_id = p_vintage and d.product_id = p_product
      and d.target_period = p_target and d.version_no = p_version - 1
      and d.forecast_towell = p_forecast and d.approved_by is null
      and p_version > 1
  );
$$;

revoke all on function private.is_active_admin() from public, anon;
revoke all on function private.can_chain(uuid,text) from public, anon;
revoke all on function private.can_read_run_log(uuid,uuid) from public, anon;
revoke all on function private.has_open_decision(uuid,uuid,date,integer,numeric) from public, anon;
grant execute on function private.is_active_admin() to authenticated;
grant execute on function private.can_chain(uuid,text) to authenticated;
grant execute on function private.can_read_run_log(uuid,uuid) to authenticated;
grant execute on function private.has_open_decision(uuid,uuid,date,integer,numeric) to authenticated;

-- Supabase's default public grants are permissive. Revoke first, then grant only
-- the verbs and columns that have reviewed policies below.
do $$ declare t text; begin
  foreach t in array array[
    'chains','profiles','user_chain_access','source_evidence','import_profiles',
    'import_profile_versions','import_batches','categories','products',
    'monthly_observations','customer_forecast_versions','customer_forecast_rows',
    'research_snapshots','research_snapshot_sources','forecast_runs','model_versions',
    'forecast_vintages','forecast_horizons','forecast_aggregates','champion_registry',
    'actual_evaluations','performance_metrics','forecast_decisions','jobs',
    'run_logs','audit_log','forecast_run_inputs','legacy_identity_map'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from public, anon, authenticated', t);
    execute format('grant select on table public.%I to authenticated', t);
  end loop;
end $$;
revoke all on table public.monthly_observations_current from public, anon, authenticated;
grant select on table public.monthly_observations_current to authenticated;

create policy chains_read on public.chains for select to authenticated
  using ((select private.can_chain(id, 'view')));
create policy chains_admin_insert on public.chains for insert to authenticated
  with check ((select private.is_active_admin()));
create policy chains_admin_update on public.chains for update to authenticated
  using ((select private.is_active_admin())) with check ((select private.is_active_admin()));
grant insert on public.chains to authenticated;
grant update (name,status,timezone,updated_at) on public.chains to authenticated;

create policy profiles_read on public.profiles for select to authenticated
  using ((select private.is_active_admin()) or (id = (select auth.uid()) and status = 'ACTIVE'));
create policy profiles_admin_insert on public.profiles for insert to authenticated
  with check ((select private.is_active_admin()) and id <> (select auth.uid()));
create policy profiles_admin_update on public.profiles for update to authenticated
  using ((select private.is_active_admin()) and id <> (select auth.uid()))
  with check ((select private.is_active_admin()) and id <> (select auth.uid()));
grant insert on public.profiles to authenticated;
grant update (full_name,status,global_role,updated_at) on public.profiles to authenticated;

create policy chain_access_read on public.user_chain_access for select to authenticated
  using ((select private.is_active_admin()) or
    (user_id = (select auth.uid()) and exists (
      select 1 from public.profiles p where p.id = (select auth.uid()) and p.status = 'ACTIVE'
    )));
create policy chain_access_admin_insert on public.user_chain_access for insert to authenticated
  with check ((select private.is_active_admin()) and user_id <> (select auth.uid()));
create policy chain_access_admin_update on public.user_chain_access for update to authenticated
  using ((select private.is_active_admin()) and user_id <> (select auth.uid()))
  with check ((select private.is_active_admin()) and user_id <> (select auth.uid()));
create policy chain_access_admin_delete on public.user_chain_access for delete to authenticated
  using ((select private.is_active_admin()) and user_id <> (select auth.uid()));
grant insert, update, delete on public.user_chain_access to authenticated;

-- All directly chain-scoped result tables are visible only with can_view.
-- Engine-produced rows remain service-writable only, even for ADMIN users.
do $$ declare t text; begin
  foreach t in array array[
    'import_profiles','import_profile_versions','import_batches','categories','products',
    'monthly_observations','customer_forecast_versions','customer_forecast_rows',
    'research_snapshots','research_snapshot_sources','forecast_runs','model_versions',
    'forecast_vintages','forecast_horizons','forecast_aggregates','champion_registry',
    'actual_evaluations','performance_metrics','forecast_decisions','forecast_run_inputs'
  ] loop
    execute format('create policy %I on public.%I for select to authenticated using (private.can_chain(chain_id, %L))', t || '_read', t, 'view');
  end loop;
end $$;

create policy source_evidence_read on public.source_evidence for select to authenticated
  using ((select private.is_active_admin()) or
    (chain_id is not null and private.can_chain(chain_id, 'view')));
create policy jobs_read on public.jobs for select to authenticated
  using ((select private.is_active_admin()) or
    (chain_id is not null and private.can_chain(chain_id, 'view')));
create policy run_logs_read on public.run_logs for select to authenticated
  using (private.can_read_run_log(job_id, run_id));
create policy audit_log_admin_read on public.audit_log for select to authenticated
  using ((select private.is_active_admin()));
create policy legacy_identity_map_admin_read on public.legacy_identity_map for select to authenticated
  using ((select private.is_active_admin()));

-- Master data edits never update identity, chain, or lineage columns.
create policy categories_edit_insert on public.categories for insert to authenticated
  with check (private.can_chain(chain_id, 'edit'));
create policy categories_edit_update on public.categories for update to authenticated
  using (private.can_chain(chain_id, 'edit')) with check (private.can_chain(chain_id, 'edit'));
grant insert on public.categories to authenticated;
grant update (name,status) on public.categories to authenticated;

create policy products_edit_insert on public.products for insert to authenticated
  with check (private.can_chain(chain_id, 'edit'));
create policy products_edit_update on public.products for update to authenticated
  using (private.can_chain(chain_id, 'edit')) with check (private.can_chain(chain_id, 'edit'));
grant insert on public.products to authenticated;
grant update (category_id,description,variant_description,status,last_seen_period,updated_at)
  on public.products to authenticated;

create policy import_profiles_insert on public.import_profiles for insert to authenticated
  with check (private.can_chain(chain_id, 'import'));
create policy import_profiles_update on public.import_profiles for update to authenticated
  using (private.can_chain(chain_id, 'import')) with check (private.can_chain(chain_id, 'import'));
grant insert on public.import_profiles to authenticated;
grant update (name,status) on public.import_profiles to authenticated;

create policy import_profile_versions_insert on public.import_profile_versions for insert to authenticated
  with check (private.can_chain(chain_id, 'import') and created_by = (select auth.uid()));
grant insert on public.import_profile_versions to authenticated;

-- Client may request an upload, but cannot forge its timestamp, owner or status.
create function private.stamp_import_batch() returns trigger
language plpgsql set search_path = '' as $$
begin
  if current_user = 'authenticated' then
    new.uploaded_at := statement_timestamp();
    new.uploaded_by := auth.uid();
    new.availability_source := 'SYSTEM_INGESTION';
  end if;
  return new;
end;
$$;
revoke all on function private.stamp_import_batch() from public, anon, authenticated;
create trigger stamp_import_batch before insert on public.import_batches
  for each row execute function private.stamp_import_batch();
create policy import_batches_insert on public.import_batches for insert to authenticated
  with check (private.can_chain(chain_id, 'import') and uploaded_by = (select auth.uid())
    and status = 'UPLOADED' and row_count = 0 and valid_rows = 0 and rejected_rows = 0);
grant insert on public.import_batches to authenticated;

create policy customer_forecast_versions_insert on public.customer_forecast_versions for insert to authenticated
  with check (private.can_chain(chain_id, 'edit') and created_by = (select auth.uid())
    and status = 'DRAFT' and validated_by is null);
grant insert on public.customer_forecast_versions to authenticated;
create policy customer_forecast_rows_insert on public.customer_forecast_rows for insert to authenticated
  with check (private.can_chain(chain_id, 'edit'));
grant insert on public.customer_forecast_rows to authenticated;

-- A user may enqueue a forecast; only the backend service advances the job.
create policy jobs_request_forecast on public.jobs for insert to authenticated
  with check (chain_id is not null and private.can_chain(chain_id, 'run_forecast')
    and requested_by = (select auth.uid()) and job_type = 'FORECAST_RUN'
    and status = 'QUEUED' and run_id is null and started_at is null
    and finished_at is null and error_code is null and metadata_json = '{}'::jsonb);
grant insert on public.jobs to authenticated;

-- Versioned managerial proposals and approvals. The 09.2B append-only trigger
-- forbids UPDATE, so approval is a new row referencing the prior open version.
create policy forecast_decisions_propose on public.forecast_decisions for insert to authenticated
  with check (private.can_chain(chain_id, 'edit') and created_by = (select auth.uid())
    and approved_by is null and approved_at is null);
create policy forecast_decisions_approve on public.forecast_decisions for insert to authenticated
  with check (private.can_chain(chain_id, 'approve') and created_by = (select auth.uid())
    and approved_by = (select auth.uid()) and approved_at is not null
    and approved_value is not null
    and private.has_open_decision(vintage_id, product_id, target_period, version_no, forecast_towell));
grant insert on public.forecast_decisions to authenticated;
