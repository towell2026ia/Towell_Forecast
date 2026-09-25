-- PRD 09.2B candidate. Do not push until explicit user approval after the gate.
-- This schema is deliberately fail-closed: 09.2C will add reviewed RLS policies.

create table public.chains (
  id uuid primary key default gen_random_uuid(),
  code text not null unique check (code ~ '^[A-Z0-9][A-Z0-9_-]*$'),
  name text not null check (btrim(name) <> ''),
  status text not null default 'ACTIVE' check (status in ('ACTIVE','INACTIVE')),
  timezone text not null default 'America/Mexico_City',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.profiles (
  id uuid primary key references auth.users(id) on delete restrict,
  full_name text not null default '',
  status text not null default 'ACTIVE' check (status in ('ACTIVE','INACTIVE')),
  global_role text not null default 'VIEWER' check (global_role in ('ADMIN','EDITOR','VIEWER')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_chain_access (
  user_id uuid not null references public.profiles(id) on delete restrict,
  chain_id uuid not null references public.chains(id) on delete restrict,
  can_view boolean not null default false,
  can_edit boolean not null default false,
  can_import boolean not null default false,
  can_run_forecast boolean not null default false,
  can_approve boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (user_id,chain_id)
);

create table public.source_evidence (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid references public.chains(id) on delete restrict,
  legacy_id text unique,
  evidence_level text not null check (evidence_level in ('E1','E2','E3','UNKNOWN')),
  evidence_type text not null,
  evidence_date timestamptz,
  source_name text not null,
  source_hash char(64) not null check (source_hash ~ '^[0-9a-f]{64}$'),
  storage_path text,
  description text not null default '',
  validated_by uuid references auth.users(id) on delete restrict,
  validated_at timestamptz,
  created_at timestamptz not null default now(),
  constraint source_evidence_temporal_valid check (
    (evidence_level='UNKNOWN' and evidence_date is null and validated_at is null)
    or (evidence_level in ('E1','E2','E3') and evidence_date is not null and validated_by is not null and validated_at is not null and evidence_date <= validated_at and storage_path is not null and btrim(storage_path) <> '')
  ),
  unique (id,chain_id)
);

create table public.import_profiles (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  name text not null check (btrim(name) <> ''),
  status text not null default 'ACTIVE' check (status in ('ACTIVE','INACTIVE')),
  created_at timestamptz not null default now(),
  unique (chain_id,name), unique (id,chain_id)
);

create table public.import_profile_versions (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null,
  chain_id uuid not null,
  version integer not null check (version > 0),
  mapping_json jsonb not null check (jsonb_typeof(mapping_json)='object'),
  required_columns jsonb not null check (jsonb_typeof(required_columns)='array'),
  created_at timestamptz not null default now(),
  created_by uuid not null references auth.users(id) on delete restrict,
  foreign key (profile_id,chain_id) references public.import_profiles(id,chain_id) on delete restrict,
  unique (profile_id,version), unique (id,chain_id)
);

create table public.import_batches (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  profile_version_id uuid not null,
  filename text not null check (btrim(filename) <> ''),
  sha256 char(64) not null check (sha256 ~ '^[0-9a-f]{64}$'),
  storage_path text,
  period date not null check (extract(day from period)=1),
  uploaded_at timestamptz not null default now(),
  uploaded_by uuid not null references auth.users(id) on delete restrict,
  availability_source text not null default 'SYSTEM_INGESTION' check (availability_source='SYSTEM_INGESTION'),
  evidence_id uuid references public.source_evidence(id) on delete restrict,
  row_count integer not null default 0 check (row_count >= 0),
  valid_rows integer not null default 0 check (valid_rows >= 0),
  rejected_rows integer not null default 0 check (rejected_rows >= 0),
  status text not null default 'UPLOADED' check (status in ('UPLOADED','VALIDATING','VALIDATED','CONFIRMED','IMPORTED','REJECTED','FAILED')),
  created_at timestamptz not null default now(),
  constraint import_batch_counts check (valid_rows + rejected_rows <= row_count),
  foreign key (profile_version_id,chain_id) references public.import_profile_versions(id,chain_id) on delete restrict,
  unique (chain_id,sha256), unique (id,chain_id)
);

create table public.categories (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  code text not null check (btrim(code) <> ''),
  name text not null check (btrim(name) <> ''),
  status text not null default 'ACTIVE' check (status in ('ACTIVE','INACTIVE')),
  created_at timestamptz not null default now(),
  unique (chain_id,code), unique (id,chain_id)
);

create table public.products (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  category_id uuid,
  product_code text not null check (btrim(product_code) <> ''),
  variant_code text check (variant_code is null or btrim(variant_code) <> ''),
  description text not null,
  variant_description text,
  status text not null default 'NEW' check (status in ('NEW','ACTIVE','INACTIVE')),
  first_seen_period date not null check (extract(day from first_seen_period)=1),
  last_seen_period date not null check (extract(day from last_seen_period)=1),
  created_from_batch_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint products_seen_order check (last_seen_period >= first_seen_period),
  foreign key (category_id,chain_id) references public.categories(id,chain_id) on delete restrict,
  foreign key (created_from_batch_id,chain_id) references public.import_batches(id,chain_id) on delete restrict,
  unique (id,chain_id)
);
create unique index products_natural_identity on public.products(chain_id,product_code,coalesce(variant_code,''));
create index products_chain_code on public.products(chain_id,product_code);
create index products_chain_category on public.products(chain_id,category_id);

create table public.monthly_observations (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  product_id uuid not null,
  period date not null check (extract(day from period)=1),
  metric_code text not null check (metric_code in ('SALES','ORDER','DELIVERY')),
  value numeric(18,3) not null check (value >= 0),
  version_no integer not null check (version_no > 0),
  available_at timestamptz,
  availability_source text not null check (availability_source in ('UNKNOWN','SYSTEM_INGESTION','E1','E2','E3')),
  availability_evidence_id uuid references public.source_evidence(id) on delete restrict,
  source_batch_id uuid,
  created_at timestamptz not null default now(),
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (source_batch_id,chain_id) references public.import_batches(id,chain_id) on delete restrict,
  constraint observation_availability_shape check (
    (availability_source='UNKNOWN' and available_at is null)
    or (availability_source='SYSTEM_INGESTION' and available_at is not null and source_batch_id is not null)
    or (availability_source in ('E1','E2','E3') and available_at is not null and availability_evidence_id is not null)
  ),
  unique (chain_id,product_id,period,metric_code,version_no), unique (id,chain_id)
);
create index observations_chain_period on public.monthly_observations(chain_id,period);
create index observations_product_period on public.monthly_observations(product_id,period);
create index observations_product_metric_period on public.monthly_observations(product_id,metric_code,period);
create index observations_available_at on public.monthly_observations(chain_id,available_at) where available_at is not null;

create table public.customer_forecast_versions (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  issue_period date not null check (extract(day from issue_period)=1),
  received_at timestamptz not null,
  version_no integer not null check (version_no > 0),
  status text not null default 'DRAFT' check (status in ('DRAFT','VALIDATED','FROZEN','SUPERSEDED')),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  validated_by uuid references auth.users(id) on delete restrict,
  validated_at timestamptz,
  frozen_at timestamptz,
  constraint customer_forecast_frozen_shape check ((status='FROZEN')=(frozen_at is not null)),
  unique (chain_id,issue_period,version_no), unique (id,chain_id)
);
create index customer_forecast_versions_chain_issue on public.customer_forecast_versions(chain_id,issue_period);

create table public.customer_forecast_rows (
  id uuid primary key default gen_random_uuid(),
  forecast_version_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  target_period date not null check (extract(day from target_period)=1),
  value numeric(18,3) not null check (value >= 0),
  created_at timestamptz not null default now(),
  foreign key (forecast_version_id,chain_id) references public.customer_forecast_versions(id,chain_id) on delete restrict,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  unique (forecast_version_id,product_id,target_period)
);
create index customer_forecast_rows_product_target on public.customer_forecast_rows(product_id,target_period);

-- All tables are inaccessible to anon/authenticated until 09.2C policies are approved.
do $$ declare t text; begin
  foreach t in array array['chains','profiles','user_chain_access','source_evidence','import_profiles','import_profile_versions','import_batches','categories','products','monthly_observations','customer_forecast_versions','customer_forecast_rows'] loop
    execute format('alter table public.%I enable row level security',t);
  end loop;
end $$;
