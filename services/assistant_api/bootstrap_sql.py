"""Offline SQL generation for controlled administrative publication, never runtime.

Durable STAGING job/log chunks are NOT published facts. A single final SQL
transaction creates every master, physical batch, evidence and observation.
Neither RLS nor the append-only guards are relaxed during bootstrap.
"""
from __future__ import annotations

import json
from uuid import UUID

from services.assistant_api.certified_bootstrap import BOOTSTRAP_KIND, safe_metadata, uid
from services.assistant_api.source_adjudication import fingerprint

CHUNK_SIZE = 400


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def serialized(value):
    return json.dumps(safe_metadata(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(plan):
    if fingerprint({k: v for k, v in plan.items() if k != "plan_sha"}) != plan["plan_sha"]:
        raise ValueError("plan_content_changed")
    manifest = plan["manifest"]
    bindings = {k: manifest[k] for k in ("kind", "dataset_sha", "certification_sha", "source_hashes", "code_commit", "bootstrap_at")}
    bid = str(UUID(manifest["bootstrap_id"]))
    if manifest["kind"] != BOOTSTRAP_KIND or uid("bootstrap", bindings) != bid:
        raise ValueError("bootstrap_identity_changed")
    return bid


def chunks(plan):
    return [plan["observations"][i:i+CHUNK_SIZE] for i in range(0, len(plan["observations"]), CHUNK_SIZE)]


def master(plan):
    return {k: plan[k] for k in ("manifest", "scopes", "categories", "products", "batches", "scope_summary", "plan_sha")}


def actor_guard(actor):
    actor = str(UUID(actor))
    return f"""if not exists(select 1 from public.profiles p join auth.users u on u.id=p.id
      where p.id='{actor}'::uuid and p.status='ACTIVE' and p.global_role='ADMIN') then
      raise exception 'approved_auth_admin_required'; end if;"""


def stage_setup_sql(plan, actor):
    bid = seal(plan)
    payload = {"master": master(plan), "chunk_hashes": [fingerprint(c) for c in chunks(plan)]}
    metadata = literal(serialized(payload))
    return f"""begin;
do $bootstrap$ declare m jsonb := {metadata}::jsonb; begin
 {actor_guard(actor)}
 perform pg_advisory_xact_lock(hashtextextended('{bid}',0));
 if exists(select 1 from public.jobs where id='{bid}'::uuid and
    (job_type<>{literal(BOOTSTRAP_KIND)} or requested_by<>'{actor}'::uuid or metadata_json<>m)) then
   raise exception 'bootstrap_manifest_collision'; end if;
 insert into public.jobs(id,job_type,status,requested_by,requested_at,started_at,metadata_json)
 values ('{bid}'::uuid,{literal(BOOTSTRAP_KIND)},'STAGING','{actor}'::uuid,
   (m->'master'->'manifest'->>'bootstrap_at')::timestamptz,now(),m) on conflict(id) do nothing;
end $bootstrap$;
select id,status from public.jobs where id='{bid}'::uuid;
commit;"""


def stage_chunk_sql(plan, actor, index):
    bid = seal(plan)
    values = chunks(plan)
    chunk = values[index]
    payload = {"index": index, "sha256": fingerprint(chunk), "payload_text": serialized(chunk)}
    step = f"CERTIFIED_STAGE_{index:04d}"
    return f"""begin;
do $bootstrap$ declare m jsonb := {literal(serialized(payload))}::jsonb; s text; begin
 {actor_guard(actor)}
 perform pg_advisory_xact_lock(hashtextextended('{bid}',0));
 select status into s from public.jobs where id='{bid}'::uuid and job_type={literal(BOOTSTRAP_KIND)};
 if s is null or s not in ('STAGING','COMPLETED') then raise exception 'bootstrap_not_staging'; end if;
 if exists(select 1 from public.run_logs where job_id='{bid}'::uuid and step={literal(step)} and metadata_json<>m) then
   raise exception 'staging_chunk_collision'; end if;
 if s='COMPLETED' then return; end if;
 if not exists(select 1 from public.run_logs where job_id='{bid}'::uuid and step={literal(step)}) then
   insert into public.run_logs(id,job_id,step,status,message,metadata_json)
   values ('{uid('stage', [bid,index])}'::uuid,'{bid}'::uuid,{literal(step)},'STAGED','Certified-only private staging chunk',m);
 end if;
end $bootstrap$;
select {index} as staged_chunk;
commit;"""


def publish_sql(plan, actor, *, simulate_failure=False):
    """All persistent masters + batches + lineage + facts commit or roll back together."""
    bid = seal(plan)
    expected = len(plan["observations"])
    failure = "raise exception 'controlled_publication_failure';" if simulate_failure else ""
    return f"""begin;
set local statement_timeout='180s';
do $bootstrap$
declare j public.jobs%rowtype; m jsonb; x jsonb; batch uuid; scope uuid;
begin
 {actor_guard(actor)}
 perform pg_advisory_xact_lock(hashtextextended('{bid}',0));
 select * into j from public.jobs where id='{bid}'::uuid for update;
 if not found or j.job_type<>{literal(BOOTSTRAP_KIND)} or j.requested_by<>'{actor}'::uuid
    or j.metadata_json->'master'->>'plan_sha'<>{literal(plan['plan_sha'])} then
   raise exception 'bootstrap_manifest_not_attested'; end if;
 if j.status='COMPLETED' then return; end if;
 if j.status<>'STAGING' then raise exception 'bootstrap_not_staging'; end if;
 m := j.metadata_json->'master';
 if (select count(*) from public.run_logs where job_id=j.id and step like 'CERTIFIED_STAGE_%')
    <>jsonb_array_length(j.metadata_json->'chunk_hashes') then raise exception 'staging_incomplete'; end if;
 if exists(select 1 from public.run_logs l where l.job_id=j.id and l.step like 'CERTIFIED_STAGE_%' and
    (l.metadata_json->>'sha256'<>j.metadata_json->'chunk_hashes'->>(l.metadata_json->>'index')::int
     or encode(sha256(convert_to(l.metadata_json->>'payload_text','UTF8')),'hex')<>l.metadata_json->>'sha256')) then
   raise exception 'staging_hash_mismatch'; end if;
 create temporary table bootstrap_facts on commit drop as
 select r.* from public.run_logs l cross join lateral
 jsonb_to_recordset((l.metadata_json->>'payload_text')::jsonb) as r(
  id uuid,chain_id uuid,product_id uuid,period date,metric_code text,value numeric(18,3),version_no integer,
  available_at timestamptz,availability_source text,source_batch_id uuid,availability_evidence_id uuid,lineage jsonb)
 where l.job_id=j.id and l.step like 'CERTIFIED_STAGE_%';
 alter table bootstrap_facts add primary key(id);
 if (select count(*) from bootstrap_facts)<>{expected} or
    (select count(distinct (chain_id,product_id,period,metric_code)) from bootstrap_facts)<>{expected} or
    exists(select 1 from bootstrap_facts where availability_source<>'UNKNOWN' or available_at is not null) then
   raise exception 'staging_fact_count_grain_or_temporal_invalid'; end if;
 -- Conflicts are reviewed, never arbitrary ON CONFLICT winners. New masters
 -- are atomic; exact already-published reruns return above and undergo readback.
 for x in select value from jsonb_array_elements(m->'scopes') loop
   if exists(select 1 from public.chains where id=(x->>'chain_uuid')::uuid or code=x->>'sql_code') then
     raise exception 'existing_scope_requires_explicit_reconciliation'; end if;
   insert into public.chains(id,code,name,status)
   values ((x->>'chain_uuid')::uuid,x->>'sql_code',x->>'code','ACTIVE');
 end loop;
 for x in select value from jsonb_array_elements(m->'categories') loop
   insert into public.categories(id,chain_id,code,name)
   values ((x->>'id')::uuid,(x->>'chain_id')::uuid,x->>'code',x->>'name');
 end loop;
 for x in select value from jsonb_array_elements(m->'batches') loop
   batch := (x->>'id')::uuid; scope := (x->>'chain_id')::uuid;
   insert into public.import_profiles(id,chain_id,name) values ((x->>'profile_id')::uuid,scope,
     {literal(BOOTSTRAP_KIND)}||':'||j.id::text);
   insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
   values ((x->>'profile_version_id')::uuid,(x->>'profile_id')::uuid,scope,1,
     jsonb_build_object('bootstrap_id',j.id,'manifest',m->'manifest','scope',
       (select value from jsonb_array_elements(m->'scopes') where value->>'chain_uuid'=scope::text),
       'products',(select jsonb_agg(value) from jsonb_array_elements(m->'products') where value->>'chain_id'=scope::text)),
     '["scope","product_code","period","metric","value","lineage"]'::jsonb,'{actor}'::uuid);
   insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_at,uploaded_by,
     row_count,valid_rows,rejected_rows,status)
   values (batch,scope,(x->>'profile_version_id')::uuid,x->>'filename',x->>'sha256',(x->>'period')::date,
     (m->'manifest'->>'bootstrap_at')::timestamptz,'{actor}'::uuid,(x->>'row_count')::int,(x->>'row_count')::int,0,'UPLOADED');
   update public.import_batches set status='VALIDATING' where id=batch;
   update public.import_batches set status='VALIDATED' where id=batch;
   update public.import_batches set status='CONFIRMED' where id=batch;
 end loop;
 for x in select value from jsonb_array_elements(m->'products') loop
   insert into public.products(id,chain_id,category_id,product_code,variant_code,description,status,
     first_seen_period,last_seen_period,created_from_batch_id)
   values ((x->>'id')::uuid,(x->>'chain_id')::uuid,(x->>'category_id')::uuid,x->>'product_code',x->>'variant_code',
     x->>'description','ACTIVE',(x->>'first_seen_period')::date,(x->>'last_seen_period')::date,
     (select (value->>'id')::uuid from jsonb_array_elements(m->'batches') where value->>'chain_id'=x->>'chain_id'));
 end loop;
 insert into public.source_evidence(id,chain_id,legacy_id,evidence_level,evidence_type,source_name,source_hash,description)
 select availability_evidence_id,chain_id,j.id::text||':'||id::text,'UNKNOWN','CERTIFIED_HISTORICAL_LITERAL',
   'sha256:'||(lineage->'canonical_fact'->>'source_sha256'),lineage->'canonical_fact'->>'source_sha256',lineage::text
 from bootstrap_facts;
 insert into public.monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,
   available_at,availability_source,source_batch_id,availability_evidence_id)
 select id,chain_id,product_id,period,metric_code,value,version_no,null,'UNKNOWN',source_batch_id,availability_evidence_id
 from bootstrap_facts;
 for x in select value from jsonb_array_elements(m->'batches') loop
   update public.import_batches set status='IMPORTED' where id=(x->>'id')::uuid;
 end loop;
 -- Validate actual joined database rows BEFORE commit, not just the staged count.
 if (select count(*) from public.monthly_observations o join bootstrap_facts b on b.id=o.id)<>{expected}
    or exists(select 1 from bootstrap_facts b left join public.monthly_observations o on o.id=b.id
      left join public.products p on p.id=o.product_id left join public.chains c on c.id=o.chain_id
      left join public.source_evidence e on e.id=o.availability_evidence_id
      left join public.import_batches ib on ib.id=o.source_batch_id
      where o.id is null or row(o.chain_id,o.product_id,o.period,o.metric_code,o.value,o.version_no,o.source_batch_id,o.availability_evidence_id)
        is distinct from row(b.chain_id,b.product_id,b.period,b.metric_code,b.value,b.version_no,b.source_batch_id,b.availability_evidence_id)
        or o.available_at is not null or o.availability_source<>'UNKNOWN' or ib.status<>'IMPORTED'
        or e.description::jsonb<>b.lineage or e.source_hash<>b.lineage->'canonical_fact'->>'source_sha256'
        or p.product_code<>b.lineage->'canonical_fact'->'key'->>1
        or c.code<>upper(b.lineage->'canonical_fact'->'key'->>0)) then
   raise exception 'remote_business_readback_mismatch_before_commit'; end if;
 {failure}
 update public.jobs set status='COMPLETED',finished_at=now() where id=j.id;
 insert into public.audit_log(actor_id,action,entity_type,entity_id,new_data,request_id)
 values ('{actor}'::uuid,'CERTIFIED_BOOTSTRAP_COMPLETED','jobs',j.id,
   jsonb_build_object('manifest',m->'manifest','started_at',j.started_at,'completed_at',now(),'counts',
   jsonb_build_object('observations',{expected},'scopes',jsonb_array_length(m->'batches'),
   'scope_masters',jsonb_array_length(m->'scopes'),'products',jsonb_array_length(m->'products'),
   'categories',jsonb_array_length(m->'categories')),'result','PASS_ATOMIC_PUBLICATION'),j.id::text);
end $bootstrap$;
select id,status from public.jobs where id='{bid}'::uuid;
commit;"""


def readback_sql(plan, *, offset=0, limit=None):
    bid = seal(plan)
    if not isinstance(offset, int) or offset < 0 or (limit is not None and (not isinstance(limit, int) or limit < 1)):
        raise ValueError("invalid_readback_page")
    page = f" limit {limit} offset {offset}" if limit else ""
    return f"""begin transaction read only;
select o.id,o.chain_id,o.product_id,to_char(o.period,'YYYY-MM-DD') as period,o.metric_code,o.value::text as value,
 o.version_no,o.available_at,o.availability_source,o.source_batch_id,o.availability_evidence_id,
 c.code as scope_code,p.product_code,p.variant_code,e.source_hash as evidence_source_hash,
 ib.status as batch_status,e.description::jsonb as lineage
from public.monthly_observations o join public.products p on p.id=o.product_id
join public.chains c on c.id=o.chain_id join public.source_evidence e on e.id=o.availability_evidence_id
join public.import_batches ib on ib.id=o.source_batch_id
join public.import_profile_versions pv on pv.id=ib.profile_version_id
where pv.mapping_json->>'bootstrap_id'='{bid}' order by o.id{page};
commit;"""


def inventory_sql():
    return """begin transaction read only;
select jsonb_build_object(
 'migrations',(select coalesce(jsonb_agg(version order by version),'[]'::jsonb) from supabase_migrations.schema_migrations),
 'tables',(select jsonb_agg(jsonb_build_object('name',c.relname,'rls_enabled',c.relrowsecurity) order by c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'),
 'views',(select coalesce(jsonb_agg(table_name order by table_name),'[]'::jsonb) from information_schema.views where table_schema='public'),
 'buckets',(select coalesce(jsonb_agg(jsonb_build_object('id',id,'public',public) order by id),'[]'::jsonb) from storage.buckets),
 'counts',jsonb_build_object('chains',(select count(*) from public.chains),'categories',(select count(*) from public.categories),'products',(select count(*) from public.products),'monthly_observations',(select count(*) from public.monthly_observations),'import_batches',(select count(*) from public.import_batches),'source_evidence',(select count(*) from public.source_evidence)),
 'admin_actors',(select coalesce(jsonb_agg(p.id order by p.id),'[]'::jsonb) from public.profiles p join auth.users u on u.id=p.id where p.status='ACTIVE' and p.global_role='ADMIN'),
 'existing_bootstraps',(select coalesce(jsonb_agg(jsonb_build_object('id',id,'status',status,'metadata_json',metadata_json) order by requested_at),'[]'::jsonb) from public.jobs where job_type='HISTORICAL_CERTIFIED_BOOTSTRAP_V1')
) as snapshot;
commit;"""


def measurement_sql(plan):
    """Independent compact business/lineage checksum for exact rerun comparison."""
    bid = seal(plan)
    return f"""begin transaction read only;
select count(*) as observations,encode(sha256(convert_to(string_agg(
 jsonb_build_object('scope',c.code,'product',p.product_code,'variant',p.variant_code,
 'period',o.period,'metric',o.metric_code,'value',o.value,'version',o.version_no,
 'available_at',o.available_at,'availability_source',o.availability_source,
 'canonical_fact',e.description::jsonb->'canonical_fact')::text,E'\\n'
 order by c.code,p.product_code,p.variant_code,o.period,o.metric_code,o.version_no),'UTF8')),'hex') as business_lineage_sha
from public.monthly_observations o join public.products p on p.id=o.product_id
join public.chains c on c.id=o.chain_id join public.source_evidence e on e.id=o.availability_evidence_id
join public.import_batches ib on ib.id=o.source_batch_id join public.import_profile_versions pv on pv.id=ib.profile_version_id
where pv.mapping_json->>'bootstrap_id'='{bid}';
commit;"""


def security_probe_sql(plan, actor):
    """Real RLS/GRANT evaluation with the real actor, entirely rolled back.

    Transaction-local viewer/editor identities never become permanent users or
    roles. No policy change, no RLS bypass, no Storage file upload.
    """
    seal(plan)
    if len(plan["batches"]) < 2:
        raise ValueError("two_scopes_required_for_cross_chain_probe")
    actor_guard(actor)
    a, b = plan["batches"][:2]
    a_id, b_id = a["chain_id"], b["chain_id"]
    probe_id = uid("security-category", plan["manifest"]["bootstrap_id"])
    return f"""begin;
create temporary table bootstrap_security_results(name text primary key,ok boolean) on commit drop;
grant insert,select on bootstrap_security_results to authenticated;
do $security$ declare n bigint; begin
 {actor_guard(actor)}
 if exists(select 1 from public.user_chain_access where user_id='{actor}'::uuid) then
   raise exception 'existing_actor_grants_require_reviewed_probe'; end if;
 update public.profiles set global_role='VIEWER' where id='{actor}'::uuid;
 insert into public.user_chain_access(user_id,chain_id,can_view,can_edit,can_import,can_run_forecast)
 values ('{actor}'::uuid,'{a_id}'::uuid,true,true,true,true);
 perform set_config('request.jwt.claim.sub','{actor}',true);
 set local role authenticated;
 insert into bootstrap_security_results values
 ('viewer_reads_authorized_scope',(select count(*) from public.monthly_observations)={a['row_count']}),
 ('viewer_cross_chain_hidden',(select count(*) from public.monthly_observations where chain_id='{b_id}'::uuid)=0),
 ('viewer_chain_isolation',(select count(*) from public.chains)=1);
 begin
   insert into public.categories(id,chain_id,code,name) values ('{probe_id}'::uuid,'{a_id}'::uuid,'SECURITY_PROBE','Security probe');
   insert into bootstrap_security_results values ('viewer_write_denied',false);
 exception when insufficient_privilege then
   insert into bootstrap_security_results values ('viewer_write_denied',true);
 end;
 update public.profiles set global_role='ADMIN' where id='{actor}'::uuid;
 get diagnostics n=row_count;
 insert into bootstrap_security_results values ('self_role_escalation_denied',n=0);
 reset role;
 update public.profiles set global_role='EDITOR' where id='{actor}'::uuid;
 set local role authenticated;
 update public.products set description=description||' RLS_ROLLBACK_PROBE' where chain_id='{a_id}'::uuid;
 get diagnostics n=row_count;
 insert into bootstrap_security_results values ('editor_authorized_update',n>0);
 update public.products set description=description||' FORBIDDEN_PROBE' where chain_id='{b_id}'::uuid;
 get diagnostics n=row_count;
 insert into bootstrap_security_results values ('editor_cross_chain_write_denied',n=0);
 begin
   insert into public.categories(id,chain_id,code,name) values ('{probe_id}'::uuid,'{b_id}'::uuid,'SECURITY_PROBE','Security probe');
   insert into bootstrap_security_results values ('editor_cross_chain_insert_denied',false);
 exception when insufficient_privilege then
   insert into bootstrap_security_results values ('editor_cross_chain_insert_denied',true);
 end;
 reset role;
 update public.profiles set global_role='ADMIN' where id='{actor}'::uuid;
 set local role authenticated;
 insert into bootstrap_security_results values
 ('admin_all_scopes',(select count(*) from public.monthly_observations)={len(plan['observations'])}),
 ('admin_permission_verified',private.is_active_admin());
 reset role;
 if exists(select 1 from bootstrap_security_results where not ok) then raise exception 'security_probe_failed'; end if;
end $security$;
select name,ok from bootstrap_security_results order by name;
rollback;"""


def cleanup_sql(plan, actor, *, certification_failed=False):
    """One-off admin withdrawal ONLY after failed final certification.

    Append-only guards are restored within the same transaction, under exclusive
    locks. FK checks stay active, RLS stays enabled, audit/staging stay immutable.
    Any unrelated reference aborts the ENTIRE withdrawal including trigger DDL.
    This is never called as part of successful publication or a normal retry.
    """
    if not certification_failed:
        raise ValueError("failed_final_certification_required_for_cleanup")
    bid = seal(plan)
    return f"""begin;
do $withdraw$ declare j public.jobs%rowtype; m jsonb; begin
 {actor_guard(actor)}
 perform pg_advisory_xact_lock(hashtextextended('{bid}',0));
 select * into j from public.jobs where id='{bid}'::uuid for update;
 if not found or j.job_type<>{literal(BOOTSTRAP_KIND)} or j.requested_by<>'{actor}'::uuid
    or j.metadata_json->'master'->>'plan_sha'<>{literal(plan['plan_sha'])} or j.status<>'COMPLETED' then
   raise exception 'withdrawal_ownership_not_attested'; end if;
 m := j.metadata_json->'master';
 lock table public.monthly_observations,public.source_evidence,public.import_batches,
   public.import_profile_versions,public.products,public.categories,public.import_profiles,public.chains
   in access exclusive mode;
 create temporary table withdrawal_ids on commit drop as
 select (r->>'id')::uuid as id,(r->>'availability_evidence_id')::uuid as evidence_id
 from public.run_logs l cross join lateral jsonb_array_elements((l.metadata_json->>'payload_text')::jsonb) r
 where l.job_id=j.id and l.step like 'CERTIFIED_STAGE_%';
 alter table withdrawal_ids add primary key(id);
 if (select count(*) from withdrawal_ids)<>{len(plan['observations'])} or
   exists(select 1 from public.monthly_observations o where o.source_batch_id in
      (select (value->>'id')::uuid from jsonb_array_elements(m->'batches')) and o.id not in (select id from withdrawal_ids)) then
   raise exception 'withdrawal_contains_unrelated_observations'; end if;
 -- Only four named DELETE guards; no global trigger or RLS bypass.
 alter table public.monthly_observations disable trigger observations_insert_only;
 alter table public.source_evidence disable trigger source_evidence_insert_only;
 alter table public.import_batches disable trigger import_batch_no_delete;
 alter table public.import_profile_versions disable trigger import_profile_versions_insert_only;
 delete from public.monthly_observations where id in (select id from withdrawal_ids) and source_batch_id in
   (select (value->>'id')::uuid from jsonb_array_elements(m->'batches'));
 delete from public.source_evidence where id in (select evidence_id from withdrawal_ids) and legacy_id like j.id::text||':%';
 delete from public.products where id in (select (value->>'id')::uuid from jsonb_array_elements(m->'products'))
   and created_from_batch_id in (select (value->>'id')::uuid from jsonb_array_elements(m->'batches'));
 delete from public.import_batches where id in (select (value->>'id')::uuid from jsonb_array_elements(m->'batches'));
 delete from public.import_profile_versions where id in
   (select (value->>'profile_version_id')::uuid from jsonb_array_elements(m->'batches')) and mapping_json->>'bootstrap_id'=j.id::text;
 delete from public.import_profiles where id in (select (value->>'profile_id')::uuid from jsonb_array_elements(m->'batches'));
 delete from public.categories where id in (select (value->>'id')::uuid from jsonb_array_elements(m->'categories'));
 delete from public.chains where id in (select (value->>'chain_uuid')::uuid from jsonb_array_elements(m->'scopes'));
 alter table public.monthly_observations enable trigger observations_insert_only;
 alter table public.source_evidence enable trigger source_evidence_insert_only;
 alter table public.import_batches enable trigger import_batch_no_delete;
 alter table public.import_profile_versions enable trigger import_profile_versions_insert_only;
 update public.jobs set status='RETIRED_FAILED_CERTIFICATION' where id=j.id;
 insert into public.audit_log(actor_id,action,entity_type,entity_id,old_data,new_data,request_id)
 values ('{actor}'::uuid,'CERTIFIED_BOOTSTRAP_SCOPED_WITHDRAWAL','jobs',j.id,m->'manifest',
   jsonb_build_object('result','RETIRED_FAILED_CERTIFICATION','unrelated_deletes',0,'lineage_retained_in_staging',true),j.id::text);
end $withdraw$;
commit;"""
