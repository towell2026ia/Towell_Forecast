-- Additive laboratory hardening; frozen manifests are always canonical in UTC.
do $$ begin
 if host(inet_server_addr())<>'127.0.0.1' or current_database() !~ '^gateb_'
    or length(coalesce(current_setting('gateb.lab_id',true),''))<>32 then raise exception 'STOP_B0'; end if;
end $$;
alter function private.gateb_catalog_hash() set timezone to 'UTC';
alter function private.gateb_physical_hash() set timezone to 'UTC';
alter function private.gateb_payload(uuid) set timezone to 'UTC';
