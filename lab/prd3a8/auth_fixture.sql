-- Synthetic Auth contract for native PostgreSQL tests, NOT a production Auth migration.
do $$ begin
  if current_database() !~ '^gateb_' or host(inet_server_addr()) <> '127.0.0.1'
     or length(coalesce(current_setting('gateb.lab_id',true),'')) <> 32 then
    raise exception 'STOP_B0';
  end if;
end $$;
do $$ begin
 if not exists(select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
 if not exists(select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
 if not exists(select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;
create schema auth;
create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
$$;
grant usage on schema auth to authenticated,anon,service_role;
grant execute on function auth.uid() to authenticated,anon,service_role;
