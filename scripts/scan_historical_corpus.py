"""Produce a private, reproducible historical reconciliation report.

Usage: python scripts/scan_historical_corpus.py --source 'file.xlsx::Walmart::2024::2024-12'
       --source 'all-chains.xlsx' --output outputs/prd09_2d/reconciliation.json
No workbook is edited and no database connection is made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.historical_corpus import SourceSpec, scan_sources  # noqa: E402


def source_spec(value: str) -> SourceSpec:
    parts = value.split("::")
    if not 1 <= len(parts) <= 4:
        raise argparse.ArgumentTypeError("source must be FILE[::CHAIN[::YEAR[::CUTOFF]]]")
    try:
        year = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except ValueError as error:
        raise argparse.ArgumentTypeError("YEAR must be an integer") from error
    cutoff = parts[3] if len(parts) > 3 and parts[3] else None
    if cutoff and (len(cutoff) != 7 or cutoff[4] != "-"):
        raise argparse.ArgumentTypeError("CUTOFF must be YYYY-MM")
    return SourceSpec(Path(parts[0]), parts[1] or None if len(parts) > 1 else None,
                      year, cutoff)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=source_spec, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="Write private row-level lineage to an ignored local path")
    parser.add_argument("--hardened", action="store_true", help="PRD 09.2D.1 evidence hierarchy")
    parser.add_argument("--baseline", type=Path, help="Frozen original conflict report")
    args = parser.parse_args()
    output = args.output.resolve()
    repository = Path(__file__).resolve().parents[1]
    allowed = repository / "outputs"
    if not output.is_relative_to(allowed):
        parser.error("private report must be under the ignored repository outputs/ directory")
    if output.exists():
        parser.error("report already exists; use a new output path to preserve evidence")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    if args.hardened and baseline is None:
        parser.error("hardened scan requires the frozen baseline conflict report")
    report = scan_sources(args.source, hardened=args.hardened, baseline=baseline)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.hardened:
        for field in ("resolution_ledger", "identity_ledger"):
            ledger_path = output.parent / f"{field}.json"
            if ledger_path.exists():
                parser.error("ledger already exists; use a fresh output directory")
        for field in ("resolution_ledger", "identity_ledger"):
            (output.parent / f"{field}.json").write_text(
                json.dumps(report[field], ensure_ascii=False, indent=2), encoding="utf-8")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "quality_issues": report.get("quality_issues", {}),
                      "blocking": report["blocking"], "report": str(output)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
