-- OPS-01: LIVE requires a forecast issued before the target month and first real.
-- Schema only; no imported observations or existing calculations are changed.
create function private.e4_live_temporally_eligible(p_calculation uuid,p_target date,p_sale uuid)
returns boolean language sql stable security invoker set search_path='' as $$
  select exists (
    select 1 from public.forecast_calculations c
    join public.monthly_observations o on o.id=p_sale
    join public.import_batches b on b.id=o.source_batch_id
    where c.id=p_calculation and o.chain_id=c.chain_id and o.product_id=c.product_id
      and o.period=p_target and o.metric_code='SALES' and b.status='IMPORTED'
      and o.available_at is not null and o.available_at<=now()
      and (p_target+interval '1 month')::timestamp at time zone 'UTC'<=now()
      and c.created_at<(p_target::timestamp at time zone 'UTC')
      and c.created_at<(select min(first_o.available_at)
        from public.monthly_observations first_o
        join public.import_batches first_b on first_b.id=first_o.source_batch_id
        where first_o.chain_id=c.chain_id and first_o.product_id=c.product_id
          and first_o.period=p_target and first_o.metric_code='SALES'
          and first_b.status='IMPORTED' and first_o.available_at is not null)
  )
$$;
revoke all on function private.e4_live_temporally_eligible(uuid,date,uuid) from public,anon,authenticated;
grant execute on function private.e4_live_temporally_eligible(uuid,date,uuid) to service_role;

create function private.e4_live_temporal_guard() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
  if not private.e4_live_temporally_eligible(new.calculation_id,new.target_period,new.sale_observation_id) then
    raise exception 'LIVE calculation temporal evidence missing' using errcode='23514';
  end if;
  return new;
end $$;
revoke all on function private.e4_live_temporal_guard() from public,anon,authenticated;
grant execute on function private.e4_live_temporal_guard() to service_role;
create trigger e4_live_temporal before insert on public.forecast_live_evaluations
  for each row execute function private.e4_live_temporal_guard();
create trigger e4_learning_temporal before insert on public.forecast_learning_events
  for each row execute function private.e4_live_temporal_guard();

