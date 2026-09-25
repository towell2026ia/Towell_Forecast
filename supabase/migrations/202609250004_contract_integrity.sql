-- PRD 09.2B candidate. Depends on 202609250001..003.

create function public.contract_insert_only() returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name using errcode='23514';
end $$;

create trigger source_evidence_insert_only before update or delete on public.source_evidence for each row execute function public.contract_insert_only();
create trigger import_profile_versions_insert_only before update or delete on public.import_profile_versions for each row execute function public.contract_insert_only();
create trigger observations_insert_only before update or delete on public.monthly_observations for each row execute function public.contract_insert_only();
create trigger decisions_insert_only before update or delete on public.forecast_decisions for each row execute function public.contract_insert_only();
create trigger run_inputs_insert_only before update or delete on public.forecast_run_inputs for each row execute function public.contract_insert_only();
create trigger run_logs_insert_only before update or delete on public.run_logs for each row execute function public.contract_insert_only();
create trigger audit_log_insert_only before update or delete on public.audit_log for each row execute function public.contract_insert_only();
create trigger legacy_identity_map_insert_only before update or delete on public.legacy_identity_map for each row execute function public.contract_insert_only();
create trigger actual_evaluations_insert_only before update or delete on public.actual_evaluations for each row execute function public.contract_insert_only();
create trigger performance_metrics_insert_only before update or delete on public.performance_metrics for each row execute function public.contract_insert_only();

create function public.contract_evidence_chain(p_evidence_id uuid, p_chain_id uuid) returns void language plpgsql as $$
declare e public.source_evidence%rowtype;
begin
  if p_evidence_id is null then return; end if;
  select * into e from public.source_evidence where id=p_evidence_id;
  if not found or (e.chain_id is not null and e.chain_id <> p_chain_id) then
    raise exception 'evidence does not belong to chain' using errcode='23503';
  end if;
end $$;

create function public.contract_batch_guard() returns trigger language plpgsql as $$
begin
  perform public.contract_evidence_chain(new.evidence_id,new.chain_id);
  if tg_op='INSERT' and new.status<>'UPLOADED' then
    raise exception 'new batch must start UPLOADED' using errcode='23514';
  end if;
  if new.status in ('VALIDATED','CONFIRMED','IMPORTED') and (new.row_count=0 or new.valid_rows+new.rejected_rows<>new.row_count) then
    raise exception 'validated batch requires reconciled nonzero counts' using errcode='23514';
  end if;
  if tg_op='UPDATE' then
    if old.status in ('IMPORTED','REJECTED','FAILED') then
      raise exception 'terminal import batch is immutable' using errcode='23514';
    end if;
    if row(old.id,old.chain_id,old.profile_version_id,old.filename,old.sha256,old.period,old.uploaded_at,old.uploaded_by,old.availability_source,old.evidence_id)
       is distinct from row(new.id,new.chain_id,new.profile_version_id,new.filename,new.sha256,new.period,new.uploaded_at,new.uploaded_by,new.availability_source,new.evidence_id) then
      raise exception 'import batch identity and ingestion time are immutable' using errcode='23514';
    end if;
    if old.status<>new.status and not (
      (old.status='UPLOADED' and new.status in ('VALIDATING','REJECTED','FAILED')) or
      (old.status='VALIDATING' and new.status in ('VALIDATED','REJECTED','FAILED')) or
      (old.status='VALIDATED' and new.status in ('CONFIRMED','REJECTED','FAILED')) or
      (old.status='CONFIRMED' and new.status in ('IMPORTED','FAILED'))
    ) then raise exception 'invalid import batch transition' using errcode='23514'; end if;
  end if;
  return new;
end $$;
create trigger import_batch_guard before insert or update on public.import_batches for each row execute function public.contract_batch_guard();
create trigger import_batch_no_delete before delete on public.import_batches for each row execute function public.contract_insert_only();

create function public.contract_observation_availability() returns trigger language plpgsql as $$
declare b public.import_batches%rowtype; e public.source_evidence%rowtype;
begin
  perform public.contract_evidence_chain(new.availability_evidence_id,new.chain_id);
  if new.availability_source='SYSTEM_INGESTION' then
    select * into b from public.import_batches where id=new.source_batch_id;
    if not found or b.chain_id<>new.chain_id or new.available_at<>b.uploaded_at or b.status not in ('CONFIRMED','IMPORTED') then
      raise exception 'system availability must equal confirmed batch uploaded_at' using errcode='23514';
    end if;
  elsif new.availability_source in ('E1','E2','E3') then
    select * into e from public.source_evidence where id=new.availability_evidence_id;
    if not found or e.evidence_level<>new.availability_source or new.available_at<>e.evidence_date then
      raise exception 'availability requires matching verified evidence date' using errcode='23514';
    end if;
  end if;
  return new;
