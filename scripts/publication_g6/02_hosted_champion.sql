-- FT-PUBLICAR-G6-01. Apply after 01_hosted_reads.sql. No activation occurs here.
-- The certified E3 registry and E4 selections are deliberately untouched.
create table if not exists private.ft_g6_champion_event (
 id uuid primary key, actor_id uuid not null references public.profiles(id),
 idempotency_key text not null, request_hash text not null, payload jsonb not null,
 created_at timestamptz not null default clock_timestamp(), unique(actor_id,idempotency_key));
create table if not exists private.ft_g6_champion_current (
 source_hash text not null, generation bigint not null, planning_year integer not null,
 product_id uuid not null, chain_id uuid not null, event_id uuid not null references private.ft_g6_champion_event(id),
 primary key(source_hash,generation,planning_year,product_id));
alter table private.ft_g6_champion_event enable row level security;
alter table private.ft_g6_champion_current enable row level security;
revoke all on private.ft_g6_champion_event,private.ft_g6_champion_current from public,anon,authenticated,service_role;

create or replace function private.ft_g6_champion_immutable() returns trigger
language plpgsql set search_path='' as $$ begin raise exception 'CHAMPION_EVENT_IMMUTABLE' using errcode='23514'; end $$;
drop trigger if exists ft_g6_champion_immutable on private.ft_g6_champion_event;
create trigger ft_g6_champion_immutable before update or delete on private.ft_g6_champion_event
 for each row execute function private.ft_g6_champion_immutable();

create or replace function private.ft_g6_release_for(p_hash text,p_generation bigint)
returns private.ft_g6_release language plpgsql stable security definer set search_path='' as $$
declare r private.ft_g6_release;
begin
 if auth.uid() is null or not exists(select 1 from public.profiles p where p.id=auth.uid() and p.status='ACTIVE')
 then raise exception 'SCOPE_FORBIDDEN' using errcode='42501'; end if;
 select * into strict r from private.ft_g6_release x where x.active;
 if r.context->>'source_hash' is distinct from p_hash or (r.context->>'generation')::bigint is distinct from p_generation
  or r.context->>'forecast_contract' is distinct from 'pedido-unificado-v1' or r.context->>'planning_basis' is distinct from 'OBSERVED_AND_COMMERCIAL_CLOSE'
  or r.context->>'target_metric' is distinct from 'ORDER' or r.context->>'objective' is distinct from 'Pedido'
  or r.context->>'training_cutoff' is distinct from '2026-12' or r.context->>'observed_cutoff' is distinct from '2026-09'
  or r.context->>'forecast_available' is distinct from 'true' or p_generation is distinct from 6::bigint
  or r.context->>'authorized_source_hash' is distinct from p_hash or r.context->>'unit' is distinct from 'PIEZAS'
  or r.context->>'target_sheet' is distinct from 'Base_2024_2026'
  or r.context->>'target_file_sha256' is distinct from '29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d'
  or r.context->>'planning_start' is distinct from '2027-01' or r.context->>'planning_end' is distinct from '2027-12'
  or r.metadata->>'planning_year' is distinct from '2027'
  or r.metadata->>'schema' is distinct from 'unified-planning-run-1'
 then raise exception 'OFFICIAL_SOURCE_MISMATCH' using errcode='40001'; end if;
 if (r.metadata->>'emitted_at') is null or (r.metadata->>'emitted_at')::timestamptz>clock_timestamp()
  or to_char((r.metadata->>'emitted_at')::timestamptz at time zone 'UTC','YYYY-MM')<=r.context->>'observed_cutoff'
 then raise exception 'CHAMPION_RUN_UNAVAILABLE' using errcode='55000'; end if;
 return r;
exception when no_data_found or too_many_rows then
 raise exception 'CHAMPION_RUN_UNAVAILABLE' using errcode='55000';
end $$;

