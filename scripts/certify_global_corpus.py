"""Opt-in private corpus certification; no database, runtime or source writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.business_membership import load_authority  # noqa: E402
from services.assistant_api.chain_ingestion import load_profiles, read_sources  # noqa: E402
from services.assistant_api.global_certification import certify_global_corpus, freeze_global_golden  # noqa: E402
from services.assistant_api.hierarchical_certification import certify_hierarchy, legacy_cohort, read_value_golden  # noqa: E402
from services.assistant_api.hierarchical_grain import load_scope_authority, reconcile_grain  # noqa: E402
from services.assistant_api.historical_corpus import SourceSpec, sha256_file  # noqa: E402
from services.assistant_api.source_adjudication import fingerprint  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_pins(files):
    for record in files.values():
        if sha256_file(Path(record["path"])) != record["sha256"]:
            raise ValueError("pinned_input_bytes_changed")


def run(contract_path, contract_sha, output):
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to(root / "outputs") or (output.exists() and any(output.iterdir())):
        raise ValueError("fresh_private_output_required_no_overwrites")
    if sha256_file(contract_path) != contract_sha:
        raise ValueError("contract_hash_mismatch")
    contract = read(contract_path)
    if contract.get("owner_approved") is not True:
        raise ValueError("owner_approval_required")
    files = contract["files"]
    verify_pins(files)
    paths = {name: Path(record["path"]) for name, record in files.items()}
    membership = load_authority(paths["membership_authority"], expected_sha=files["membership_authority"]["sha256"],
                                rule_path=paths["membership_rule"], rule_sha=files["membership_rule"]["sha256"])
    scope = load_scope_authority(paths["scope_authority"], expected_sha=files["scope_authority"]["sha256"],
                                 rule_path=paths["scope_rule"], rule_sha=files["scope_rule"]["sha256"])
    profiles = load_profiles(paths["profiles"])
    config = read(paths["source_rules"])
    specs = [SourceSpec(Path(p)) for p in config["files"]]
    source_pins = {str(s.path): sha256_file(s.path) for s in specs}
    if set(source_pins.values()) != set(config["sources"]):
        raise ValueError("source_bytes_changed")
    baseline = read(paths["baseline"])
    books, manifests = read_sources(specs)
    report, _ = reconcile_grain(books, manifests, profiles, membership, scope)
    for field in ("selected", "versions", "fact_scope_catalog"):
        if report[field] != baseline[field]:
            raise ValueError("approved_baseline_changed_" + field)
    if report["summary"]["dataset_sha256"] != contract["approved_dataset_sha256"]:
        raise ValueError("approved_dataset_changed")
    forward = certify_global_corpus(report, books, profiles, membership, scope, verified_source_hashes=set(source_pins.values()))
    reverse_books, reverse_manifests = read_sources(specs[::-1])
    reverse_membership = {**membership, "scopes": membership["scopes"][::-1]}
    reverse_scope = {**scope, "source_scopes": scope["source_scopes"][::-1]}
    reverse, _ = reconcile_grain(reverse_books, reverse_manifests, profiles[::-1], reverse_membership, reverse_scope)
    backward = certify_global_corpus(reverse, reverse_books, profiles[::-1], reverse_membership, reverse_scope,
                                     verified_source_hashes=set(source_pins.values()))
    if forward["status"] != "PASS" or backward["status"] != "PASS":
        raise ValueError("global_certification_blocked:" + json.dumps(forward["summary"], sort_keys=True))
    checks = {"dataset": report["summary"]["dataset_sha256"] == reverse["summary"]["dataset_sha256"],
              "certification": forward["certification_sha256"] == backward["certification_sha256"],
              "counts": forward["summary"] == backward["summary"], "scopes": forward["scope_summary"] == backward["scope_summary"],
              "products": forward["product_coverage"] == backward["product_coverage"],
              "blocked_trace": forward["blocked_trace"] == backward["blocked_trace"]}
    if not all(checks.values()):
        raise ValueError("source_order_nondeterministic:" + json.dumps(checks))
    policy = read(paths["legacy_policy"])
    legacy = read(paths["legacy_report"])
    cohort = legacy_cohort(legacy, policy)
    reference = read_value_golden(paths["legacy_csv"], expected_sha=files["legacy_csv"]["sha256"],
                                 cohort=cohort, metric_map=policy["metric_map"])
    legacy_check = certify_hierarchy(cohort, report, reference=reference, policy=policy)
    if legacy_check["status"] != "PASS" or legacy_check != read(paths["hierarchical_golden"]):
        raise ValueError("frozen_legacy_or_hierarchical_regression")
    verify_pins(files)
    if source_pins != {str(s.path): sha256_file(s.path) for s in specs} or sha256_file(contract_path) != contract_sha:
        raise ValueError("inputs_changed_during_certification")
    golden = freeze_global_golden(forward, certified_at=datetime.now(timezone.utc).isoformat())
    artifacts = {"global_hierarchical_certification": forward, "global_scope_summary": forward["scope_summary"],
                 "global_product_coverage": forward["product_coverage"], "blocked_trace": forward["blocked_trace"],
                 "independent_scope_representations": forward["independent_scope_representations"],
                 "non_actual_scopes": forward["non_actual_scopes"], "global_corpus_golden": golden,
                 "legacy_regression_reference": {"status": "PASS", "summary": legacy_check["summary"],
                                                  "pinned_files_unchanged": True, "legacy_is_not_global_authority": True},
                 "determinism": {"status": "PASS", "checks": checks, "certification_sha256": forward["certification_sha256"],
                                  "source_order": "REVERSED", "profile_order": "REVERSED", "authority_order": "REVERSED"},
                 "closure": {"status": "PASS_GLOBAL_HIERARCHICAL_CERTIFICATION", "source_files_unchanged": True,
                             "supabase_writes": 0, "runtime_modified": False, "baseline_files_unchanged": True,
                             "contract_sha256": contract_sha, "summary": forward["summary"]}}
    for name, artifact in artifacts.items():
        write_new(output / (name + ".json"), artifact)
    packet = ["# Grouped evidence review", "", "No automatic winners, child promotion or temporal release.", ""]
    for group in forward["blocked_trace"]["groups"]:
        packet.extend(["## " + group["group_id"], "", f"Scope: {group['scope_id']}; kind: {group['kind']}; reason: {group['reason']}.",
                       f"Keys: {group['blocked_keys']}; periods: {group['period_min']}..{group['period_max']}.",
                       "Metrics: " + json.dumps(group["metric_counts"], sort_keys=True),
                       "Sources: " + json.dumps(group["source_pairs"], ensure_ascii=False),
                       "Question: " + group["question"], "Status: BLOCKED; winner selected: NO.", ""])
    with (output / "grouped_review_packet.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(packet))
    print(json.dumps({"status": artifacts["closure"]["status"], "summary": forward["summary"],
                      "certification_sha256": forward["certification_sha256"], "legacy": legacy_check["summary"],
                      "review_groups": len(forward["blocked_trace"]["groups"])}, ensure_ascii=False, indent=2))
    return forward


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.contract, args.contract_sha, args.output_dir)


if __name__ == "__main__":
    main()
