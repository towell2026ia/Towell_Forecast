-- Manual rollback only; never discard inventory evidence. No CASCADE.
begin;
do $$ begin
  if exists(select 1 from public.pos_inventory_observations) or exists(select 1 from public.pos_inventory_snapshots) then
    raise exception 'STOP: inventory evidence exists; preserve it and prepare a reviewed forward migration';
  end if;
end $$;
drop table public.pos_inventory_observations;
drop table public.pos_inventory_snapshots;
drop function private.validate_pos_inventory();
commit;
