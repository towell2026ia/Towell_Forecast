-- Local pilot only. Run inside the source loader's owner transaction on loopback.
do $guard$
begin
  if current_database() !~ '^gateb_official_[a-z0-9_]+$'
    or inet_server_addr() is distinct from inet '127.0.0.1'
    or current_setting('listen_addresses') <> '127.0.0.1'
    or nullif(current_setting('gateb.lab_id', true), '') is null
    or current_setting('towell.official_database', true) is distinct from current_database()
    or coalesce(current_setting('towell.official_source_hash', true), '') !~ '^[0-9a-f]{64}$'
  then raise exception 'LOCAL_PEDIDO_LAB_REQUIRED'; end if;
end $guard$;

-- Keep the original E4 operation, actor check, idempotency, twelve-horizon
-- validation, source lock and audit. Scope numbering is per objective.
do $upgrade$
declare
  body text := pg_get_functiondef('private.official_e4_create_impl(jsonb)'::regprocedure);
  original text[] := array[
    $s$p->>'objective'<>'Venta'$s$,
    $s$chain::text||product::text||'Venta'$s$,
    $s$product_id=product and objective='Venta'$s$,
    $s$values(chain,product,'Venta',number$s$
  ];
  revised text[] := array[
    $s$coalesce(p->>'objective','') not in ('Venta','Pedido')$s$,
    $s$chain::text||product::text||(p->>'objective')$s$,
    $s$product_id=product and objective=(p->>'objective')$s$,
    $s$values(chain,product,p->>'objective',number$s$
  ];
  slot integer;
begin
  for slot in 1..array_length(original, 1) loop
    if length(body) - length(replace(body, original[slot], '')) = length(original[slot])
       and strpos(body, revised[slot]) = 0 then
      body := replace(body, original[slot], revised[slot]);
    elsif strpos(body, original[slot]) <> 0
       or length(body) - length(replace(body, revised[slot], '')) <> length(revised[slot]) then
      raise exception 'LOCAL_PEDIDO_E4_IMPL_UNRECOGNIZED at %',slot;
    end if;
  end loop;
  execute body;
  if not exists (select 1 from pg_constraint where conrelid='public.forecast_calculations'::regclass
                   and conname='local_e4_objective') then
    if (select pg_get_expr(conbin, conrelid) from pg_constraint
         where conrelid='public.forecast_calculations'::regclass and conname='forecast_calculations_objective_check')
       is distinct from '(objective = ''Venta''::text)' then
      raise exception 'LOCAL_PEDIDO_OBJECTIVE_CONSTRAINT_UNRECOGNIZED';
    end if;
    alter table public.forecast_calculations add constraint local_e4_objective check (objective in ('Venta','Pedido'));
    alter table public.forecast_calculations drop constraint forecast_calculations_objective_check;
  end if;
end $upgrade$;

create or replace function private.local_verify_pedido_calculation()
returns trigger language plpgsql security invoker set search_path='' as $verify$
declare
  ctx jsonb := new.input_snapshot->'source_context';
  inputs jsonb := new.input_snapshot->'observation_ids';
  master uuid;
begin
  if new.objective <> 'Pedido' and (ctx->>'forecast_contract') is distinct from 'pedido-towell-v1' then return new; end if;
  if new.objective <> 'Pedido' or not coalesce(ctx @> '{"forecast_contract":"pedido-towell-v1","objective":"Pedido","target_metric":"ORDER","target_sheet":"Pedidos","unit":"PIEZAS","target_source_available":true,"forecast_available":true}', false)
    or not private.official_result_current(ctx)
    or (ctx->>'authorized_source_hash') is distinct from (ctx->>'source_hash')
    or coalesce(ctx->>'training_cutoff','') !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'
    or to_char(new.issue_period,'YYYY-MM') > (ctx->>'training_cutoff')
    or to_char(new.cutoff_at at time zone 'UTC','YYYY-MM') <> to_char(new.issue_period,'YYYY-MM')
    or jsonb_typeof(inputs) is distinct from 'array'
    or new.evidence_mode <> 'RETROSPECTIVE_TRAINING'
  then raise exception 'LOCAL_PEDIDO_SOURCE_OR_CUT_INVALID'; end if;

  -- The accredited closure lives in the sealed master's evidence, independently
  -- of the request. The excluded partial month can still be read as history.
  select e.id into strict master from public.official_source_current s
    join public.source_evidence e on e.id=any(s.evidence_ids)
    where s.singleton and e.source_hash=ctx->>'target_file_sha256'
      and starts_with(e.description, 'Pedidos!K:M ORDER captured, confirmed closed through '||(ctx->>'training_cutoff')||';');
  if jsonb_array_length(inputs)=0 or
    (select count(distinct id) from jsonb_array_elements_text(inputs) i(id)) <> jsonb_array_length(inputs)
    or exists (
      select 1 from jsonb_array_elements_text(inputs) i(id) where not exists (
        select 1 from public.monthly_observations o
          join private.official_observation_source p on p.observation_id=o.id
          join public.import_batches b on b.id=o.source_batch_id
        where o.id=i.id::uuid and o.chain_id=new.chain_id and o.product_id=new.product_id
          and o.metric_code='ORDER' and o.period<=new.issue_period and p.purchase_type='RS'
          and p.source_reference ~ '^Pedidos![KLM][1-9][0-9]*$'
          and b.official_source_hash=ctx->>'source_hash' and b.evidence_id=master and b.status='IMPORTED'
      )
    ) then raise exception 'LOCAL_PEDIDO_INPUT_LINEAGE_INVALID'; end if;
  return new;
end $verify$;
revoke all on function private.local_verify_pedido_calculation() from public,anon,authenticated,service_role;
create or replace trigger local_pedido_input_guard before insert on public.forecast_calculations
  for each row execute function private.local_verify_pedido_calculation();