create or replace function private.ft_g6_champion_scope(p_hash text,p_generation bigint,p_chains uuid[],p_products uuid[],p_write boolean)
returns void language plpgsql stable security definer set search_path='' as $$
declare n integer; actual uuid[];
begin
 if p_chains is null or p_products is null or cardinality(p_chains) not between 1 and 100 or cardinality(p_products) not between 1 and 2000
  or cardinality(p_chains)<>(select count(distinct x) from unnest(p_chains) x)
  or cardinality(p_products)<>(select count(distinct x) from unnest(p_products) x)
 then raise exception 'REQUEST_001' using errcode='22023'; end if;
 if p_write and not private.is_active_admin() then raise exception 'MANAGER_REQUIRED' using errcode='42501'; end if;
 if exists(select 1 from unnest(p_chains) x where not private.can_chain(x,case when p_write then 'run_forecast' else 'view' end))
 then raise exception 'SCOPE_FORBIDDEN' using errcode='42501'; end if;
 select count(*),array_agg(distinct c.chain_id order by c.chain_id) into n,actual
 from public.operational_read_catalog(p_hash,p_generation) c where c.product_id=any(p_products) and c.chain_id=any(p_chains) and c.purchase_type='RS';
 if n<>cardinality(p_products) or actual is distinct from array(select x from unnest(p_chains) x order by x)
 then raise exception 'SCOPE_FORBIDDEN' using errcode='42501'; end if;
end $$;

create or replace function public.operational_read_planning_run(p_source_hash text,p_generation bigint,p_product_ids uuid[],p_chain_ids uuid[])
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare r private.ft_g6_release; products jsonb;
begin
 r:=private.ft_g6_release_for(p_source_hash,p_generation);
 if cardinality(p_product_ids)=0 and cardinality(p_chain_ids)=0 then products:='[]'::jsonb;
 else
  perform private.ft_g6_champion_scope(p_source_hash,p_generation,p_chain_ids,p_product_ids,false);
  select coalesce(jsonb_agg(p.payload order by p.product_id),'[]'::jsonb) into products
  from private.ft_g6_product p where p.run_id=r.run_id and p.product_id=any(p_product_ids) and p.chain_id=any(p_chain_ids)
   and private.can_chain(p.chain_id,'view');
 end if;
 return r.metadata||jsonb_build_object('run_id',r.run_id,'source_context',r.context,'products',products,
  'file_sha256',r.file_sha256,'content_digest',r.content_digest,'transport','SUPABASE_SEALED_G6');
end $$;

create or replace function public.operational_read_champions(p_source_hash text,p_generation bigint,p_planning_year integer,p_chain_ids uuid[],p_product_ids uuid[])
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare r private.ft_g6_release; result jsonb;
begin
 r:=private.ft_g6_release_for(p_source_hash,p_generation);
 perform private.ft_g6_champion_scope(p_source_hash,p_generation,p_chain_ids,p_product_ids,false);
 if p_planning_year is distinct from (r.metadata->>'planning_year')::integer then raise exception 'REQUEST_001' using errcode='22023'; end if;
 select coalesce(jsonb_agg((e.payload-'products'-'previous_champion_ids')||jsonb_build_object('product',p.item) order by c.product_id),'[]'::jsonb)
 into result from private.ft_g6_champion_current c join private.ft_g6_champion_event e on e.id=c.event_id
 cross join lateral jsonb_array_elements(e.payload->'products') p(item)
 where c.source_hash=p_source_hash and c.generation=p_generation and c.planning_year=p_planning_year
  and c.product_id=any(p_product_ids) and c.chain_id=any(p_chain_ids) and private.can_chain(c.chain_id,'view')
  and p.item->>'product_id'=c.product_id::text and p.item->>'chain_id'=c.chain_id::text
  and e.payload->'source_context'=r.context;
 return jsonb_build_object('source_context',r.context,'planning_year',p_planning_year,'products',result,
  'covered',jsonb_array_length(result),'eligible',cardinality(p_product_ids));
end $$;

