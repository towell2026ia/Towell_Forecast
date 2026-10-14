-- PRD 09.2E5-01B-A.1. SCHEMA ONLY: guarded preflight required before remote apply.
-- No seeds, backfill, runtime endpoint, or changes to existing business rows.
-- Product types are an open dictionary, independent of models and sizes.
create table public.commercial_product_types (
  id uuid primary key default gen_random_uuid(),
  name text not null check (btrim(name) <> ''),
  name_key text generated always as (regexp_replace(upper(btrim(name)), '[[:space:]]+', ' ', 'g')) stored unique,
  evidence_json jsonb not null check (jsonb_typeof(evidence_json) = 'object' and evidence_json <> '{}'),
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default now()
);
create table public.commercial_models (
  id uuid primary key default gen_random_uuid(),
  name text not null check (btrim(name) <> ''),
  name_key text generated always as (regexp_replace(upper(btrim(name)), '[[:space:]]+', ' ', 'g')) stored unique,
  evidence_json jsonb not null check (jsonb_typeof(evidence_json) = 'object' and evidence_json <> '{}'),
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default now()
);
create table public.commercial_colors (
  id uuid primary key default gen_random_uuid(),
  name text not null check (btrim(name) <> ''),
  name_key text generated always as (regexp_replace(upper(btrim(name)), '[[:space:]]+', ' ', 'g')) stored unique,
  evidence_json jsonb not null check (jsonb_typeof(evidence_json) = 'object' and evidence_json <> '{}'),
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default now()
);
create table public.commercial_sizes (
  id uuid primary key default gen_random_uuid(),
  name text not null check (btrim(name) <> ''),
  name_key text generated always as (regexp_replace(upper(btrim(name)), '[[:space:]]+', ' ', 'g')) stored unique,
  kind text not null check (kind in ('PRESENTATION','EXPLICIT_DIMENSIONS')),
  -- Measurements remain evidence, not guessed dimensions or SKU conversions.
  evidence_json jsonb not null check (jsonb_typeof(evidence_json) = 'object' and evidence_json <> '{}'),
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default now()
);
create table public.product_commercial_classifications (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  category_id uuid,
  product_id uuid not null,
  product_type_id uuid references public.commercial_product_types(id) on delete restrict,
  model_id uuid references public.commercial_models(id) on delete restrict,
  color_id uuid references public.commercial_colors(id) on delete restrict,
  size_id uuid references public.commercial_sizes(id) on delete restrict,
  model_state text not null check (model_state in ('KNOWN','UNKNOWN')),
  color_state text not null check (color_state in ('KNOWN','UNKNOWN','NOT_APPLICABLE')),
  size_state text not null check (size_state in ('KNOWN','UNKNOWN','NOT_APPLICABLE')),
  product_type_state text not null check (product_type_state in ('KNOWN','UNKNOWN')),
  original_model text, original_color text, original_size text, original_product_type text,
  product_type_evidence_json jsonb not null check (jsonb_typeof(product_type_evidence_json) = 'object' and product_type_evidence_json <> '{}'),
  classification_status text not null default 'PENDING_CLASSIFICATION' check (classification_status in ('PROPOSED','APPROVED','PENDING_CLASSIFICATION','REJECTED')),
  classification_source text not null check (btrim(classification_source) <> ''),
  classification_version integer not null check (classification_version > 0),
  rules_version text not null check (btrim(rules_version) <> ''),
  evidence_json jsonb not null check (jsonb_typeof(evidence_json) = 'object' and evidence_json <> '{}'),
  explanation text not null check (btrim(explanation) <> ''),
  product_snapshot jsonb not null check (jsonb_typeof(product_snapshot) = 'object'),
  mapping_packet_hash char(64) not null check (mapping_packet_hash ~ '^[0-9a-f]{64}$'),
  approval_reference text,
  approved_by uuid references public.profiles(id) on delete restrict,
  approved_at timestamptz,
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default now(),
  supersedes_id uuid,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (category_id,chain_id) references public.categories(id,chain_id) on delete restrict,
  unique (product_id,chain_id,classification_version),
  unique (id,product_id,chain_id),
  foreign key (supersedes_id,product_id,chain_id)
    references public.product_commercial_classifications(id,product_id,chain_id) on delete restrict,
  check ((model_state='KNOWN' and model_id is not null and nullif(btrim(original_model),'') is not null)
      or (model_state='UNKNOWN' and model_id is null and original_model is null)),
  check ((color_state='KNOWN' and color_id is not null and nullif(btrim(original_color),'') is not null)
      or (color_state in ('UNKNOWN','NOT_APPLICABLE') and color_id is null and original_color is null)),
  check ((size_state='KNOWN' and size_id is not null and nullif(btrim(original_size),'') is not null)
      or (size_state in ('UNKNOWN','NOT_APPLICABLE') and size_id is null and original_size is null)),
  check ((product_type_state='KNOWN' and product_type_id is not null and nullif(btrim(original_product_type),'') is not null)
      or (product_type_state='UNKNOWN' and product_type_id is null and original_product_type is null)),
  check ((classification_status='APPROVED' and approved_by is not null and approved_at is not null
       and nullif(btrim(approval_reference),'') is not null and model_state='KNOWN' and product_type_state='KNOWN'
       and color_state <> 'UNKNOWN' and size_state <> 'UNKNOWN')
      or (classification_status <> 'APPROVED' and approved_by is null and approved_at is null and approval_reference is null))
);
create index commercial_classifications_scope_status
  on public.product_commercial_classifications(chain_id,classification_status,product_id);
