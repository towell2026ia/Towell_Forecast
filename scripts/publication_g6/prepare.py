"""Produce private, resumable staging SQL from a sealed G6 export. No network or calculation."""
import argparse
import base64
import hashlib
import json
import zipfile
from pathlib import Path
from uuid import UUID


RESOURCES = ("chains", "categories", "products", "import_profile_versions", "observations",
             "inventory", "fulfillment", "catalog", "calculations", "horizons")
GLOBAL_FIELDS = ("schema", "run_id", "objective", "cutoff", "observed_cutoff", "model_cutoff",
                 "planning_year", "planning_basis", "planning_distances", "forecast_distances",
                 "emitted_at", "engine_version", "method_selection_version", "content_digest",
                 "training_fingerprint", "observed_training_fingerprint", "source_context",
                 "evaluation_mode", "champion_published")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def literal(value):
    # PostgreSQL quotes data, never interprets workbook text as part of a statement.
    tag = "$g" + digest(value.encode())[:20] + "$"
    if tag in value:
        raise ValueError("SQL data delimiter collision")
    return tag + value + tag


def record(resource, line, periods):
    row = json.loads(line)
    chain = row["id"] if resource == "chains" else row["chain_id"]
    product = row.get("product_id") or (row["id"] if resource == "products" else None)
    period = row.get("period") or row.get("target_period")
    if resource in ("inventory", "fulfillment"):
        key = compact([chain, product, period, row["format"], row["upc"]])
    elif resource == "catalog":
        key = compact([chain, product])
    else:
        key = row["id"]
    if resource == "observations" and row["metric_code"] in ("SALES", "ORDER", "DELIVERY"):
        periods.add((chain, period))
    index = dict(record_key=key, chain_id=str(UUID(chain)), product_id=str(UUID(product)) if product else None, period=period)
    return compact(index)[:-1] + ',"payload":' + line + "}"


def stage_sql(run_id, resource, batch, lines):
    raw = "[" + ",".join(lines) + "]"
    checksum = digest(raw.encode())
    if resource == "scopes":
        insert = """insert into private.ft_g6_scope(run_id,chain_id)
          select rid,(v->>'id')::uuid from jsonb_array_elements(body::jsonb) v"""
    elif resource == "sealed_products":
        insert = """insert into private.ft_g6_product(run_id,chain_id,product_id,payload)
          select rid,(v->>'chain_id')::uuid,(v->>'product_id')::uuid,v from jsonb_array_elements(body::jsonb) v"""
    elif resource == "archive":
        insert = """insert into private.ft_g6_archive(run_id,part,data,sha256)
          select rid,(v->>'part')::integer,decode(v->>'data','base64'),v->>'sha256'
          from jsonb_array_elements(body::jsonb) v"""
    else:
        insert = """insert into private.ft_g6_row(run_id,resource,record_key,chain_id,product_id,period,payload)
          select rid,res,v.record_key,v.chain_id,v.product_id,v.period,v.payload from jsonb_to_recordset(body::jsonb)
          as v(record_key text,chain_id uuid,product_id uuid,period text,payload jsonb)"""
    # Generate the outer DO body with a second data-dependent delimiter as well.
    block = f"""declare rid uuid:='{UUID(run_id)}'; res text:={literal(resource)}; n integer:={batch};
      expected text:='{checksum}'; body text:={literal(raw)}; existing private.ft_g6_receipt%rowtype; cnt integer;
    begin
      perform pg_advisory_xact_lock(hashtextextended(rid::text||res||n::text,0));
      select * into existing from private.ft_g6_receipt where run_id=rid and resource=res and batch_no=n;
      if found then
        if existing.content_sha256<>expected or existing.row_count<>{len(lines)} then raise exception 'Receipt mismatch'; end if;
        return;
      end if;
      if (select active from private.ft_g6_release where run_id=rid for share) is distinct from false
        then raise exception 'Release is missing or already published'; end if;
      if encode(sha256(convert_to(body,'UTF8')),'hex')<>expected then raise exception 'Payload digest mismatch'; end if;
      {insert};
      get diagnostics cnt=row_count;
      if cnt<>{len(lines)} then raise exception 'Incomplete batch'; end if;
      insert into private.ft_g6_receipt values(rid,res,n,expected,cnt);
    end"""
    return "do " + literal(block) + ";\n"