create or replace function public.operational_save_champion(p_source_hash text,p_generation bigint,p_run_id uuid,p_candidate_family text,
 p_planning_year integer,p_chain_ids uuid[],p_product_ids uuid[],p_comment text,p_idempotency_key text,p_confirm boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
declare r private.ft_g6_release; item record; candidate jsonb; h jsonb; idx integer; wanted text;
 selected jsonb:='[]'::jsonb; previous jsonb:='{}'::jsonb; event jsonb; event_id uuid;
 signature text; existing private.ft_g6_champion_event; calendar text;
begin
 r:=private.ft_g6_release_for(p_source_hash,p_generation);
 -- Serialize publication against release cutover and concurrent choices. The
 -- authoritative row is checked again after waiting for this lock.
 select * into r from private.ft_g6_release x where x.run_id=r.run_id and x.active for update;
 if not found then raise exception 'OFFICIAL_SOURCE_MISMATCH' using errcode='40001'; end if;
 perform private.ft_g6_champion_scope(p_source_hash,p_generation,p_chain_ids,p_product_ids,true);
 if p_confirm is distinct from true or p_run_id is distinct from r.run_id or p_planning_year is distinct from (r.metadata->>'planning_year')::integer
  or p_candidate_family is null or p_candidate_family not in ('STATISTICAL','ML','ENSEMBLE','COMMERCIAL_PLAN')
  or length(btrim(p_comment)) not between 5 and 1000 or p_idempotency_key !~ '^[!-~]{1,128}$'
  or p_comment is null or p_idempotency_key is null
 then raise exception 'REQUEST_001' using errcode='22023'; end if;
 signature:=encode(pg_catalog.sha256(convert_to(jsonb_build_object('source_context',r.context,'run_id',p_run_id,
  'family',p_candidate_family,'year',p_planning_year,'products',array(select x from unnest(p_product_ids) x order by x),
  'chains',array(select x from unnest(p_chain_ids) x order by x),'actor',auth.uid(),'comment',btrim(p_comment))::text,'UTF8')),'hex');
 select * into existing from private.ft_g6_champion_event e where e.actor_id=auth.uid() and e.idempotency_key=p_idempotency_key;
 if found then
  if existing.request_hash<>signature then raise exception 'CHAMPION_IDEMPOTENCY_CONFLICT' using errcode='23505'; end if;
  return existing.payload;
 end if;
 if (select count(*) from private.ft_g6_product p where p.run_id=r.run_id and p.product_id=any(p_product_ids) and p.chain_id=any(p_chain_ids))<>cardinality(p_product_ids)
 then raise exception 'CHAMPION_CANDIDATE_INELIGIBLE' using errcode='23514'; end if;
 for item in select p.* from private.ft_g6_product p where p.run_id=r.run_id and p.product_id=any(p_product_ids) order by p.product_id loop
  candidate:=item.payload->'candidates'->p_candidate_family;
  if item.payload->'input_snapshot'->'source_context' is distinct from r.context
   or candidate is null or jsonb_typeof(candidate)<>'object' or candidate->>'family' is distinct from p_candidate_family
   or nullif(btrim(candidate->>'model'),'') is null or nullif(btrim(candidate->>'origin'),'') is null or candidate->>'evaluation_target_basis' is distinct from 'OBSERVED_ONLY'
   or (p_candidate_family='COMMERCIAL_PLAN' and nullif(btrim(candidate->>'source_reference'),'') is null)
   or (p_candidate_family<>'COMMERCIAL_PLAN' and candidate->>'origin' is distinct from 'PYTHON_EXISTING_ENGINE')
   or jsonb_typeof(candidate->'horizons') is distinct from 'array' or jsonb_array_length(candidate->'horizons')<>12
  then raise exception 'CHAMPION_CANDIDATE_INELIGIBLE' using errcode='23514'; end if;
  for idx in 1..12 loop
   select value into h from jsonb_array_elements(candidate->'horizons') where value->>'planning_horizon'=idx::text;
   if (select count(*) from jsonb_array_elements(candidate->'horizons') where value->>'planning_horizon'=idx::text)<>1
    then raise exception 'CHAMPION_CANDIDATE_INELIGIBLE' using errcode='23514'; end if;
   wanted:=p_planning_year::text||'-'||lpad(idx::text,2,'0');
   if jsonb_typeof(h->'planning_horizon') is distinct from 'number' or jsonb_typeof(h->'horizon') is distinct from 'number'
    or jsonb_typeof(h->'forecast_distance') is distinct from 'number'
    or h->>'horizon' is distinct from idx::text or h->>'forecast_distance' is distinct from idx::text or h->>'target_period' is distinct from wanted
    or jsonb_typeof(h->'value') is distinct from 'string' or h->>'value' !~ '^[0-9]+(\.[0-9]+)?$'
   then raise exception 'CHAMPION_CANDIDATE_INELIGIBLE' using errcode='23514'; end if;
  end loop;
  selected:=selected||jsonb_build_array(jsonb_build_object('product_id',item.product_id,'chain_id',item.chain_id,
   'identity_key',item.payload->'identity_key','model_group_key',item.payload->'model_group_key',
   'candidate_family',p_candidate_family,'model',candidate->'model','origin',candidate->'origin','source_reference',candidate->'source_reference',
   'evaluation_signature',candidate->'evaluation_signature','technical_eligible',coalesce(candidate->'eligible','false'::jsonb),
   'technical_status',candidate->'status','technical_reason',candidate->'reason','technical_metrics',candidate->'metrics','horizons',candidate->'horizons'));
 end loop;
 select coalesce(jsonb_object_agg(c.product_id::text,c.event_id),'{}'::jsonb) into previous from private.ft_g6_champion_current c
 where c.source_hash=p_source_hash and c.generation=p_generation and c.planning_year=p_planning_year and c.product_id=any(p_product_ids);
 event_id:=pg_catalog.gen_random_uuid();
 event:=jsonb_build_object('id',event_id,'environment','hosted','status','ACTIVE','publication_scope','HOSTED_OPERATIONAL','official_publication',false,
  'objective','Pedido','unit','PIEZAS','planning_year',p_planning_year,'cutoff',r.context->>'training_cutoff','run_id',r.run_id,
  'run_content_digest',r.content_digest,'run_file_sha256',r.file_sha256,'run_emitted_at',r.metadata->'emitted_at','source_context',r.context,
  'candidate_family',p_candidate_family,'products',selected,'selected_by',auth.uid(),'selected_at',clock_timestamp(),'comment',btrim(p_comment),
  'recommendation_changed',false,'ft_decision_changed',false,'previous_champion_ids',previous,'request_hash',signature);
 insert into private.ft_g6_champion_event(id,actor_id,idempotency_key,request_hash,payload) values(event_id,auth.uid(),p_idempotency_key,signature,event);
 insert into private.ft_g6_champion_current(source_hash,generation,planning_year,product_id,chain_id,event_id)
 select p_source_hash,p_generation,p_planning_year,(x->>'product_id')::uuid,(x->>'chain_id')::uuid,event_id from jsonb_array_elements(selected) x
 on conflict(source_hash,generation,planning_year,product_id) do update set event_id=excluded.event_id,chain_id=excluded.chain_id;
 return event;
end $$;

revoke all on function private.ft_g6_champion_immutable(),private.ft_g6_release_for(text,bigint),private.ft_g6_champion_scope(text,bigint,uuid[],uuid[],boolean) from public,anon,authenticated,service_role;
revoke all on function public.operational_read_planning_run(text,bigint,uuid[],uuid[]),public.operational_read_champions(text,bigint,integer,uuid[],uuid[]),
 public.operational_save_champion(text,bigint,uuid,text,integer,uuid[],uuid[],text,text,boolean) from public,anon,service_role;
grant execute on function public.operational_read_planning_run(text,bigint,uuid[],uuid[]),public.operational_read_champions(text,bigint,integer,uuid[],uuid[]),
 public.operational_save_champion(text,bigint,uuid,text,integer,uuid[],uuid[],text,text,boolean) to authenticated;
