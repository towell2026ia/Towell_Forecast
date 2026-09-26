"""Private, admin-only membership replay. No database, network or runtime writes."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.business_membership import (  # noqa: E402
    certify_literals, commercial_catalog, load_authority,
    manual_membership_review, membership_counts, reconcile_business_history, trace_membership_baseline, trace_value_baseline,
)
from services.assistant_api.chain_ingestion import load_profiles, read_sources  # noqa: E402
from services.assistant_api.historical_corpus import SourceSpec, sha256_file  # noqa: E402
from services.assistant_api.source_adjudication import cluster_conflicts, fingerprint  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402


def verify_baseline(old, *, dataset_sha, versions_sha):
    actual = hashlib.sha256(json.dumps(old["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    counts = Counter(d["reason_code"] for d in old["resolution_ledger"] if d["blocking"])
    if (actual != dataset_sha or old["summary"]["dataset_sha256"] != dataset_sha or fingerprint(old["versions"]) != versions_sha
            or counts != {"AMBIGUOUS_CHAIN_MEMBERSHIP": 7628, "MISSING_CHAIN_MEMBERSHIP": 4222,
                          "INTERNAL_SOURCE_DUPLICATE": 408, "CROSS_SOURCE_CONFLICT": 91, "SAME_SNAPSHOT_CONFLICT": 1}):
        raise ValueError("frozen_11850_500_baseline_mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--baseline-dataset-sha", required=True)
    parser.add_argument("--baseline-versions-sha", required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--authority-sha", required=True)
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument("--rule-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    private_root = Path(__file__).resolve().parents[1] / "outputs"
    output = args.output_dir.resolve()
    if (not output.is_relative_to(private_root) or not args.authority.resolve().is_relative_to(private_root)
            or (output / "reconciliation.json").exists()):
        parser.error("fresh outputs/ and private authority are required; no overwrite")
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    old = read(args.baseline_dir / "reconciliation.json")
    verify_baseline(old, dataset_sha=args.baseline_dataset_sha, versions_sha=args.baseline_versions_sha)
    profiles = load_profiles(args.baseline_dir / "sheet_profiles.json")
    config = read(args.baseline_dir / "source_rules.json")
    specs = [SourceSpec(Path(path)) for path in config["files"]]
    original_hashes = {str(s.path): sha256_file(s.path) for s in specs}
    if set(original_hashes.values()) != set(config["sources"]):
        raise ValueError("source_hashes_changed")
    authority = load_authority(args.authority, expected_sha=args.authority_sha, rule_path=args.rule, rule_sha=args.rule_sha)
    books, manifests = read_sources(specs)
    report, adapted, memberships, scopes = reconcile_business_history(books, manifests, profiles, authority)
    reverse_books, reverse_manifests = read_sources(specs[::-1])
    reverse, _, _, _ = reconcile_business_history(reverse_books, reverse_manifests, profiles[::-1],
                                                   {**authority, "scopes": authority["scopes"][::-1]})
    for field in ("summary", "selected", "versions", "chain_membership_ledger", "source_assignments"):
        if report[field] != reverse[field]:
            raise ValueError("source_order_changed_" + field)
    blockers = lambda r: sorted((d for d in r["resolution_ledger"] if d["blocking"]), key=fingerprint)
    if blockers(report) != blockers(reverse):
        raise ValueError("source_order_changed_conflicts")
    membership_resolution = trace_membership_baseline(old, report)
    value_resolution = trace_value_baseline(old, report)
    if len(membership_resolution) != 11850 or len(value_resolution) != 500:
        raise ValueError("old_blockers_not_all_traced")
    catalog = commercial_catalog(adapted, scopes, memberships, read(args.baseline_dir / "chain_catalog.json"))
    certificate, samples = certify_literals(report, books)
    review, review_decisions = manual_membership_review(report)
    counts = membership_counts(report)
    comparison = {"before": {"commercial_units": 17, "membership": 11850, "values": 500},
                  "after": {"catalog_units": len(catalog), "catalog_status": dict(Counter(c["status"] for c in catalog)),
                            "eligible_business_sheets": len(scopes), "full_corpus_blockers": counts,
                            "membership_baseline_outcomes": dict(sorted(Counter(r["final_status"] for r in membership_resolution).items())),
                            "value_baseline_outcomes": dict(sorted(Counter(r["final_status"] for r in value_resolution).items())),
                            "value_baseline_membership_pending": sum(r["membership_pending"] for r in value_resolution),
                            "grouped_membership_review_decisions": review_decisions}, "source_bytes_unchanged": True}
    if original_hashes != {str(s.path): sha256_file(s.path) for s in specs}:
        raise ValueError("source_bytes_changed_during_scan")
    verify_baseline(read(args.baseline_dir / "reconciliation.json"), dataset_sha=args.baseline_dataset_sha, versions_sha=args.baseline_versions_sha)
    artifacts = {"business_sheet_evidence": report["business_sheet_evidence"], "commercial_unit_catalog": catalog,
                 "commercial_sheet_aliases": authority["commercial_sheet_aliases"], "membership_ledger": [asdict(m) for m in memberships],
                 "membership_resolution": membership_resolution, "value_conflicts_rekeyed": value_resolution,
                 "reconciliation": report, "conflict_clusters": cluster_conflicts(report), "sheet_profiles": [asdict(p) for p in adapted],
                 "literal_certificate": certificate, "regression_samples": samples, "comparison": comparison,
                 "determinism": {"status": "PASS", "dataset_sha256": report["summary"]["dataset_sha256"]}}
    for name, value in artifacts.items():
        write_new(output / f"{name}.json", value)
    with (output / "manual_membership_review.md").open("x", encoding="utf-8") as stream:
        stream.write(review)
    print(json.dumps({"comparison": comparison, "summary": {k: v for k, v in report["summary"].items() if k != "coverage"},
                      "certificate": certificate}, indent=2))


if __name__ == "__main__":
    main()