def prepare(archive, snapshot, output, archive_sha, manifest_sha):
    archive_bytes = archive.read_bytes()
    mbytes = (snapshot / "Manifest.json").read_bytes()
    if digest(archive_bytes) != archive_sha or digest(mbytes) != manifest_sha:
        raise ValueError("Pinned archive or database manifest digest mismatch")
    manifest = json.loads(mbytes)
    indexed = {f["path"]: f for f in manifest["files"]}
    for name, entry in indexed.items():
        path = (snapshot / name).resolve()
        if not path.is_relative_to(snapshot.resolve()) or digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError(f"Invalid snapshot file: {name}")
    context = json.loads((snapshot / "source_context.json").read_bytes())
    with zipfile.ZipFile(archive) as z:
        runbytes = z.read("planning/planning_run.json")
        if digest(runbytes) != manifest["run_file_sha256"] or z.testzip() is not None:
            raise ValueError("Invalid sealed planning run")
        pinned_context = json.loads(z.read("metadata/source_context.json"))
        if (any(context.get(k) != v or k not in context for k, v in pinned_context.items())
                or any(k != "forecast_unavailable_reason" or context[k] is not None
                       for k in context.keys() - pinned_context.keys())):
            raise ValueError("Mismatched source contexts")
    run = json.loads(runbytes)
    rid = str(UUID(run["run_id"]))
    content = {k: v for k,v in run.items() if k != "content_digest"}
    if (rid != manifest["run_id"] or digest(compact(content).encode()) != manifest["content_digest"]
            or run["content_digest"] != manifest["content_digest"] or manifest["generation"] != 6
            or context["source_hash"] != manifest["source_hash"] or context["scenario_hash"] != manifest["scenario_hash"]):
        raise ValueError("Sealed planning run does not match SQL snapshot")
    if any(manifest["sql_decisions"].values()) or any(manifest["sqlite_decisions"].values()):
        raise ValueError("Existing decisions need a separately reconciled import")
    output.mkdir(parents=True, exist_ok=False)
    header = {k:run[k] for k in GLOBAL_FIELDS}
    header["file_sha256"] = manifest["run_file_sha256"]
    values = ",".join(literal(s) for s in (rid, compact(context), compact(header), manifest["run_file_sha256"], run["content_digest"]))
    code = f"""insert into private.ft_g6_release(run_id,context,metadata,file_sha256,content_digest)
      values ({values}) on conflict(run_id) do nothing;
      do $$begin if not exists(select 1 from private.ft_g6_release where run_id='{rid}' and
      content_digest='{run['content_digest']}' and file_sha256='{manifest['run_file_sha256']}'
      and context={literal(compact(context))}::jsonb and metadata={literal(compact(header))}::jsonb)
      then raise exception 'Release identity conflict'; end if; end$$;"""
    (output/"000_release.sql").write_text("begin;\n"+code+"\ncommit;\n",encoding="utf-8")
    receipts = []
    def emit(resource, rows):
        batch, size, items = 0, 0, []
        def write():
            sql = stage_sql(rid, resource, batch, items)
            name = f"{resource}_{batch:04d}.sql"
            payload = sql.encode()
            (output/name).write_bytes(payload)
            receipts.append(dict(file=name, sha256=digest(payload), resource=resource, rows=len(items)))
        for line in rows:
            length = len(line.encode())
            if items and size+length>760_000:
                write()
                batch, items, size = batch+1, [], 0
            items.append(line)
            size += length+1
        if items:
            write()
    with (snapshot/"resources/chains.jsonl").open(encoding="utf-8") as stream:
        emit("scopes", (compact({"id":str(UUID(json.loads(line)["id"]))}) for line in stream))
    emit("sealed_products", (compact(p) for p in run["products"]))
    periods = set()
    for resource in RESOURCES:
        with (snapshot/"resources"/(resource+".jsonl")).open(encoding="utf-8") as stream:
            emit(resource,(record(resource,line.strip(),periods) for line in stream if line.strip()))
    with (snapshot/"resources/periods.jsonl").open(encoding="utf-8") as stream:
        exported_periods = {json.loads(line)["period"] for line in stream}
    if {p for _,p in periods} != exported_periods:
        raise ValueError("Per-chain period coverage differs from exported calendar")
    emit("periods",(compact(dict(chain_id=c,record_key=c+"/"+p,period=p,payload={"period":p})) for c,p in sorted(periods)))
    emit("archive",(compact(dict(part=i//480000,sha256=digest(archive_bytes[i:i+480000]),
                            data=base64.b64encode(archive_bytes[i:i+480000]).decode("ascii")))
                    for i in range(0,len(archive_bytes),480000)))
    (output/"batches.json").write_text(compact(dict(run_id=rid,archive_sha256=archive_sha,
        manifest_sha256=manifest_sha,batches=receipts)),encoding="utf-8")


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("archive","snapshot","output"):
        p.add_argument("--"+name,type=Path,required=True)
    for name in ("archive-sha","manifest-sha"):
        p.add_argument("--"+name,required=True)
    prepare(**vars(p.parse_args()))
