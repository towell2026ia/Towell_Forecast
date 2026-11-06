"""Local-only CLI; writes a new preview file and never imports real inventory."""
import argparse
import hashlib
import json
from pathlib import Path
from .validator import csv_rows, preflight


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("csv", "manifest", "mappings", "catalog", "existing", "output"):
        p.add_argument("--" + arg, type=Path, required=True)
    args = p.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    raw = args.csv.read_bytes()
    manifest = load(args.manifest)
    if manifest.get("source_sha256") != hashlib.sha256(raw).hexdigest():
        raise SystemExit("HASH_FUENTE_NO_COINCIDE")
    result = preflight(csv_rows(raw.decode("utf-8-sig")), manifest, load(args.mappings), load(args.catalog), load(args.existing))
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(result["status"] + ": " + str(args.output.resolve()))
    return 0 if result["status"] == "READY_FOR_OFFLINE_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
