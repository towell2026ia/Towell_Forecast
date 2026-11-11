"""Prepare an exact, separately authorized retirement script on the isolated mirror."""
from __future__ import annotations

from collections import defaultdict

from psycopg import sql

from .local import digest

AB_TABLES = (
    "private.operational_audit_baseline", "private.operational_source_lineage",
    "public.operational_data_version_inventory", "public.operational_data_version_observations",
    "public.operational_data_active", "public.operational_data_versions",
)


def _relation(name):
    return sql.Identifier(*name.split("."))


def _ids(c, relation, predicate, params=()):
    return {str(r["id"]) for r in c.execute(sql.SQL("select id from {} where ").format(_relation(relation)) + predicate, params)}


def _fingerprint(c, relation, column, ids):
    return c.execute(sql.SQL("""
        select encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' order by t.{}),''),'UTF8')),'hex') h
        from {} t where t.{}=any(%s::uuid[])
    """).format(sql.Identifier(column), _relation(relation), sql.Identifier(column)), (sorted(ids),)).fetchone()["h"]


def retirement_plan(lab, c):
    lab.checked(c)
    lab._official()
    c.execute("set timezone='UTC'")
    active = c.execute("select source_hash,generation from public.official_source_current where singleton").fetchone()
    if not active:
        raise ValueError("official_current_source_required")
    targets = defaultdict(set)
    targets["public.monthly_observations"] = _ids(c, "public.monthly_observations", sql.SQL(
        "not private.official_observation_current(id)"))
    targets["public.monthly_inventory"] = _ids(c, "public.monthly_inventory", sql.SQL(
        "source_batch_id in(select id from public.import_batches where official_source_hash is distinct from %s)"),
        (active["source_hash"],))
    targets["public.forecast_runs"] = _ids(c, "public.forecast_runs", sql.SQL("not private.official_result_current(source_context)"))
    if c.execute("select to_regclass('public.forecast_calculations') t").fetchone()["t"]:
        targets["public.forecast_calculations"] = _ids(c, "public.forecast_calculations",
            sql.SQL("not private.official_result_current(input_snapshot->'source_context')"))
    targets["public.customer_forecast_versions"] = _ids(c, "public.customer_forecast_versions", sql.SQL("true"))
    targets["public.import_batches"] = _ids(c, "public.import_batches", sql.SQL(
        "official_source_hash is distinct from %s and not exists(select 1 from public.products p where p.created_from_batch_id=import_batches.id)"),
        (active["source_hash"],))
    targets["public.source_evidence"] = _ids(c, "public.source_evidence", sql.SQL(
        "id<>all((select evidence_ids from public.official_source_current where singleton)::uuid[])"
        " and not exists(select 1 from public.import_batches b where b.evidence_id=source_evidence.id and b.id<>all(%s::uuid[]))"),
        (sorted(targets["public.import_batches"]),))
    primary = {r["relation"]: r["column"] for r in c.execute("""
        select n.nspname||'.'||r.relname relation,a.attname as column
        from pg_constraint k join pg_class r on r.oid=k.conrelid
        join pg_namespace n on n.oid=r.relnamespace
        join pg_attribute a on a.attrelid=r.oid and a.attnum=k.conkey[1]
        where k.contype='p' and cardinality(k.conkey)=1 and a.atttypid='uuid'::regtype
          and n.nspname in ('public','private')
    """)}
    foreign = c.execute("""
        select k.conname,n.nspname||'.'||r.relname child,pn.nspname||'.'||pr.relname parent,
          array(select a.attname from unnest(k.conkey) with ordinality s(num,ord)
            join pg_attribute a on a.attrelid=r.oid and a.attnum=s.num order by s.ord) child_columns,
          array(select a.attname from unnest(k.confkey) with ordinality s(num,ord)
            join pg_attribute a on a.attrelid=pr.oid and a.attnum=s.num order by s.ord) parent_columns
        from pg_constraint k join pg_class r on r.oid=k.conrelid join pg_namespace n on n.oid=r.relnamespace
        join pg_class pr on pr.oid=k.confrelid join pg_namespace pn on pn.oid=pr.relnamespace
        where k.contype='f' and n.nspname in ('public','private') and pn.nspname in ('public','private')
    """).fetchall()
    changed = True
    while changed:
        changed = False
        for edge in foreign:
            parent, child = edge["parent"], edge["child"]
            if not targets[parent] or child in AB_TABLES:
                continue
            if child not in primary or parent not in primary:
                raise ValueError("retirement_foreign_key_requires_review:" + edge["conname"])
            join = sql.SQL(" and ").join(sql.SQL("child.{}=parent.{}").format(sql.Identifier(a),sql.Identifier(b))
                                       for a,b in zip(edge["child_columns"],edge["parent_columns"],strict=True))
            found = c.execute(sql.SQL("select child.{} id from {} child join {} parent on ").format(
                sql.Identifier(primary[child]),_relation(child),_relation(parent)) + join
                + sql.SQL(" where parent.{}=any(%s::uuid[])").format(sql.Identifier(primary[parent])),
                (sorted(targets[parent]),)).fetchall()
            newly = {str(r["id"]) for r in found} - targets[child]
            if newly and child in {"public.products","public.chains","public.profiles"}:
                raise ValueError("retirement_would_remove_catalog:" + child)
            targets[child].update(newly)
            changed = changed or bool(newly)
    targets = {name: ids for name,ids in targets.items() if ids}
    for table, predicate in (
        ("public.forecast_runs",sql.SQL("private.official_result_current(source_context)")),
        ("public.forecast_calculations",sql.SQL("private.official_result_current(input_snapshot->'source_context')")),
        ("public.forecast_run_inputs",sql.SQL("private.official_run_current(run_id)")),
        ("public.forecast_live_evaluations",sql.SQL("private.official_calculation_current(calculation_id)")),
    ):
        if table in targets and targets[table] & _ids(c,table,predicate):
            raise ValueError("retirement_references_current_result:" + table)
    dependencies = {name: set() for name in targets}
    for edge in foreign:
        child,parent = edge["child"],edge["parent"]
        if child in targets and parent in targets and child!=parent:
            dependencies[parent].add(child)
    ordered = []
    while dependencies:
        ready = sorted(name for name,children in dependencies.items() if not children)
        if not ready:
            raise ValueError("retirement_dependency_cycle_requires_review")
        ordered.extend(ready)
        for name in ready:
            del dependencies[name]
        for children in dependencies.values():
            children.difference_update(ready)
    legacy_functions = [r["signature"] for r in c.execute("""
        select p.oid::regprocedure::text signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='private' and (
          p.proname ~ '^(gateb_|unification_)'
          or p.proname in ('operational_assert_pin','operational_payload_before_lineage',
            'operational_validate_source_lineage','operational_validate_archive_seal')
        ) order by p.proname
    """)]
    legacy_triggers = c.execute("""
        select n.nspname||'.'||r.relname relation,t.tgname name
        from pg_trigger t join pg_proc p on p.oid=t.tgfoid join pg_namespace f on f.oid=p.pronamespace
        join pg_class r on r.oid=t.tgrelid join pg_namespace n on n.oid=r.relnamespace
        where not t.tgisinternal and f.nspname='private' and (
          p.proname ~ '^(gateb_|unification_)'
          or p.proname in ('operational_validate_source_lineage','operational_validate_archive_seal')
        )
    """).fetchall()
    anchors = c.execute("""select count(*) n from public.import_batches b where b.official_source_hash is distinct from %s
        and exists(select 1 from public.products p where p.created_from_batch_id=b.id)""",(active["source_hash"],)).fetchone()["n"]
    legacy_policies = c.execute("""select schemaname||'.'||tablename relation,policyname name
        from pg_policies where schemaname||'.'||tablename=any(%s::text[])
        order by schemaname,tablename,policyname""",(list(AB_TABLES),)).fetchall()
    plan = {"source_context":dict(active),"retained_catalog_creation_batches":anchors,
            "delete_order":ordered,"rows":{name:{"ids":sorted(targets[name]),"key":primary[name],
                "sha256":_fingerprint(c,name,primary[name],targets[name])} for name in ordered},
            "foreign_keys":[dict(edge) for edge in foreign if edge["parent"] in targets or edge["child"] in targets],
            "retired_tables":[name for name in AB_TABLES if c.execute("select to_regclass(%s) t",(name,)).fetchone()["t"]],
            "retired_functions":legacy_functions,"retired_triggers":[dict(r) for r in legacy_triggers],
            "retired_policies":[dict(r) for r in legacy_policies]}
    return {**plan,"plan_sha256":digest(plan)}


