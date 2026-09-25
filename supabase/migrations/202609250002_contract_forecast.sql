-- PRD 09.2B candidate. Depends on 202609250001_contract_core.sql.

create table public.research_snapshots (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  cutoff_at timestamptz not null,
  provider text not null,
  status text not null check (status in ('DRAFT','VALIDATED','FROZEN','FAILED')),
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  summary_json jsonb not null default '{}'::jsonb check (jsonb_typeof(summary_json)='object'),
  created_at timestamptz not null default now(),
  frozen_at timestamptz,
  constraint research_frozen_shape check ((status='FROZEN')=(frozen_at is not null)),
  unique (id,chain_id)
);

create table public.research_snapshot_sources (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid not null,
  chain_id uuid not null,
  source_url text not null,
  source_hash char(64) not null check (source_hash ~ '^[0-9a-f]{64}$'),
  published_at timestamptz not null,
  captured_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (snapshot_id,chain_id) references public.research_snapshots(id,chain_id) on delete restrict,
  unique (snapshot_id,source_hash)
);

create table public.forecast_runs (
  id uuid primary key default gen_random_uuid(),
  run_code text not null unique,
  chain_id uuid not null references public.chains(id) on delete restrict,
  objective text not null check (objective in ('Venta','Pedido','Entrega')),
  issue_period date not null check (extract(day from issue_period)=1),
  cutoff_at timestamptz not null,
  status text not null check (status in ('QUEUED','PREPARING','RESEARCH','STATISTICAL','ML','ENSEMBLE','CERTIFICATION','SAVING','COMPLETED','FAILED')),
  data_snapshot_hash char(64) not null check (data_snapshot_hash ~ '^[0-9a-f]{64}$'),
  research_snapshot_id uuid,
  started_at timestamptz,
  finished_at timestamptz,
  actor_id uuid references auth.users(id) on delete restrict,
  engine_version text not null,
  git_sha text not null,
  error_code text,
  created_at timestamptz not null default now(),
  foreign key (research_snapshot_id,chain_id) references public.research_snapshots(id,chain_id) on delete restrict,
  constraint run_time_order check (finished_at is null or (started_at is not null and finished_at >= started_at)),
  unique (id,chain_id)
);
create index forecast_runs_chain_issue on public.forecast_runs(chain_id,issue_period);

create table public.model_versions (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  chain_id uuid not null,
  model_family text not null check (model_family in ('STATISTICAL','ML','ENSEMBLE')),
  algorithm text not null,
  model_version text not null,
  parameters_json jsonb not null default '{}'::jsonb check (jsonb_typeof(parameters_json)='object'),
  training_start date, training_end date,
  validation_start date, validation_end date,
  certification_start date, certification_end date,
  validation_wape numeric check (validation_wape >= 0),
  certified_wape numeric check (certified_wape >= 0),
  certified_bias numeric,
  certification_status text not null check (certification_status in ('CERTIFIED','PROVISIONAL','INSUFFICIENT')),
  candidate_status text check (candidate_status in ('CHALLENGER','APPROVED','REJECTED','RETIRED')),
  artifact_path text,
  created_at timestamptz not null default now(),
  foreign key (run_id,chain_id) references public.forecast_runs(id,chain_id) on delete restrict,
  constraint model_training_range check (training_start is null or (training_end is not null and training_start <= training_end)),
  constraint model_validation_range check (validation_start is null or (validation_end is not null and validation_start <= validation_end)),
  constraint model_certification_range check (certification_start is null or (certification_end is not null and certification_start <= certification_end)),
  constraint model_ranges_disjoint check (
    (training_end is null or validation_start is null or training_end < validation_start)
    and (validation_end is null or certification_start is null or validation_end < certification_start)
  ),
  unique (run_id,model_family,algorithm,model_version), unique (id,chain_id)
);

create table public.forecast_vintages (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null unique,
  chain_id uuid not null,
  objective text not null check (objective in ('Venta','Pedido','Entrega')),
  issue_period date not null check (extract(day from issue_period)=1),
  cutoff_at timestamptz not null,
  forecast_version text not null,
  champion_model_version_id uuid,
  research_snapshot_id uuid,
  certification_status text not null check (certification_status in ('CERTIFIED','PROVISIONAL','INSUFFICIENT')),
  legacy_id text unique,
  frozen_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (run_id,chain_id) references public.forecast_runs(id,chain_id) on delete restrict,
  foreign key (champion_model_version_id,chain_id) references public.model_versions(id,chain_id) on delete restrict,
  foreign key (research_snapshot_id,chain_id) references public.research_snapshots(id,chain_id) on delete restrict,
  unique (id,chain_id)
);
create index forecast_vintages_chain_issue on public.forecast_vintages(chain_id,issue_period);

