"""Read-only all-fact/source-literal certification. Outputs stay private."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.chain_ingestion import OUTCOMES, read_sources  # noqa: E402
from services.assistant_api.historical_corpus import SourceSpec, number  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402


def certify(report, books, trace, profiles, sheet_inventory):
    assignments = {(r["source_sha256"], r["sheet"], r["cell"]): r for r in report["source_assignments"]}
    samples = {}
    for fact in report["selected"]:
        key = (fact["source_sha256"], fact["sheet"], fact["cell"])
        sheet = books[key[0]][key[1]]
        column = "".join(c for c in key[2] if c.isalpha())
        row = int("".join(c for c in key[2] if c.isdigit()))
        status, value = number(sheet["rows"][row].get(column))
        if (key[2] in sheet["formulas"] or status != "VALUE" or Decimal(value) != Decimal(fact["value"])
                or assignments[key]["key"] != fact["key"]):
            raise ValueError("source_literal_or_chain_regression")
        if fact["availability_source"] != "UNKNOWN" or fact["available_at"] is not None:
            raise ValueError("historical_date_fabrication")
        samples.setdefault(fact["key"][0], fact)
    if len(trace) != 594 or any(r["new_resolution"] not in OUTCOMES for r in trace):
        raise ValueError("594_trace_incomplete")
    if sum(Counter(r["new_resolution"] for r in trace).values()) != 594:
        raise ValueError("594_trace_accounting")
    expected = {(digest, name) for digest, sheets in books.items() for name in sheets}
    if {(p["source_hash"], p["sheet_name"]) for p in profiles} != expected:
        raise ValueError("profile_inventory_incomplete")
    if {(s["source_hash"], s["sheet_name"]) for s in sheet_inventory} != expected:
        raise ValueError("sheet_inventory_incomplete")
    return {"all_selected_literals": len(report["selected"]), "literal_differences": 0,
            "chains_sampled": len(samples), "old_blockers_traced": len(trace),
            "unique_sheets": len(expected), "physical_sheets": len(sheet_inventory),
            "fabricated_available_at": 0, "dataset_sha256": report["summary"]["dataset_sha256"],
            "status": "PASS"}, [samples[k] for k in sorted(samples)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(Path(__file__).resolve().parents[1] / "outputs"):
        parser.error("outputs must stay private")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    books, _ = read_sources([SourceSpec(Path(p)) for p in config["files"]])
    def read(name):
        return json.loads((directory / (name + ".json")).read_text(encoding="utf-8"))
    certificate, samples = certify(read("reconciliation"), books, read("old_to_new_conflict_map"), read("sheet_profiles"), read("sheet_inventory"))
    write_new(directory / "literal_certificate.json", certificate)
    write_new(directory / "regression_samples.json", samples)
    print(json.dumps(certificate, indent=2))


if __name__ == "__main__":
    main()
