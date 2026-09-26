"""Compare a private historical fact export against the generic scan result.

Only keys present in the reconciled result are compared; pre-lifecycle zero
rows and unresolved conflicts are intentionally not treated as golden matches.
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

METRIC = {"Venta": "SALES", "Pedido": "ORDER", "Entrega": "DELIVERY"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--chain", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--product-prefix", required=True)
    parser.add_argument("--expected-count", type=int,
                        help="Fail if comparable coverage shrinks or expands")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    selected = {(row["key"][1], row["key"][2], row["key"][3]): Decimal(row["value"])
                for row in report["selected"] if row["key"][0] == args.chain
                and row["key"][1].startswith(args.product_prefix)
                and row["key"][2].startswith(args.year)}
    expected = {}
    with args.facts.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if (row.get("metric") not in METRIC or not row.get("period", "").startswith(args.year)
                    or not row.get("upc", "").startswith(args.product_prefix)
                    or row.get("value") in {None, "", "None"}):
                continue
            key = (row["upc"], row["period"], METRIC[row["metric"]])
            value = Decimal(row["value"])
            if key in expected and expected[key] != value:
                raise SystemExit("golden_source_internal_conflict")
            expected[key] = value
    missing = sorted(set(selected) - set(expected))
    mismatches = [key for key in selected.keys() & expected.keys() if selected[key] != expected[key]]
    print(json.dumps({"compared": len(selected.keys() & expected.keys()),
                      "missing_expected": len(missing), "mismatches": len(mismatches)}, indent=2))
    if not selected or missing or mismatches or (args.expected_count is not None
                                                and len(selected.keys() & expected.keys()) != args.expected_count):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
