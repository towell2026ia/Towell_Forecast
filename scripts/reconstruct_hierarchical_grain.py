"""Private, read-only hierarchical historical pre-ingestion. Never publishes data."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.business_membership import certify_literals, load_authority  # noqa: E402
from services.assistant_api.chain_ingestion import load_profiles, read_sources  # noqa: E402
from services.assistant_api.historical_corpus import SourceSpec, sha256_file  # noqa: E402
from services.assistant_api.hierarchical_certification import (  # noqa: E402
    certify_hierarchy, legacy_cohort, read_value_golden,
)
from services.assistant_api.hierarchical_grain import (  # noqa: E402
    BASELINE_MEMBERSHIP, SCOPE_BLOCKS, VALUE_REASONS, coverage, load_scope_authority,
    reconcile_grain, trace_baseline, value_conflicts,
)
from services.assistant_api.source_adjudication import fingerprint  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402


def verify_baseline(old, dataset_sha):
    digest = hashlib.sha256(json.dumps(old["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    counts = Counter(d["reason_code"] for d in old["resolution_ledger"] if d["blocking"])
    if (digest != dataset_sha or old["summary"]["dataset_sha256"] != digest or len(old["selected"]) != 32624
            or sum(counts[r] for r in BASELINE_MEMBERSHIP) != 9016 or sum(counts[r] for r in VALUE_REASONS) != 729):
        raise ValueError("frozen_9016_729_baseline_mismatch")


def manual_review(report, reinterpretation):
    lines = ["# Revisión de grano y cantidades", "", "Sin autorización de publicar ni asignar cantidades padre a hijos.", "",
             "## Las 12 decisiones anteriores", "", "La validez del scope no implica elegir una cantidad contradictoria.", ""]
    for group in reinterpretation["review_decisions"]:
        lines.extend([f"### {group['old_decision_id']}", "", f"Motivo anterior: {group['old_reason']}",
                      f"Hechos anteriores: {group['old_affected_facts']}",
                      "Resultado: " + json.dumps(group["new_results"], ensure_ascii=False, sort_keys=True),
                      f"Cantidades con contradicción pendiente: {group['value_blocking_facts']}",
                      "Asignación a hijos: NO APROBADA.", ""])
    groups = {}
    for row in report["resolution_ledger"]:
        if not row["blocking"]:
            continue
        sources = tuple(sorted({(s["source_sha256"], s["sheet"]) for s in row["sources"]}))
        groups.setdefault((row["reason_code"], sources), []).append(row)
    lines.extend(["## Decisiones todavía pendientes", ""])
    for (reason, sources), rows in sorted(groups.items()):
        question = ("¿Esta cantidad literal es propia de una unidad, de la cadena padre o una copia derivada? Aporta scope y evidencia hash/hoja/celda; la pertenencia comercial no prueba propiedad de cantidad."
                    if reason in SCOPE_BLOCKS else
                    "¿Qué evidencia independiente identifica versión/autoridad de estos valores contradictorios del mismo scope? No seleccionar mayor/último ni sumar representaciones.")
        lines.extend(["### G-"+fingerprint([reason, sources])[:12], "", "Pestañas: " + ", ".join(sorted({s[1] for s in sources})),
                      "Hashes: " + ", ".join(sorted({s[0] for s in sources})), f"Motivo: {reason}. Claves: {len(rows)}.",
                      "Pregunta: " + question, "", "Estado: PENDIENTE.", ""])
    return "\n".join(lines), len(groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline-dir", "profiles", "source-rules", "membership-authority", "membership-rule", "scope-authority", "scope-rule", "output-dir",
                 "certification-contract", "legacy-value-golden"):
        parser.add_argument("--"+name, type=Path, required=True)
    for name in ("baseline-dataset-sha", "membership-authority-sha", "membership-rule-sha", "scope-authority-sha", "scope-rule-sha",
                 "certification-contract-sha", "legacy-value-golden-sha"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    if (not output.is_relative_to(root/"outputs") or (output/"reconciliation.json").exists()
            or args.scope_authority.resolve() != output/"parent_chain_authority.json"):
        parser.error("fresh ignored output with private authority is required; no overwrites")
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    baseline_path = args.baseline_dir/"reconciliation.json"
    baseline_bytes_sha = sha256_file(baseline_path)
    old = read(baseline_path)
    verify_baseline(old, args.baseline_dataset_sha)
    if sha256_file(args.certification_contract) != args.certification_contract_sha:
        raise ValueError("certification_contract_hash_mismatch")
    policy = read(args.certification_contract)
    if policy["legacy_report_sha256"] != baseline_bytes_sha:
        raise ValueError("legacy_report_bytes_changed")
    cohort = legacy_cohort(old, policy)
    legacy_reference = read_value_golden(args.legacy_value_golden, expected_sha=args.legacy_value_golden_sha,
                                        cohort=cohort, metric_map=policy["metric_map"])
    if (policy.get("owner_approved_scope_refinement") is not True
            or legacy_reference["content_sha256"] != policy["legacy_csv_content_sha256"]):
        raise ValueError("unapproved_scope_refinement_or_legacy_content_change")
    membership_authority = load_authority(args.membership_authority, expected_sha=args.membership_authority_sha,
                                         rule_path=args.membership_rule, rule_sha=args.membership_rule_sha)
    scope_authority = load_scope_authority(args.scope_authority, expected_sha=args.scope_authority_sha,
                                         rule_path=args.scope_rule, rule_sha=args.scope_rule_sha)
    profiles = load_profiles(args.profiles)
    config = read(args.source_rules)
    specs = [SourceSpec(Path(path)) for path in config["files"]]
    original_hashes = {str(s.path): sha256_file(s.path) for s in specs}
    if set(original_hashes.values()) != set(config["sources"]):
        raise ValueError("source_bytes_changed")
    books, manifests = read_sources(specs)
    report, resolver = reconcile_grain(books, manifests, profiles, membership_authority, scope_authority)
    reverse_books, reverse_manifests = read_sources(specs[::-1])
    reverse, _ = reconcile_grain(reverse_books, reverse_manifests, profiles[::-1],
                                {**membership_authority, "scopes": membership_authority["scopes"][::-1]},
                                {**scope_authority, "source_scopes": scope_authority["source_scopes"][::-1]})
    for field in ("summary", "selected", "versions", "source_assignments", "commercial_memberships", "derived_lineage", "fact_scope_catalog"):
        if report[field] != reverse[field]:
            raise ValueError("source_order_changed_"+field)
    functional_ledger = lambda r: sorted((d for d in r["resolution_ledger"] if len(d["key"]) == 4), key=fingerprint)
    if functional_ledger(report) != functional_ledger(reverse):
        raise ValueError("source_order_changed_ledger")
    golden = certify_hierarchy(cohort, report, reference=legacy_reference, policy=policy)
    if golden != certify_hierarchy(cohort, reverse, reference=legacy_reference, policy=policy):
        raise ValueError("source_order_changed_certification")
    if golden["status"] != "PASS":
        raise ValueError("hierarchical_golden_failed:" + json.dumps(golden["summary"]))
    reinterpretation = trace_baseline(old, report)
    old_ids = set(re.findall(r"## Decisión (M-[a-f0-9]+)", (args.baseline_dir/"manual_membership_review.md").read_text(encoding="utf-8")))
    groups = reinterpretation["review_decisions"]
    if (len(reinterpretation["baseline_membership"]) != 9016 or len(reinterpretation["baseline_values"]) != 729
            or len(groups) != 12 or {g["old_decision_id"] for g in groups} != old_ids
            or sum(g["old_affected_facts"] for g in groups) != 9016):
        raise ValueError("9016_729_12_accounting_mismatch")
    facts, children = coverage(report, resolver)
    values = value_conflicts(report, reinterpretation["baseline_values"])
    certificate, samples = certify_literals(report, books)
    review, pending_groups = manual_review(report, reinterpretation)
    comparison = {"before": {"membership_blockers": 9016, "value_blockers": 729, "selected": 32624},
                  "after": {"fact_coverage": {k:v for k,v in facts.items() if k != "series"}, "child_coverage": children,
                            "baseline_membership_outcomes": dict(sorted(Counter(r["final_status"] for r in reinterpretation["baseline_membership"]).items())),
                            "value_counts": values["current_counts"], "baseline_value_outcomes": values["baseline_outcomes"],
                            "derived_representations": len(report["derived_lineage"]), "pending_review_groups": pending_groups}}
    if (original_hashes != {str(s.path): sha256_file(s.path) for s in specs} or sha256_file(baseline_path) != baseline_bytes_sha
            or sha256_file(args.legacy_value_golden) != args.legacy_value_golden_sha):
        raise ValueError("source_or_baseline_modified")
    artifacts = {"fact_scope_catalog": report["fact_scope_catalog"], "commercial_memberships": report["commercial_memberships"],
                 "membership_reinterpretation": reinterpretation, "fact_coverage": facts, "child_coverage": children,
                 "reconciliation": report, "value_conflicts": values, "comparison": comparison,
                 "literal_certificate": certificate, "regression_samples": samples,
                 "hierarchical_scope_golden": golden,
                 "determinism": {"status": "PASS", "dataset_sha256": report["summary"]["dataset_sha256"], "hierarchical_golden": "PASS"}}
    for name, value in artifacts.items():
        write_new(output/(name+".json"), value)
    with (output/"manual_review.md").open("x", encoding="utf-8") as stream:
        stream.write(review)
    print(json.dumps({"comparison":comparison,"summary":{k:v for k,v in report["summary"].items() if k != "coverage"},
                      "certificate":certificate}, indent=2))


if __name__ == "__main__":
    main()
