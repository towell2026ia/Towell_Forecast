-- PRD 09.2B candidate. Depends on 202609250001 and 202609250002.

create table public.forecast_decisions (
  id uuid primary key default gen_random_uuid(),
  vintage_id uuid not null,
  chain_id uuid not null,
  product_id uuid not null,
  target_period date not null check (extract(day from target_period)=1),
  version_no integer not null check (version_no > 0),
  forecast_towell numeric(18,3) not null check (forecast_towell >= 0),
  adjusted_value numeric(18,3) check (adjusted_value >= 0),
  approved_value numeric(18,3) check (approved_value >= 0),
  reason_code text not null,
  comment text,
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  approved_by uuid references auth.users(id) on delete restrict,
  approved_at timestamptz,
  foreign key (vintage_id,chain_id) references public.forecast_vintages(id,chain_id) on delete restrict,
  foreign key (product_id,chain_id) references public.products(id,chain_id) on delete restrict,
  foreign key (vintage_id,product_id,target_period) references public.forecast_horizons(vintage_id,product_id,target_period) on delete restrict,
  constraint decision_approval_shape check ((approved_by is null)=(approved_at is null)),
  unique (vintage_id,product_id,target_period,version_no)
);

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid references public.chains(id) on delete restrict,
  job_type text not null,
  run_id uuid,
  status text not null,
  requested_by uuid references auth.users(id) on delete restrict,
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  metadata_json jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata_json)='object'),
  foreign key (run_id,chain_id) references public.forecast_runs(id,chain_id) on delete restrict,
  constraint job_run_chain check (run_id is null or chain_id is not null)
);
create index jobs_chain_requested on public.jobs(chain_id,requested_at desc);

create table public.run_logs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references public.jobs(id) on delete restrict,
  run_id uuid references public.forecast_runs(id) on delete restrict,
  step text not null,
  status text not null,
  message text not null,
  metadata_json jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata_json)='object'),
  created_at timestamptz not null default now(),
  constraint run_log_parent check (job_id is not null or run_id is not null)
);
create index run_logs_job_created on public.run_logs(job_id,created_at);
create index run_logs_run_created on public.run_logs(run_id,created_at);

create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  chain_id uuid references public.chains(id) on delete restrict,
  actor_id uuid references auth.users(id) on delete restrict,
  action text not null,
  entity_type text not null,
  entity_id uuid not null,
  old_data jsonb,
  new_data jsonb,
  request_id text,
  created_at timestamptz not null default now()
);
create index audit_log_chain_created on public.audit_log(chain_id,created_at desc);

create table public.forecast_run_inputs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  chain_id uuid not null,
  monthly_observation_id uuid,
  customer_forecast_version_id uuid,
  source_evidence_id uuid references public.source_evidence(id) on delete restrict,
  data_snapshot_hash char(64) not null check (data_snapshot_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  foreign key (run_id,chain_id) references public.forecast_runs(id,chain_id) on delete restrict,
  foreign key (monthly_observation_id,chain_id) references public.monthly_observations(id,chain_id) on delete restrict,
  foreign key (customer_forecast_version_id,chain_id) references public.customer_forecast_versions(id,chain_id) on delete restrict,
  constraint run_input_one_source check ((monthly_observation_id is not null)<>(customer_forecast_version_id is not null)),
  unique (run_id,monthly_observation_id), unique (run_id,customer_forecast_version_id)
);
create index forecast_run_inputs_run on public.forecast_run_inputs(run_id);

create table public.legacy_identity_map (
  id uuid primary key default gen_random_uuid(),
  entity_type text not null,
  legacy_id text not null,
  new_uuid uuid not null,
  source_hash char(64) not null check (source_hash ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  unique (entity_type,legacy_id), unique (entity_type,new_uuid,legacy_id)
);

create or replace view public.monthly_observations_current with (security_invoker=true) as
select distinct on (chain_id,product_id,period,metric_code) *
from public.monthly_observations
where available_at is not null
order by chain_id,product_id,period,metric_code,version_no desc;
comment on view public.monthly_observations_current is 'Latest known version only. Historical replay MUST additionally filter available_at <= execution cutoff before choosing a version.';

do $$ declare t text; begin
  foreach t in array array['forecast_decisions','jobs','run_logs','audit_log','forecast_run_inputs','legacy_identity_map'] loop
    execute format('alter table public.%I enable row level security',t);
  end loop;
end $$;