end $$;
create trigger observation_availability before insert on public.monthly_observations for each row execute function public.contract_observation_availability();

create function public.contract_customer_forecast_guard() returns trigger language plpgsql as $$
declare s text;
begin
  if tg_table_name='customer_forecast_versions' then
    if tg_op='DELETE' or old.status in ('FROZEN','SUPERSEDED') then
      raise exception 'frozen/superseded customer forecast is immutable' using errcode='23514';
    end if;
    if row(old.id,old.chain_id,old.issue_period,old.received_at,old.version_no,old.created_by)
       is distinct from row(new.id,new.chain_id,new.issue_period,new.received_at,new.version_no,new.created_by) then
      raise exception 'customer forecast version identity is immutable' using errcode='23514';
    end if;
    if old.status<>new.status and not ((old.status='DRAFT' and new.status='VALIDATED') or (old.status='VALIDATED' and new.status in ('FROZEN','SUPERSEDED'))) then
      raise exception 'invalid customer forecast transition' using errcode='23514';
    end if;
  else
    select status into s from public.customer_forecast_versions where id=coalesce(new.forecast_version_id,old.forecast_version_id);
    if s in ('FROZEN','SUPERSEDED') then raise exception 'customer forecast rows are frozen' using errcode='23514'; end if;
    if tg_op<>'DELETE' and new.target_period<(select issue_period from public.customer_forecast_versions where id=new.forecast_version_id) then
      raise exception 'customer forecast target predates issue period' using errcode='23514';
    end if;
  end if;
  return coalesce(new,old);
end $$;
create trigger customer_forecast_header_guard before update or delete on public.customer_forecast_versions for each row execute function public.contract_customer_forecast_guard();
create trigger customer_forecast_rows_guard before insert or update or delete on public.customer_forecast_rows for each row execute function public.contract_customer_forecast_guard();

create function public.contract_horizon_guard() returns trigger language plpgsql as $$
declare v public.forecast_vintages%rowtype; p public.products%rowtype;
begin
  select * into v from public.forecast_vintages where id=coalesce(new.vintage_id,old.vintage_id);
  if v.frozen_at is not null then raise exception 'forecast vintage is frozen' using errcode='23514'; end if;
  if tg_op<>'DELETE' then
    if new.target_period<>(v.issue_period + make_interval(months=>new.horizon))::date then
      raise exception 'horizon target period must match issue period + horizon' using errcode='23514';
    end if;
    if tg_table_name='forecast_horizons' then
      select * into p from public.products where id=new.product_id;
      if new.category_id is distinct from p.category_id then
        raise exception 'horizon category snapshot must match product at creation' using errcode='23514';
      end if;
    end if;
  end if;
  return coalesce(new,old);
end $$;
create trigger forecast_horizon_guard before insert or update or delete on public.forecast_horizons for each row execute function public.contract_horizon_guard();
create trigger forecast_aggregate_guard before insert or update or delete on public.forecast_aggregates for each row execute function public.contract_horizon_guard();

create function public.contract_vintage_guard() returns trigger language plpgsql as $$
declare r public.forecast_runs%rowtype; m public.model_versions%rowtype; s public.research_snapshots%rowtype;
declare h integer; product_sum numeric; chain_sum numeric; n integer; c record;
begin
  if tg_op='DELETE' then raise exception 'forecast vintage cannot be deleted' using errcode='23514'; end if;
  if tg_op='UPDATE' and old.frozen_at is not null then raise exception 'frozen vintage is immutable' using errcode='23514'; end if;
  select * into r from public.forecast_runs where id=new.run_id;
  if r.chain_id<>new.chain_id or r.objective<>new.objective or r.issue_period<>new.issue_period or r.cutoff_at<>new.cutoff_at then
    raise exception 'vintage must match its run' using errcode='23514';
  end if;
  if new.champion_model_version_id is not null then
    select * into m from public.model_versions where id=new.champion_model_version_id;
    if m.run_id<>new.run_id then raise exception 'vintage model must belong to run' using errcode='23514'; end if;
  end if;
  if new.research_snapshot_id is not null then
    select * into s from public.research_snapshots where id=new.research_snapshot_id;
    if s.cutoff_at>new.cutoff_at then raise exception 'research postdates run cutoff' using errcode='23514'; end if;
    if new.frozen_at is not null and s.status<>'FROZEN' then raise exception 'linked research must be frozen first' using errcode='23514'; end if;
  end if;
  if new.frozen_at is not null and (tg_op='INSERT' or old.frozen_at is null) then
    if new.frozen_at<new.created_at then raise exception 'frozen_at predates creation' using errcode='23514'; end if;
    if exists(select 1 from public.forecast_horizons where vintage_id=new.id group by product_id having count(*)<>12) then
      raise exception 'every forecast product must have H1-H12' using errcode='23514';
    end if;
    for h in 1..12 loop
      select coalesce(sum(forecast_towell),0),count(*) into product_sum,n from public.forecast_horizons where vintage_id=new.id and horizon=h;
      if n=0 then raise exception 'missing H% forecast',h using errcode='23514'; end if;
      select forecast_towell into chain_sum from public.forecast_aggregates where vintage_id=new.id and level='CHAIN' and horizon=h;
      if not found or abs(chain_sum-product_sum)>0.001 then raise exception 'chain aggregate mismatch at H%',h using errcode='23514'; end if;
      for c in select category_id,sum(forecast_towell) as total from public.forecast_horizons where vintage_id=new.id and horizon=h and category_id is not null group by category_id loop
        select forecast_towell into chain_sum from public.forecast_aggregates where vintage_id=new.id and level='CATEGORY' and category_id=c.category_id and horizon=h;
        if not found or abs(chain_sum-c.total)>0.001 then raise exception 'category aggregate mismatch at H%',h using errcode='23514'; end if;
      end loop;
      if exists (select 1 from public.forecast_aggregates a where a.vintage_id=new.id and a.level='CATEGORY' and a.horizon=h and not exists
        (select 1 from public.forecast_horizons fh where fh.vintage_id=new.id and fh.horizon=h and fh.category_id=a.category_id)) then
        raise exception 'orphan category aggregate at H%',h using errcode='23514';
      end if;
    end loop;
  end if;
  return new;