create table public.forecast_horizons (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  category_id uuid,
  target_period date not null check (extract(day from target_period)=1),
  horizon integer not null check (horizon between 1 and 12),
  statistical_value numeric(18,3) check (statistical_value >= 0),
  ml_value numeric(18,3) check (ml_value >= 0),
  ensemble_value numeric(18,3) check (ensemble_value >= 0),
  forecast_towell numeric(18,3) not null check (forecast_towell >= 0),
  operational_value numeric(18,3) check (operational_value >= 0),
  model_strategy text not null,
  confidence text,
  p10 numeric(18,3), p50 numeric(18,3), p90 numeric(18,3), p95 numeric(18,3),
  band_basis text, band_observations integer check (band_observations >= 0),
  forecast_status text not null,
  certification_status text not null check (certification_status in ('CERTIFIED','PROVISIONAL','INSUFFICIENT')),
  created_at timestamptz not null default now(),
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (category_id,chain_id) references public.categories(id,chain_id) on delete restrict,
  constraint horizon_bands check (
    (p10 is null and p50 is null and p90 is null and p95 is null and band_basis is null and band_observations is null)
    or (p10 >= 0 and p10 <= p50 and p50 <= p90 and p90 <= p95 and band_basis is not null and band_observations is not null and band_observations > 0)
  ),
  unique (vintage_id,product_id,horizon), unique (vintage_id,product_id,target_period),
  unique (id,chain_id)
);
create index forecast_horizons_product_target on public.forecast_horizons(product_id,target_period);

create table public.forecast_aggregates (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  level text not null check (level in ('CATEGORY','CHAIN')),
  category_id uuid,
  target_period date not null check (extract(day from target_period)=1),
  horizon integer not null check (horizon between 1 and 12),
  forecast_towell numeric(18,3) not null check (forecast_towell >= 0),
  p10 numeric(18,3), p50 numeric(18,3), p90 numeric(18,3), p95 numeric(18,3),
  created_at timestamptz not null default now(),
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  foreign key (category_id,chain_id) references public.categories(id,chain_id) on delete restrict,
  constraint aggregate_level_category check ((level='CATEGORY' and category_id is not null) or (level='CHAIN' and category_id is null)),
  constraint aggregate_bands check ((p10 is null and p50 is null and p90 is null and p95 is null) or (p10 >= 0 and p10 <= p50 and p50 <= p90 and p90 <= p95))
);
create unique index aggregates_category_unique on public.forecast_aggregates(vintage_id,category_id,horizon) where level='CATEGORY';
create unique index aggregates_chain_unique on public.forecast_aggregates(vintage_id,horizon) where level='CHAIN';

create table public.champion_registry (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid not null references public.chains(id) on delete restrict,
  objective text not null check (objective in ('Venta','Pedido','Entrega')),
  scope_type text not null check (scope_type in ('CHAIN','CATEGORY','PRODUCT')),
  scope_id uuid not null,
  model_version_id uuid not null,
  strategy text not null,
  published_at timestamptz not null,
  published_by uuid not null references auth.users(id) on delete restrict,
  valid_from timestamptz not null,
  valid_to timestamptz,
  status text not null check (status in ('ACTIVE','RETIRED')),
  foreign key (model_version_id,chain_id) references public.model_versions(id,chain_id) on delete restrict,
  constraint champion_validity check (valid_to is null or valid_to >= valid_from),
  constraint champion_active_window check ((status='ACTIVE')=(valid_to is null))
);
create unique index champion_one_active_scope on public.champion_registry(chain_id,objective,scope_type,scope_id) where status='ACTIVE';

create table public.actual_evaluations (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  horizon integer not null check (horizon between 1 and 12),
  target_period date not null check (extract(day from target_period)=1),
  forecast_value numeric(18,3) not null check (forecast_value >= 0),
  actual_value numeric(18,3) not null check (actual_value >= 0),
  actual_observation_id uuid,
  evaluated_at timestamptz not null default now(),
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (actual_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  foreign key (vintage_id,product_id,horizon) references public.forecast_horizons(vintage_id,product_id,horizon) on delete restrict,
  unique (vintage_id,product_id,horizon,actual_observation_id)
);

create table public.performance_metrics (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  product_id uuid,
  horizon integer check (horizon between 1 and 12),
  metric text not null,
  phase text not null check (phase in ('VALIDATION','CERTIFICATION','LIVE','OPERATIONAL')),
  value numeric,
  period date not null check (extract(day from period)=1),
  created_at timestamptz not null default now(),
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  constraint metric_value_domain check (value is null or metric not in ('WAPE','FILL_RATE','MAE','RMSE') or value >= 0)
);
create index performance_metrics_chain_period on public.performance_metrics(chain_id,period);
create index performance_metrics_product_period on public.performance_metrics(product_id,period);

do $$ declare t text; begin
  foreach t in array array['research_snapshots','research_snapshot_sources','forecast_runs','model_versions','forecast_vintages','forecast_horizons','forecast_aggregates','champion_registry','actual_evaluations','performance_metrics'] loop
    execute format('alter table public.%I enable row level security',t);
  end loop;
end $$;
