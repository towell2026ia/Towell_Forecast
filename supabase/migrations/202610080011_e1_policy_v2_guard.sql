-- Prepared only. DO NOT apply remotely in PRD 09.2E1 without separate approval.
-- E3-GATES-1.0.0 remains valid for its immutable historical evidence.
-- A v2 vintage may be frozen only when all three gates use ONE version.
create or replace function public.e3_freeze_vintage(p_vintage_id uuid,p_actor_id uuid) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype; r public.forecast_runs%rowtype;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  if v.id is null or not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') then
    raise exception 'manager or vintage missing' using errcode='23514'; end if;
  if v.frozen_at is not null then return v.id; end if;
  select * into r from public.forecast_runs where id=v.run_id;
  if r.status<>'COMPLETED' or not exists(select 1 from public.forecast_run_inputs where run_id=r.id) or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id
       and policy_version in ('E3-GATES-1.0.0','E3-GATES-2.0.0')
       group by policy_version having count(*)=3 and count(distinct gate_type)=3) or
     (select count(*) from public.forecast_quality_gates where vintage_id=v.id)<>3 then
    raise exception 'vintage not freezable' using errcode='23514'; end if;
  update public.forecast_vintages set frozen_at=now() where id=v.id;
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id)
    values(v.chain_id,p_actor_id,'FREEZE_VINTAGE','forecast_vintage',v.id);
  return v.id;
end $$;

-- No v2 Champion promotion until the E4 decision contract and PIT certification
-- fields can be checked atomically in the promotion RPC. Existing v1 behavior
-- remains unchanged. This avoids promoting a suggested retrospective model.
create or replace function public.e3_promote_champion(p_vintage_id uuid,p_model_version_id uuid,
  p_actor_id uuid,p_comment text) returns uuid
language plpgsql security invoker set search_path = '' as $$
declare v public.forecast_vintages%rowtype; m public.model_versions%rowtype; c_id uuid;
begin
  select * into v from public.forecast_vintages where id=p_vintage_id for update;
  select * into m from public.model_versions where id=p_model_version_id;
  if v.id is null or v.frozen_at is null or m.id is null or m.run_id<>v.run_id or
     m.certification_status<>'CERTIFIED' or p_comment is null or length(trim(p_comment))<5 or
     exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and policy_version='E3-GATES-2.0.0') or
     not exists(select 1 from public.profiles where id=p_actor_id and status='ACTIVE' and global_role='ADMIN') or
     not exists(select 1 from public.forecast_quality_gates where vintage_id=v.id and gate_type='FORECAST_QUALITY'
       and status='FORECAST_QUALITY_READY' and details_json->>'no_degradation'='true') then
    raise exception 'champion precondition failed' using errcode='23514'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v.chain_id::text||v.objective,0));
  select id into c_id from public.champion_registry where chain_id=v.chain_id and objective=v.objective
    and scope_type='CHAIN' and scope_id=v.chain_id and status='ACTIVE';
  if c_id is not null and exists(select 1 from public.champion_registry where id=c_id and model_version_id=m.id) then
    return c_id; end if;
  if c_id is not null then
    update public.champion_registry set status='RETIRED',valid_to=now() where id=c_id;
    insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id)
      values(v.chain_id,p_actor_id,'RETIRE_CHAMPION','champion_registry',c_id);
  end if;
  c_id := gen_random_uuid();
  insert into public.champion_registry(id,chain_id,objective,scope_type,scope_id,model_version_id,
    strategy,published_at,published_by,valid_from,status)
  values(c_id,v.chain_id,v.objective,'CHAIN',v.chain_id,m.id,m.algorithm,now(),p_actor_id,now(),'ACTIVE');
  insert into public.audit_log(chain_id,actor_id,action,entity_type,entity_id,new_data)
    values(v.chain_id,p_actor_id,'PROMOTE_CHAMPION','champion_registry',c_id,
      jsonb_build_object('vintage_id',v.id,'model_version_id',m.id,'comment',p_comment));
  return c_id;
end $$;
