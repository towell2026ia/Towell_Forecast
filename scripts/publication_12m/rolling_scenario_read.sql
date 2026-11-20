-- PROPOSED. Apply only after separate authorization and rolling_source_calendar.
-- No original G6 routine is replaced, no source/release is activated here.
begin;
create or replace function public.operational_read_rolling_scenario(
 p_source_hash text,p_generation bigint,p_product_ids uuid[],p_chain_ids uuid[])
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare r private.ft_g6_release; products jsonb; visible_count bigint;
begin
 if auth.uid() is null or not exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE')
 then raise exception 'AUTH_REQUIRED' using errcode='42501'; end if;
 if p_generation<=6 or p_chain_ids is null or p_product_ids is null
  or cardinality(p_chain_ids) not between 1 and 100 or cardinality(p_product_ids) not between 1 and 2000
  or cardinality(p_chain_ids)<>(select count(distinct x) from unnest(p_chain_ids) x)
  or cardinality(p_product_ids)<>(select count(distinct x) from unnest(p_product_ids) x)
 then raise exception 'REQUEST_001' using errcode='22023'; end if;
 select * into r from private.ft_g6_release x where x.active
  and x.context->>'source_hash'=p_source_hash and (x.context->>'generation')::bigint=p_generation;
 if not found or r.context->>'forecast_contract' is distinct from 'pedido-unificado-v1'
  or r.context->>'objective' is distinct from 'Pedido' or r.context->>'target_metric' is distinct from 'ORDER'
  or r.context->>'planning_basis' is distinct from 'OBSERVED_AND_COMMERCIAL_CLOSE'
  or r.context->>'scenario_hash' !~ '^[a-f0-9]{64}$'
  or r.context->>'observed_cutoff' >= r.context->>'training_cutoff'
 then raise exception 'OFFICIAL_SOURCE_MISMATCH' using errcode='40001'; end if;
 if exists(select 1 from unnest(p_chain_ids) x where not private.can_chain(x,'view'))
 then raise exception 'SCOPE_FORBIDDEN' using errcode='42501'; end if;
 select count(*) into visible_count from public.operational_read_catalog(p_source_hash,p_generation) c
 where c.product_id=any(p_product_ids) and c.chain_id=any(p_chain_ids) and c.purchase_type='RS';
 if visible_count<>cardinality(p_product_ids)
 then raise exception 'SCOPE_FORBIDDEN' using errcode='42501'; end if;
 -- The exact imported source context and row references are returned, not an
 -- archive blob, aggregate totals or another release's fallback candidates.
 if exists(select 1 from private.ft_g6_product p where p.run_id=r.run_id and p.product_id=any(p_product_ids)
  and (p.payload->'input_snapshot'->'source_context' is distinct from r.context
   or jsonb_typeof(p.payload->'input_snapshot'->'commercial_history') is distinct from 'array'))
 then raise exception 'PLANNING_SOURCE_UNAVAILABLE' using errcode='23514'; end if;
 select coalesce(jsonb_agg(jsonb_build_object('product_id',p.product_id,'chain_id',p.chain_id,
  'input_snapshot',jsonb_build_object('source_context',r.context,
   'commercial_history',(select coalesce(jsonb_agg(jsonb_build_object('period',h->'period','value',h->'value',
      'basis',h->'basis','source_reference',h->'source_reference','source_id',h->'source_id')),'[]'::jsonb)
     from jsonb_array_elements(p.payload->'input_snapshot'->'commercial_history') h))) order by p.product_id),'[]'::jsonb)
 into products from private.ft_g6_product p where p.run_id=r.run_id and p.product_id=any(p_product_ids)
  and p.chain_id=any(p_chain_ids) and private.can_chain(p.chain_id,'view');
 return jsonb_build_object('transport','SUPABASE_SEALED_ROLLING_SCENARIO','source_context',r.context,
  'run_id',r.run_id,'content_digest',r.content_digest,'file_sha256',r.file_sha256,'products',products);
end $$;
revoke all on function public.operational_read_rolling_scenario(text,bigint,uuid[],uuid[]) from public,anon,service_role;
grant execute on function public.operational_read_rolling_scenario(text,bigint,uuid[],uuid[]) to authenticated;
commit;