end $$;
create trigger vintage_guard before insert or update or delete on public.forecast_vintages for each row execute function public.contract_vintage_guard();

create function public.contract_run_guard() returns trigger language plpgsql as $$
begin
  if exists(select 1 from public.forecast_vintages where run_id=old.id and frozen_at is not null) then
    raise exception 'run linked to frozen vintage is immutable' using errcode='23514';
  end if;
  if tg_op='UPDATE' and row(old.id,old.run_code,old.chain_id,old.objective,old.issue_period,old.cutoff_at,old.data_snapshot_hash,old.engine_version,old.git_sha)
    is distinct from row(new.id,new.run_code,new.chain_id,new.objective,new.issue_period,new.cutoff_at,new.data_snapshot_hash,new.engine_version,new.git_sha) then
    raise exception 'run identity and cutoff are immutable' using errcode='23514';
  end if;
  return coalesce(new,old);
end $$;
create trigger run_guard before update or delete on public.forecast_runs for each row execute function public.contract_run_guard();

create function public.contract_linked_frozen_guard() returns trigger language plpgsql as $$
declare frozen boolean;
begin
  if tg_table_name='research_snapshots' then
    frozen := old.frozen_at is not null or exists(select 1 from public.forecast_vintages where research_snapshot_id=old.id and frozen_at is not null);
  else
    frozen := exists(select 1 from public.forecast_vintages where run_id=old.run_id and frozen_at is not null);
  end if;
  if frozen then raise exception 'linked frozen evidence/model is immutable' using errcode='23514'; end if;
  return coalesce(new,old);
end $$;
create trigger research_frozen_guard before update or delete on public.research_snapshots for each row execute function public.contract_linked_frozen_guard();
create trigger model_frozen_guard before update or delete on public.model_versions for each row execute function public.contract_linked_frozen_guard();

create function public.contract_new_model_guard() returns trigger language plpgsql as $$
begin
  if exists(select 1 from public.forecast_vintages where run_id=new.run_id and frozen_at is not null) then
    raise exception 'cannot add a model to frozen run' using errcode='23514';
  end if;
  return new;
end $$;
create trigger new_model_guard before insert on public.model_versions for each row execute function public.contract_new_model_guard();

create function public.contract_research_source_guard() returns trigger language plpgsql as $$
declare s public.research_snapshots%rowtype;
begin
  select * into s from public.research_snapshots where id=coalesce(new.snapshot_id,old.snapshot_id);
  if s.frozen_at is not null then raise exception 'research snapshot sources are frozen' using errcode='23514'; end if;
  if tg_op<>'DELETE' and (new.published_at>s.cutoff_at or new.captured_at>s.cutoff_at) then
    raise exception 'research source postdates cutoff' using errcode='23514';
  end if;
  return coalesce(new,old);
end $$;
create trigger research_source_guard before insert or update or delete on public.research_snapshot_sources for each row execute function public.contract_research_source_guard();

