"""Generate dry-run and activation transactions from the approved export, including full receipt coverage."""
import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from prepare import compact, literal


def generate(folder, snapshot):
    index = json.loads((folder/"batches.json").read_bytes())
    raw_manifest = (snapshot/"Manifest.json").read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != index["manifest_sha256"]:
        raise ValueError("Snapshot signature mismatch")
    manifest = json.loads(raw_manifest)
    rid = str(UUID(index["run_id"]))
    if manifest["run_id"] != rid:
        raise ValueError("Planning run mismatch")
    receipt_rows, cardinalities = [], Counter()
    for batch in index["batches"]:
        raw = (folder/batch["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != batch["sha256"]:
            raise ValueError("Prepared SQL signature mismatch")
        expected = re.search(rb"expected text:='([a-f0-9]{64})'; body", raw)
        if not expected:
            raise ValueError("Missing batch digest")
        n = int(Path(batch["file"]).stem.rsplit("_",1)[1])
        receipt_rows.append(dict(resource=batch["resource"],batch_no=n,row_count=batch["rows"],
                                 content_sha256=expected.group(1).decode()))
        cardinalities[batch["resource"]]+=batch["rows"]
    def resource(name):
        raw=(snapshot/"resources"/(name+".jsonl")).read_bytes()
        pinned=next(f for f in manifest["files"] if f["path"]=="resources/"+name+".jsonl")
        if hashlib.sha256(raw).hexdigest()!=pinned["sha256"]:
            raise ValueError("Changed resource: "+name)
        return [json.loads(row,parse_float=Decimal) for row in raw.splitlines()]
    horizons=resource("horizons")
    totals=[sum(row[field] for row in horizons if row[field] is not None)
            for field in ("statistical_value","ml_value","ensemble_value")]
    types="(resource text,batch_no int,row_count int,content_sha256 text)"
    checks=f"""do $verify$
    declare rid uuid:='{rid}'; a numeric; b numeric; c numeric;
    begin
      if not exists(select 1 from private.ft_g6_release where run_id=rid and
        file_sha256='{manifest['run_file_sha256']}' and content_digest='{manifest['content_digest']}'
        and context->>'scenario_hash'='{manifest['scenario_hash']}' and context->>'source_hash'='{manifest['source_hash']}')
        then raise exception 'Release signature mismatch'; end if;
      if (select count(*) from private.ft_g6_receipt where run_id=rid)<>{len(receipt_rows)}
        or exists(select x.* from jsonb_to_recordset({literal(compact(receipt_rows))}::jsonb) as x{types}
          except select resource,batch_no,row_count,content_sha256 from private.ft_g6_receipt where run_id=rid)
        then raise exception 'Missing or unexpected staging receipts'; end if;
      if (select count(*) from private.ft_g6_product where run_id=rid)<>{cardinalities['sealed_products']}
        then raise exception 'Private planning product count mismatch'; end if;
      if (select encode(sha256(string_agg(data,''::bytea order by part)),'hex') from private.ft_g6_archive where run_id=rid)
        is distinct from '{index['archive_sha256']}' then raise exception 'Reconstructed archive digest mismatch'; end if;
      if exists(select x.key,x.value::bigint from jsonb_each_text({literal(compact({k:v for k,v in cardinalities.items() if k not in ('archive','scopes','sealed_products')}))}::jsonb) x
        except select resource,count(*) from private.ft_g6_row where run_id=rid group by resource)
        then raise exception 'Projection cardinality mismatch'; end if;
      select sum((payload->>'statistical_value')::numeric),sum((payload->>'ml_value')::numeric),
             sum((payload->>'ensemble_value')::numeric) into a,b,c from private.ft_g6_row where resource='horizons' and run_id=rid;
      if (a,b,c) is distinct from ({totals[0]}::numeric,{totals[1]}::numeric,{totals[2]}::numeric)
        then raise exception 'Annual quantities differ from frozen projections'; end if;
      if exists(select 1 from private.ft_g6_row where resource='horizons' and run_id=rid
        and ((payload->>'horizon')::int not between 1 and 12 or (payload->>'target_period')::date is distinct from
          make_date(2027,(payload->>'horizon')::int,1))) then raise exception 'Calendar mismatch'; end if;
    end $verify$;"""
    (folder/"verify.sql").write_text("begin read only;\n"+checks+"\ncommit;\n",encoding="utf-8")
    (folder/"activate.sql").write_text("begin;\nlock table private.ft_g6_release in share row exclusive mode;\n"+checks+
        f"\nupdate private.ft_g6_release set active=true where run_id='{rid}' and not active;\nnotify pgrst,'reload schema';\ncommit;\n",encoding="utf-8")
    (folder/"unpublish.sql").write_text(f"begin;\nupdate private.ft_g6_release set active=false where run_id='{rid}' and content_digest='{manifest['content_digest']}';\ncommit;\n",encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    parser.add_argument("snapshot",type=Path)
    args=parser.parse_args()
    generate(args.folder,args.snapshot)
