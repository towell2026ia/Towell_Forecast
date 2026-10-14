"""Build a local approval packet from a sealed, RLS-filtered read-only export."""

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.commercial_catalog import analyze  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--rules", type=Path, default=Path("config/commercial_catalog_rules.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    catalog = raw[0]["catalog"] if isinstance(raw, list) else raw
    packet = analyze(catalog, json.loads(args.rules.read_text(encoding="utf-8")))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "mapping_proposals.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = [f for f in packet["rows"][0] if f != "evidence"] if packet["rows"] else ["product_id"]
    with (args.output_dir / "classification_matrix.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in packet["rows"]:
            values = {f: json.dumps(row[f], ensure_ascii=False) if isinstance(row[f], list) else row[f] for f in fields}
            writer.writerow({f: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for f, v in values.items()})
    print(json.dumps({"verdict": packet["verdict"], "metrics": packet["metrics"], "mapping_hash": packet["mapping_hash"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
