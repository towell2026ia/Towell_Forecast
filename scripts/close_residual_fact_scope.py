"""Private read-only residual review. Never bootstraps a database or edits V1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.certify_global_corpus import read, verify_pins  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402
from services.assistant_api.business_membership import load_authority  # noqa: E402
from services.assistant_api.chain_ingestion import load_profiles, read_sources  # noqa: E402
from services.assistant_api.global_certification import certify_global_corpus  # noqa: E402
from services.assistant_api.hierarchical_certification import certify_hierarchy, legacy_cohort, read_value_golden  # noqa: E402
from services.assistant_api.hierarchical_grain import load_scope_authority, reconcile_grain  # noqa: E402
from services.assistant_api.historical_corpus import SourceSpec, sha256_file  # noqa: E402
from services.assistant_api.residual_scope import (  # noqa: E402
    close_residuals, cluster_inventory, grouped_review, inventory_residuals, verify_global_baseline,
)
from services.assistant_api.source_adjudication import fingerprint  # noqa: E402


def review_markdown(reviews):
    lines = ["# Revisión agrupada de alcance residual", "", "Sin asignación de cantidades ni selección de ganadores.", ""]
    for review in reviews:
        lines.extend(["## " + review["decision_id"], "", "Sources/sheets: " + json.dumps(review["source_pairs"], ensure_ascii=False),
                      f"Affected facts: {review['affected_facts']}", f"Period: {review['period_min']} to {review['period_max']}",
                      "Candidate parent: " + ", ".join(review["candidate_parents"]),
                      "Candidate units (membership only): " + ", ".join(review["candidate_units"]),
                      "Evidence available: " + "; ".join(review["evidence_available"]),
                      "Evidence missing: " + review["evidence_missing"],
                      "Possible interpretations: " + "; ".join(review["possible_interpretations"]),
                      "Risk: " + review["risk"], "Question to owner: " + review["question"], "Status: BLOCKED", ""])
    return "\n".join(lines)


def run(contract_path, contract_sha, output):
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to(root / "outputs") or (output.exists() and any(output.iterdir())):
        raise ValueError("fresh_private_output_required_no_overwrites")
    if sha256_file(contract_path) != contract_sha:
        raise ValueError("residual_contract_hash_mismatch")
    contract = read(contract_path)
    files = contract["files"]
    verify_pins(files)
    paths = {k: Path(v["path"]) for k, v in files.items()}
    baseline, cert, golden = read(paths["baseline"]), read(paths["global_certification"]), read(paths["global_golden"])
    gate = verify_global_baseline(baseline, cert, golden, contract["expected_global"])
    membership = load_authority(paths["membership_authority"], expected_sha=files["membership_authority"]["sha256"],
                                rule_path=paths["membership_rule"], rule_sha=files["membership_rule"]["sha256"])
    scope = load_scope_authority(paths["scope_authority"], expected_sha=files["scope_authority"]["sha256"],
                                 rule_path=paths["scope_rule"], rule_sha=files["scope_rule"]["sha256"])
    rules = read(paths["residual_rules"])
    for rule in rules["rules"]:
        if rule.get("rule_sha256") not in {v["sha256"] for v in files.values()}:
            raise ValueError("unverified_review_document")
    profiles = load_profiles(paths["profiles"])
    config = read(paths["source_rules"])
    specs = [SourceSpec(Path(f)) for f in config["files"]]
    original = {str(s.path): sha256_file(s.path) for s in specs}
    if set(original.values()) != set(config["sources"]):
        raise ValueError("source_bytes_changed_STOP")
    books, manifests = read_sources(specs)
    replay, _ = reconcile_grain(books, manifests, profiles, membership, scope)
    for field in ("selected", "versions", "fact_scope_catalog"):
        if replay[field] != baseline[field]:
            raise ValueError("baseline_replay_changed_STOP_" + field)
    fresh_cert = certify_global_corpus(replay, books, profiles, membership, scope, verified_source_hashes=set(original.values()))
    if fresh_cert["certification_sha256"] != cert["certification_sha256"]:
        raise ValueError("full_literal_global_regression_failed_STOP")
    inventory, registry, adapted, members = inventory_residuals(baseline, books, profiles, membership, scope)
    if len(inventory) != contract["expected_scope_blockers"]:
        raise ValueError("scope_blocker_baseline_changed_STOP")
    clusters = cluster_inventory(inventory)
    result = close_residuals(baseline, inventory, registry, books, adapted, members, rules=rules["rules"])
    reverse_books, reverse_manifests = read_sources(specs[::-1])
    reverse_membership = {**membership, "scopes": membership["scopes"][::-1]}
    reverse_scope = {**scope, "source_scopes": scope["source_scopes"][::-1]}
    backwards, _ = reconcile_grain(reverse_books, reverse_manifests, profiles[::-1], reverse_membership, reverse_scope)
    for field in ("selected", "versions", "source_assignments", "fact_scope_catalog", "derived_lineage"):
        if fingerprint(backwards[field]) != fingerprint(baseline[field]):
            raise ValueError("reverse_source_baseline_changed_STOP_" + field)
    # Membership is a set of independently evidenced rows, not read order.
    if fingerprint(sorted(backwards["commercial_memberships"], key=fingerprint)) != fingerprint(sorted(baseline["commercial_memberships"], key=fingerprint)):
        raise ValueError("reverse_source_baseline_membership_changed_STOP")
    functional_ledger = lambda report: sorted((d for d in report["resolution_ledger"] if len(d["key"]) == 4), key=fingerprint)
    if fingerprint(functional_ledger(backwards)) != fingerprint(functional_ledger(baseline)):
        raise ValueError("reverse_source_baseline_ledger_changed_STOP")
    reverse_inventory, reverse_registry, reverse_adapted, reverse_members = inventory_residuals(
        backwards, reverse_books, profiles[::-1], reverse_membership, reverse_scope)
    # V2 inherits the pinned V1 manifest, including both byte-identical physical
    # copies. Their read-order-dependent primary filename is not grain evidence.
    # All replayed functional fields and the complete residual inventory are
    # independently checked before this immutable provenance is reused.
    reverse_result = close_residuals(baseline, reverse_inventory, reverse_registry, reverse_books, reverse_adapted,
                                     reverse_members, rules=rules["rules"][::-1])
    checks = {"inventory": inventory == reverse_inventory, "registry": registry == reverse_registry,
              "clusters": clusters == cluster_inventory(reverse_inventory)}
    for name in ("result", "global_v1_to_v2_trace", "scope_resolution_ledger", "value_conflict_trace", "canonical_candidate"):
        checks[name] = fingerprint(result[name]) == fingerprint(reverse_result[name])
    if not all(checks.values()):
        raise ValueError("residual_source_order_nondeterministic:" + json.dumps(checks))
    if result["value_conflict_trace"]["before"] != contract["expected_value_conflicts"]:
        raise ValueError("value_conflict_baseline_changed_STOP")
    policy = read(paths["legacy_policy"])
    cohort = legacy_cohort(read(paths["legacy_report"]), policy)
    reference = read_value_golden(paths["legacy_csv"], expected_sha=files["legacy_csv"]["sha256"], cohort=cohort, metric_map=policy["metric_map"])
    legacy = certify_hierarchy(cohort, baseline, reference=reference, policy=policy)
    if legacy["status"] != "PASS" or legacy != read(paths["hierarchical_golden"]):
        raise ValueError("frozen_legacy_changed_STOP")
    # Also run legacy matching against the candidate; new facts cannot rewrite it.
    legacy_candidate = certify_hierarchy(cohort, result["canonical_candidate"], reference=reference, policy=policy)
    if legacy_candidate["status"] != "PASS" or legacy_candidate["summary"] != legacy["summary"]:
        raise ValueError("legacy_candidate_regression_failed")
    reviews = grouped_review(clusters, result["scope_resolution_ledger"])
    if sum(r["affected_facts"] for r in reviews) != result["result"]["remaining_scope_blockers"]:
        raise ValueError("untraced_remaining_review_blocker")
    verify_pins(files)
    if original != {str(s.path): sha256_file(s.path) for s in specs} or sha256_file(contract_path) != contract_sha:
        raise ValueError("input_bytes_changed_during_review")
    artifacts = {**result, "fact_scope_inventory": inventory, "fact_scope_clusters": clusters,
                 "scope_evidence_registry": registry, "review_decisions": reviews, "approved_source_rules": rules,
                 "baseline_gate": gate, "determinism": {"status": "PASS", "checks": checks, "result_sha256": fingerprint(result)},
                 "regressions": {"global_v1": "PASS", "global_literal_checks": fresh_cert["summary"]["literal_matches"],
                                 "legacy": legacy["summary"], "source_files_unchanged": True, "frozen_files_unchanged": True}}
    for name, artifact in artifacts.items():
        write_new(output / (name + ".json"), artifact)
    with (output / "manual_fact_scope_review.md").open("x", encoding="utf-8") as stream:
        stream.write(review_markdown(reviews))
    print(json.dumps({"result": result["result"], "cluster_count": clusters["cluster_count"], "pareto": clusters["pareto"],
                      "block_types": clusters["block_type_counts"], "review_decisions": len(reviews),
                      "global_regression": result["global_v1_to_v2_trace"]["status"], "legacy": legacy["summary"],
                      "value_conflicts_remaining": result["value_conflict_trace"]["remaining_original"]}, indent=2))
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.contract, args.contract_sha, args.output_dir)


if __name__ == "__main__":
    main()