def render_cleanup(plan):
    """Return explicit SQL whose authorization is bound to the reviewed row graph."""
    body = {k:v for k,v in plan.items() if k!="plan_sha256"}
    if digest(body)!=plan["plan_sha256"]:
        raise ValueError("retirement_plan_sha_mismatch")
    context = plan["source_context"]
    authorization = "DESTROY_RETIRED_SOURCE_DATA:"+plan["plan_sha256"]
    script = [
        "-- Prepared destructive operation. Requires separate authorization for this exact report.",
        "begin;",
        "set local timezone='UTC';",
    ]
    guard = sql.SQL("""do $$ begin
          if current_setting('towell.official_cleanup_authorization',true) is distinct from {}
             or current_setting('towell.official_database',true) is distinct from current_database()
             or session_user is distinct from (select pg_get_userbyid(relowner)
                  from pg_class where oid='public.monthly_observations'::regclass)
             or not exists(select 1 from public.official_source_current
               where singleton and source_hash={} and generation={})
          then raise exception 'OFFICIAL_CLEANUP_AUTHORIZATION_REQUIRED'; end if;
        end $$;""").format(sql.Literal(authorization),sql.Literal(context["source_hash"]),sql.Literal(context["generation"])).as_string()
    locked = sorted(set(plan["delete_order"]) | {"public.official_source_current","public.products"})
    script.append((sql.SQL("lock table ")+sql.SQL(",").join(_relation(name) for name in locked)
                   +sql.SQL(" in access exclusive mode;")).as_string())
    script.append(guard)
    for item in plan["retired_triggers"]:
        script.append(sql.SQL("drop trigger {} on {};").format(sql.Identifier(item["name"]),_relation(item["relation"])).as_string())
    for item in plan["retired_policies"]:
        script.append(sql.SQL("drop policy {} on {};").format(sql.Identifier(item["name"]),_relation(item["relation"])).as_string())
    for signature in plan["retired_functions"]:
        # Signatures came from PostgreSQL regprocedure formatting, never user input.
        script.append("drop function "+signature+";")
    for relation in plan["retired_tables"]:
        script.append(sql.SQL("drop table {};").format(_relation(relation)).as_string())
    for relation in plan["delete_order"]:
        row = plan["rows"][relation]
        ids = sql.SQL("array[")+sql.SQL(",").join(sql.Literal(value) for value in row["ids"])+sql.SQL("]::uuid[]")
        script.append(sql.SQL("""do $$ begin if (
            select encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' order by t.{}),''),'UTF8')),'hex')
            from {} t where t.{}=any({})
          ) is distinct from {} then raise exception 'OFFICIAL_CLEANUP_DATA_CHANGED'; end if; end $$;""").format(
            sql.Identifier(row["key"]),_relation(relation),sql.Identifier(row["key"]),ids,sql.Literal(row["sha256"])).as_string())
        script.append(sql.SQL("alter table {} disable trigger user;").format(_relation(relation)).as_string())
        script.append(sql.SQL("delete from {} where {}=any({});").format(_relation(relation),sql.Identifier(row["key"]),ids).as_string())
        script.append(sql.SQL("alter table {} enable trigger user;").format(_relation(relation)).as_string())
    script.append("commit;")
    return "\n".join(script)+"\n"