create index commercial_classifications_model on public.product_commercial_classifications(model_id,chain_id);
create index commercial_classifications_color on public.product_commercial_classifications(color_id,chain_id);
create index commercial_classifications_size on public.product_commercial_classifications(size_id,chain_id);
create index commercial_classifications_product_type on public.product_commercial_classifications(product_type_id,chain_id);

create function private.commercial_classification_guard() returns trigger
language plpgsql set search_path = '' as $$
declare p public.products%rowtype; previous public.product_commercial_classifications%rowtype;
begin
  -- Serializes revisions of one product without modifying its identity or data.
  select * into p from public.products where id=new.product_id for update;
  if not found or p.chain_id <> new.chain_id or p.category_id is distinct from new.category_id then
    raise exception 'classification must match product/category/chain' using errcode='23514';
  end if;
  if new.product_snapshot is distinct from to_jsonb(p) then
    raise exception 'catalog changed since proposal; regenerate and reapprove' using errcode='23514';
  end if;
  select * into previous from public.product_commercial_classifications
    where product_id=new.product_id and chain_id=new.chain_id order by classification_version desc limit 1;
  if new.classification_version <> coalesce(previous.classification_version,0)+1
      or new.supersedes_id is distinct from previous.id then
    raise exception 'classification revisions must be consecutive with exact predecessor' using errcode='23514';
  end if;
  if not exists(select 1 from public.profiles where id=new.created_by and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'classification requires active administrative validation' using errcode='23514';
  end if;
  if new.classification_status='APPROVED' then
    if not exists(select 1 from public.profiles where id=new.approved_by and status='ACTIVE' and global_role='ADMIN')
        or new.approved_at > statement_timestamp() then
      raise exception 'approval requires active administrator and nonfuture timestamp' using errcode='23514';
    end if;
    if (new.color_state='NOT_APPLICABLE' and nullif(btrim(new.evidence_json->>'color_not_applicable'),'') is null)
        or (new.size_state='NOT_APPLICABLE' and nullif(btrim(new.evidence_json->>'size_not_applicable'),'') is null) then
      raise exception 'not applicable requires explicit commercial evidence' using errcode='23514';
    end if;
  end if;
  new.created_at := statement_timestamp();
  return new;
end $$;
revoke all on function private.commercial_classification_guard() from public,anon,authenticated,service_role;
create trigger commercial_classification_guard before insert on public.product_commercial_classifications
  for each row execute function private.commercial_classification_guard();

create function private.commercial_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'commercial records are append-only; create a reviewed revision' using errcode='23514';
end $$;
revoke all on function private.commercial_append_only() from public,anon,authenticated,service_role;
do $$ declare t text; begin
  foreach t in array array['commercial_product_types','commercial_models','commercial_colors','commercial_sizes','product_commercial_classifications'] loop
    execute format('create trigger commercial_append_only before update or delete on public.%I for each row execute function private.commercial_append_only()', t);
    execute format('alter table public.%I enable row level security', t);
    -- No browser or backend writer is activated by schema approval.
    execute format('revoke all on table public.%I from public,anon,authenticated,service_role', t);
  end loop;
end $$;
-- Shared dictionary evidence can mention multiple chains. Do not expose it to
-- chain-scoped browser readers even when the dictionary name is shared.
grant select (id,name,name_key,created_at) on public.commercial_models to authenticated;
grant select (id,name,name_key,created_at) on public.commercial_product_types to authenticated;
grant select (id,name,name_key,created_at) on public.commercial_colors to authenticated;
grant select (id,name,name_key,kind,created_at) on public.commercial_sizes to authenticated;
grant select on public.product_commercial_classifications to authenticated;
create policy commercial_classifications_read on public.product_commercial_classifications
  for select to authenticated using (private.can_chain(chain_id,'view'));
create policy commercial_models_read on public.commercial_models for select to authenticated using (
  (select private.is_active_admin()) or exists(select 1 from public.product_commercial_classifications c
    where c.model_id=commercial_models.id and private.can_chain(c.chain_id,'view')));
create policy commercial_colors_read on public.commercial_colors for select to authenticated using (
  (select private.is_active_admin()) or exists(select 1 from public.product_commercial_classifications c
    where c.color_id=commercial_colors.id and private.can_chain(c.chain_id,'view')));
create policy commercial_sizes_read on public.commercial_sizes for select to authenticated using (
  (select private.is_active_admin()) or exists(select 1 from public.product_commercial_classifications c
    where c.size_id=commercial_sizes.id and private.can_chain(c.chain_id,'view')));
create policy commercial_product_types_read on public.commercial_product_types for select to authenticated using (
  (select private.is_active_admin()) or exists(select 1 from public.product_commercial_classifications c
    where c.product_type_id=commercial_product_types.id and private.can_chain(c.chain_id,'view')));

-- Includes future, unmapped products without fabricating classification rows.
create view public.product_commercial_classifications_current with (security_invoker=true) as
select p.id as product_id,p.chain_id,p.category_id,p.product_code,p.description,
  c.id as classification_id,c.model_id,c.color_id,c.size_id,c.product_type_id,
  case when c.id is null or c.product_snapshot is distinct from to_jsonb(p) then 'PENDING_CLASSIFICATION'
    else c.classification_status end as classification_status,
  case when c.id is null then 'No reviewed commercial classification exists for this product'
    when c.product_snapshot is distinct from to_jsonb(p) then 'Product changed; regenerate and reapprove classification'
    else c.explanation end as explanation,
  c.classification_status as stored_classification_status,
  c.id is not null and c.product_snapshot is distinct from to_jsonb(p) as stale_snapshot,
  coalesce(c.model_state,'UNKNOWN') as model_state,coalesce(c.color_state,'UNKNOWN') as color_state,
  coalesce(c.size_state,'UNKNOWN') as size_state,c.original_model,c.original_color,c.original_size,
  coalesce(c.product_type_state,'UNKNOWN') as product_type_state,c.original_product_type,
  c.product_type_evidence_json,
  c.classification_source,c.classification_version,c.rules_version,c.approved_by,c.approved_at,c.created_at
from public.products p left join lateral (
  select * from public.product_commercial_classifications r
  where r.product_id=p.id and r.chain_id=p.chain_id order by r.classification_version desc limit 1
) c on true;
revoke all on public.product_commercial_classifications_current from public,anon,authenticated,service_role;
grant select on public.product_commercial_classifications_current to authenticated;