create or replace function public.e4_close_live(p_chain uuid,p_product uuid,p_period date,p_actor uuid,p_key text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare sale public.monthly_observations%rowtype; ordered public.monthly_observations%rowtype;
declare delivered public.monthly_observations%rowtype; h record; choice public.forecast_selection_events%rowtype;
declare v_candidate text; amount numeric; best text; best_error numeric; suggested_error numeric;
declare selected_error numeric; signals jsonb; event_count integer:=0; eval_count integer:=0;
declare result jsonb; prior public.forecast_live_closures%rowtype; closure_id uuid;
declare first_sale_available_at timestamptz; learning_id uuid;
begin
  if not private.e4_actor_allowed(p_actor,p_chain,'close') or p_key is null or length(p_key) not between 1 and 128 or
    p_period is null or extract(day from p_period)<>1 then
    raise exception 'live close not authorized' using errcode='23514';
  end if;
  select o.* into sale from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='SALES'
      and b.status='IMPORTED' and o.available_at is not null
    order by o.version_no desc limit 1;
  if sale.id is null then raise exception 'confirmed sale required' using errcode='23514'; end if;
  select min(o.available_at) into first_sale_available_at
    from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period
      and o.metric_code='SALES' and b.status='IMPORTED' and o.available_at is not null;
  perform pg_advisory_xact_lock(hashtextextended(p_chain::text||p_product::text||p_period::text||sale.id::text,2));
  select * into prior from public.forecast_live_closures where evaluated_by=p_actor and idempotency_key=p_key;
  if found then
    if prior.chain_id<>p_chain or prior.product_id<>p_product or prior.target_period<>p_period or
      prior.sale_observation_id<>sale.id then raise exception 'idempotency payload conflict' using errcode='23514'; end if;
    return prior.result_json;
  end if;
  select * into prior from public.forecast_live_closures where chain_id=p_chain and product_id=p_product
    and target_period=p_period and sale_observation_id=sale.id;
  if found then return prior.result_json; end if;
  select o.* into ordered from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='ORDER' and b.status='IMPORTED'
    order by o.version_no desc limit 1;
  select o.* into delivered from public.monthly_observations o join public.import_batches b on b.id=o.source_batch_id
    where o.chain_id=p_chain and o.product_id=p_product and o.period=p_period and o.metric_code='DELIVERY' and b.status='IMPORTED'
    order by o.version_no desc limit 1;
  for h in select c.id as calculation_id,c.suggested_reference,c.chain_id,c.product_id,
      x.horizon,x.target_period,x.statistical_value,x.ml_value,x.ensemble_value
      from public.forecast_calculations c join public.forecast_calculation_horizons x on x.calculation_id=c.id
      where c.chain_id=p_chain and c.product_id=p_product and c.objective='Venta' and x.target_period=p_period
      and c.status in ('READY_FOR_DECISION','DECIDED','SUPERSEDED')
      and private.e4_live_temporally_eligible(c.id,p_period,sale.id) loop
    select * into choice from public.forecast_selection_events where calculation_id=h.calculation_id
      and selected_at <= first_sale_available_at and selected_at<(p_period::timestamp at time zone 'UTC')
      order by selected_at desc,id desc limit 1;
    best:=null; best_error:=null; suggested_error:=null; selected_error:=null;
    foreach v_candidate in array array['STATISTICAL','ML','ENSEMBLE','TOWELL_SELECTED'] loop
      amount:=case v_candidate when 'STATISTICAL' then h.statistical_value when 'ML' then h.ml_value
        when 'ENSEMBLE' then h.ensemble_value when 'TOWELL_SELECTED' then
          case choice.selected_candidate when 'STATISTICAL' then h.statistical_value
            when 'ML' then h.ml_value when 'ENSEMBLE' then h.ensemble_value end end;
      if amount is null then continue; end if;
      insert into public.forecast_live_evaluations(calculation_id,chain_id,product_id,horizon,target_period,
        candidate,forecast_value,actual_sale,absolute_error,signed_error,ape,sale_observation_id,
        sale_version_no,order_observation_id,delivery_observation_id,order_value,delivery_value,selected_event_id)
      values(h.calculation_id,p_chain,p_product,h.horizon,p_period,v_candidate,amount,sale.value,
        abs(sale.value-amount),sale.value-amount,case when sale.value>0 then abs(sale.value-amount)/sale.value*100 end,
        sale.id,sale.version_no,ordered.id,delivered.id,ordered.value,delivered.value,
        case when v_candidate='TOWELL_SELECTED' then choice.id end)
      on conflict (calculation_id,horizon,candidate,sale_observation_id) do nothing;
      eval_count:=eval_count+1;
      if v_candidate<>'TOWELL_SELECTED' and (best_error is null or abs(sale.value-amount)<best_error) then
        best:=v_candidate; best_error:=abs(sale.value-amount);
      end if;
      if v_candidate=upper(h.suggested_reference->>'family') then suggested_error:=abs(sale.value-amount); end if;
      if v_candidate='TOWELL_SELECTED' then selected_error:=abs(sale.value-amount); end if;
    end loop;
    signals:='[]'::jsonb;
    if selected_error is not null and selected_error>0 then signals:=signals||'"DEMAND_FORECAST_ERROR"'::jsonb; end if;
    if ordered.id is not null and ordered.value<>sale.value then signals:=signals||'"ORDER_DEVIATION"'::jsonb; end if;
    if delivered.id is not null and delivered.value>sale.value then signals:=signals||'"SELL_THROUGH_GAP"'::jsonb; end if;
    if ordered.id is not null and delivered.id is not null and delivered.value<ordered.value then
      signals:=signals||'"SUPPLY_SHORTFALL"'::jsonb;
    end if;
    if ordered.id is not null and delivered.id is not null and ordered.value>0 and delivered.value/ordered.value<0.95 then
      signals:=signals||'"LOW_FILL_RATE"'::jsonb;
    end if;
    if selected_error is not null and suggested_error is not null and
      choice.selected_candidate is distinct from upper(h.suggested_reference->>'family') then
      signals:=signals||to_jsonb(case when suggested_error>selected_error then
        'DECISION_OUTPERFORMED_RECOMMENDATION' else 'DECISION_UNDERPERFORMED_RECOMMENDATION' end);
    end if;
    learning_id:=null;
    insert into public.forecast_learning_events(calculation_id,chain_id,product_id,horizon,target_period,
      sale_observation_id,sale_version_no,suggested_candidate,selected_candidate,best_candidate_actual,
      order_value,sale_value,delivery_value,decision_regret_absolute,decision_value_added_absolute,
      signals_json,metrics_json)
    values(h.calculation_id,p_chain,p_product,h.horizon,p_period,sale.id,sale.version_no,
      upper(h.suggested_reference->>'family'),choice.selected_candidate,best,ordered.value,sale.value,delivered.value,
      case when selected_error is not null then selected_error-best_error end,
      case when choice.selected_candidate is distinct from upper(h.suggested_reference->>'family') and
        suggested_error is not null and selected_error is not null then suggested_error-selected_error end,
      signals,jsonb_build_object('actual_sale',sale.value,'actual_order',ordered.value,
        'actual_delivery',delivered.value,'statistical_forecast',h.statistical_value,
        'ml_forecast',h.ml_value,'ensemble_forecast',h.ensemble_value,
        'towell_forecast',case choice.selected_candidate when 'STATISTICAL' then h.statistical_value
          when 'ML' then h.ml_value when 'ENSEMBLE' then h.ensemble_value end,
        'statistical_absolute_error',abs(sale.value-h.statistical_value),
        'ml_absolute_error',case when h.ml_value is not null then abs(sale.value-h.ml_value) end,
        'ensemble_absolute_error',case when h.ensemble_value is not null then abs(sale.value-h.ensemble_value) end,
        'best_absolute_error',best_error,'selected_absolute_error',selected_error,
        'fill_rate',case when ordered.value>0 and delivered.id is not null then delivered.value/ordered.value*100 end,
        'order_observation_id',ordered.id,'delivery_observation_id',delivered.id))
    on conflict (calculation_id,horizon,sale_observation_id) do nothing returning id into learning_id;
    if learning_id is not null then
      event_count:=event_count+1;
      insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
        values(p_chain,p_actor,'LEARNING_EVENT','forecast_learning_event',learning_id,
          jsonb_build_object('calculation_id',h.calculation_id,'horizon',h.horizon,
            'target_period',p_period,'sale_observation_id',sale.id));
    end if;
  end loop;
  if eval_count=0 then
    return jsonb_build_object('status','NO_TEMPORALLY_ELIGIBLE_CALCULATIONS','period',p_period,
      'sale_observation_id',sale.id,'evaluations',0,'learning_events',0);
  end if;
  result:=jsonb_build_object('status','CLOSED','period',p_period,'sale_observation_id',sale.id,
    'evaluations',eval_count,'learning_events',event_count);
  insert into public.forecast_live_closures(chain_id,product_id,target_period,sale_observation_id,
    evaluated_by,idempotency_key,result_json)
    values(p_chain,p_product,p_period,sale.id,p_actor,p_key,result) returning id into closure_id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(p_chain,p_actor,'LIVE_EVALUATION','forecast_live_closure',closure_id,result);
  return result;
end $$;
notify pgrst, 'reload schema';
