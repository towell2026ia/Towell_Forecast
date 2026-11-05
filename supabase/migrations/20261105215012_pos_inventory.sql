-- PRD 3A.2: structure only. Applying this migration requires separate approval.
-- Stock is a dated snapshot, never an additive monthly flow.
create table public.pos_inventory_snapshots (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id),
  account_code text not null check (length(btrim(account_code)) > 0),
  cutoff_date date not null,
  version_no integer not null check (version_no > 0),
  expected_store_codes text[] not null check (cardinality(expected_store_codes) > 0),
  source_reference text not null check (length(btrim(source_reference)) > 0),
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default now(),
  unique (chain_id, account_code, cutoff_date, version_no),
  unique (id, chain_id)
);
create table public.pos_inventory_observations (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  store_code text not null check (length(btrim(store_code)) > 0),
  quantity numeric not null check (quantity >= 0 and quantity <> 'NaN'::numeric and quantity <> 'Infinity'::numeric),
  unit text not null default 'PIEZAS' check (unit = 'PIEZAS'),
  source_reference text not null check (length(btrim(source_reference)) > 0),
  created_at timestamptz not null default now(),
  foreign key (snapshot_id, chain_id) references public.pos_inventory_snapshots(id, chain_id),
  foreign key (product_id, chain_id) references public.products(id, chain_id),
  unique (snapshot_id, product_id, store_code)
);
create index pos_inventory_product_scope on public.pos_inventory_observations(chain_id, product_id, snapshot_id);

create function private.validate_pos_inventory() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  if tg_op <> 'INSERT' then
    raise exception 'Inventory snapshots are append-only; create a new version' using errcode = '23514';
  end if;
  if tg_table_name = 'pos_inventory_snapshots' then
    if exists (select 1 from unnest(new.expected_store_codes) s where s is null or s = '' or s <> btrim(s))
      or cardinality(new.expected_store_codes) <> (select count(distinct s) from unnest(new.expected_store_codes) s) then
      raise exception 'Expected stores must be nonblank and unique' using errcode = '23514';
    end if;
  elsif not exists (select 1 from public.pos_inventory_snapshots s where s.id = new.snapshot_id
    and s.chain_id = new.chain_id and new.store_code = any(s.expected_store_codes)) then
    raise exception 'Store is outside the snapshot scope' using errcode = '23514';
  end if;
  return new;
end $$;
create trigger validate_pos_inventory_snapshot before insert or update or delete on public.pos_inventory_snapshots
  for each row execute function private.validate_pos_inventory();
create trigger validate_pos_inventory_observation before insert or update or delete on public.pos_inventory_observations
  for each row execute function private.validate_pos_inventory();
revoke all on function private.validate_pos_inventory() from public;
alter table public.pos_inventory_snapshots enable row level security;
alter table public.pos_inventory_observations enable row level security;
create policy pos_inventory_snapshot_read on public.pos_inventory_snapshots for select to authenticated
  using (private.can_chain(chain_id, 'view'));
create policy pos_inventory_observation_read on public.pos_inventory_observations for select to authenticated
  using (private.can_chain(chain_id, 'view'));
-- No ingestion route or writing role is activated in this phase.
revoke all on public.pos_inventory_snapshots, public.pos_inventory_observations from anon, authenticated, service_role;
grant select on public.pos_inventory_snapshots, public.pos_inventory_observations to authenticated;
comment on table public.pos_inventory_snapshots is 'PRD 3A.2 structure only. Date/store scope and immutable version; missing rows mean unknown, not zero.';
comment on table public.pos_inventory_observations is 'PIEZAS stock at one product/store/date via snapshot. Never sum dates or different store sets. No production ingestion authorized.';