create function public.contract_champion_scope() returns trigger language plpgsql as $$
declare m public.model_versions%rowtype; r public.forecast_runs%rowtype;
begin
  if new.scope_type='CHAIN' and new.scope_id<>new.chain_id then raise exception 'chain scope id mismatch' using errcode='23514'; end if;
  if new.scope_type='CATEGORY' and not exists(select 1 from public.categories where id=new.scope_id and chain_id=new.chain_id) then
    raise exception 'category scope mismatch' using errcode='23514'; end if;
  if new.scope_type='PRODUCT' and not exists(select 1 from public.products where id=new.scope_id and chain_id=new.chain_id) then
    raise exception 'product scope mismatch' using errcode='23514'; end if;
  select * into m from public.model_versions where id=new.model_version_id;
  select * into r from public.forecast_runs where id=m.run_id;
  if r.objective<>new.objective or m.certification_status<>'CERTIFIED' then
    raise exception 'champion requires a certified model for the objective' using errcode='23514';
  end if;
  if tg_op='UPDATE' and (old.status<>'ACTIVE' or new.status<>'RETIRED' or
    row(old.chain_id,old.objective,old.scope_type,old.scope_id,old.model_version_id,old.strategy,old.published_at,old.published_by,old.valid_from)
    is distinct from row(new.chain_id,new.objective,new.scope_type,new.scope_id,new.model_version_id,new.strategy,new.published_at,new.published_by,new.valid_from)) then
    raise exception 'champion can only transition ACTIVE to RETIRED' using errcode='23514';
  end if;
  return new;
end $$;
create trigger champion_scope_guard before insert or update on public.champion_registry for each row execute function public.contract_champion_scope();
create trigger champion_no_delete before delete on public.champion_registry for each row execute function public.contract_insert_only();

create function public.contract_decision_guard() returns trigger language plpgsql as $$
declare h public.forecast_horizons%rowtype; v public.forecast_vintages%rowtype;
begin
  select * into h from public.forecast_horizons where vintage_id=new.vintage_id and product_id=new.product_id and target_period=new.target_period;
  select * into v from public.forecast_vintages where id=new.vintage_id;
  if v.frozen_at is null or h.forecast_towell<>new.forecast_towell then
    raise exception 'decision must preserve frozen Forecast Towell' using errcode='23514';
  end if;
  return new;
end $$;
create trigger decision_guard before insert on public.forecast_decisions for each row execute function public.contract_decision_guard();

create function public.contract_run_input_guard() returns trigger language plpgsql as $$
declare r public.forecast_runs%rowtype; o public.monthly_observations%rowtype; c public.customer_forecast_versions%rowtype;
begin
  select * into r from public.forecast_runs where id=new.run_id;
  if exists(select 1 from public.forecast_vintages where run_id=new.run_id and frozen_at is not null) then
    raise exception 'cannot add input to frozen run' using errcode='23514';
  end if;
  if new.data_snapshot_hash<>r.data_snapshot_hash then raise exception 'run input snapshot hash mismatch' using errcode='23514'; end if;
  perform public.contract_evidence_chain(new.source_evidence_id,new.chain_id);
  if new.monthly_observation_id is not null then
    select * into o from public.monthly_observations where id=new.monthly_observation_id;
    if o.available_at is null or o.available_at>r.cutoff_at or o.period>r.issue_period then
      raise exception 'observation unavailable at run cutoff' using errcode='23514';
    end if;
  else
    select * into c from public.customer_forecast_versions where id=new.customer_forecast_version_id;
    if c.received_at>r.cutoff_at or c.issue_period>r.issue_period or c.status not in ('VALIDATED','FROZEN') then
      raise exception 'customer forecast unavailable at run cutoff' using errcode='23514';
    end if;
  end if;
  return new;
end $$;
create trigger run_input_guard before insert on public.forecast_run_inputs for each row execute function public.contract_run_input_guard();

create function public.contract_actual_guard() returns trigger language plpgsql as $$
declare h public.forecast_horizons%rowtype; o public.monthly_observations%rowtype; v public.forecast_vintages%rowtype; expected_metric text;
begin
  select * into h from public.forecast_horizons where vintage_id=new.vintage_id and product_id=new.product_id and horizon=new.horizon;
  select * into v from public.forecast_vintages where id=new.vintage_id;
  if v.frozen_at is null then raise exception 'actual evaluation requires frozen vintage' using errcode='23514'; end if;
  if h.target_period<>new.target_period or h.forecast_towell<>new.forecast_value then
    raise exception 'actual evaluation must match frozen horizon' using errcode='23514';
  end if;
  if new.actual_observation_id is not null then
    select * into o from public.monthly_observations where id=new.actual_observation_id;
    expected_metric := case v.objective when 'Venta' then 'SALES' when 'Pedido' then 'ORDER' else 'DELIVERY' end;
    if o.product_id<>new.product_id or o.period<>new.target_period or o.metric_code<>expected_metric or o.value<>new.actual_value or o.available_at is null or o.available_at>new.evaluated_at then
      raise exception 'actual evaluation must reference a known matching observation' using errcode='23514';
    end if;
  end if;
  return new;
end $$;
create trigger actual_evaluation_guard before insert on public.actual_evaluations for each row execute function public.contract_actual_guard();
