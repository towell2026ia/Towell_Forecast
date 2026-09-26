"""Read-only grouped adjudication. Private artifacts only; never contacts Supabase.

Source profiles use scan_historical_corpus's FILE::CHAIN::YEAR::CUTOFF syntax.
Aggregated-measure headers are conservative SUMMARY_ONLY, not revision evidence.
Human source authority needs a separately verified administrative evidence registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import load_workbook  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from scripts.scan_historical_corpus import source_spec  # noqa: E402
from services.assistant_api.historical_corpus import HEADER_PERIOD, MONTHLY_METRICS, number, scan_sources, sha256_file  # noqa: E402
from services.assistant_api.source_adjudication import (  # noqa: E402
    AdjudicationConfig, AuthorityRule, cluster_conflicts, fingerprint, pareto, review_pack, resolution_counts,
    load_adjudication_files,
)


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str))


def inspect_sources(specs, reference):
    """Verify headers and retain literal cell/row context for every blocked locator."""
    targets = {}
    for decision in reference["resolution_ledger"]:
        if decision["blocking"]:
            for source in decision["sources"]:
                targets.setdefault((source["source_sha256"], source["sheet"]), set()).add(source["cell"])
    inspections, registry, rules, visited = [], {}, [], set()
    reviewed_at = datetime.now(timezone.utc).isoformat()
    for spec in specs:
        digest = sha256_file(spec.path)
        if digest in visited:
            continue
        visited.add(digest)
        book = load_workbook(spec.path, read_only=True, data_only=False)
        try:
            for sheet in book:
                needed = targets.get((digest, sheet.title), set())
                if not needed:
                    continue
                header = None
                context_rows = {int(''.join(filter(str.isdigit, cell))) for cell in needed}
                contexts, top = {}, []
                for row_number, cells in enumerate(sheet.iter_rows(), 1):
                    values = [cell.value for cell in cells]
                    if row_number <= 20:
                        top.append({"row": row_number, "cells": {cell.coordinate: cell.value for cell in cells
                                                                 if cell.value is not None}})
                    is_wide = "Prime Item Nbr" in values or "PrimeItemNbr" in values
                    # Only a recognisable explicit identity + monthly field header.
                    has_identity = any(str(v).replace(" ", "").casefold() == "primeitemnbr" for v in values)
                    is_raw = "Cadena" in values and "UPC" in values and "Fecha" in values
                    if header is None and (has_identity or is_wide or is_raw):
                        header = {"row": row_number, "fields": {get_column_letter(i): str(v)
                                                                 for i, v in enumerate(values, 1) if v is not None}}
                        for cell in cells:
                            text = str(cell.value or "")
                            match = HEADER_PERIOD.search(text)
                            if not (has_identity and match and text.startswith(("Suma de ", "Sum of "))):
                                continue
                            metrics = [metric for fragment, metric in MONTHLY_METRICS.items() if fragment in match[3]]
                            if len(metrics) != 1 or not spec.chain_hint:
                                continue
                            proof = {"source_hash": digest, "sheet": sheet.title, "locator": cell.coordinate,
                                     "kind": "STRUCTURAL_HEADER", "assertion": "AGGREGATED_MEASURE_HEADER", "content": text}
                            evidence_hash = fingerprint(proof)
                            registry[evidence_hash] = proof
                            ref = {**{k: proof[k] for k in ("source_hash", "sheet", "locator", "kind")},
                                   "evidence_hash": evidence_hash}
                            period = f"{match[1]}-{match[2]}"
                            rules.append(AuthorityRule(
                                "H-" + fingerprint(proof)[:12], spec.chain_hint, period, period, metrics[0], "*",
                                digest, sheet.title, "CHAIN+EXPLICIT_ITEM+MONTH+AGGREGATED_MEASURE", "C", "SUMMARY_ONLY",
                                "Literal exported measure explicitly labelled as aggregation; not raw operational detail.",
                                (ref,), True, "structural-header-verifier", reviewed_at))
                    if row_number in context_rows:
                        contexts[str(row_number)] = {cell.coordinate: cell.value for cell in cells if cell.value is not None}
                if needed or any(rule.source_hash == digest and rule.sheet == sheet.title for rule in rules):
                    inspections.append({"source_hash": digest, "filename": spec.path.name, "sheet": sheet.title,
                                        "rows": sheet.max_row, "columns": sheet.max_column, "header": header,
                                        "top_context": top, "blocked_row_contexts": contexts,
                                        "cutoff_profile": spec.source_cutoff, "cutoff_is_temporal_proof": False,
                                        "purpose": "Operational-looking detail unless field explicitly labelled aggregate",
                                        "identity": "Explicit source identity; description never authority",
                                        "known_issues": "Literal same-grain disagreement requires business authority or disjoint-grain proof",
                                        "authority_status": "UNRESOLVED for competing Level A detail"})
        finally:
            book.close()
    return inspections, registry, rules


def canonical_signature(report):
    # Filesystem inventory/copy filename and scan order are not canonical facts.
    blocked = [{**row, "sources": sorted(row["sources"], key=fingerprint)}
               for row in report["resolution_ledger"] if row["blocking"]]
    return {"summary": report["summary"], "versions": report["versions"],
            "conflicts": sorted(blocked, key=lambda row: tuple(row["key"]))}


def inspect_cluster_grains(clusters, reference, inspections):
    """Check every blocked literal locator, not just the displayed sample."""
    indexed = {(s["source_hash"], s["sheet"]): s for s in inspections}
    decisions = {tuple(d["key"]): d for d in reference["resolution_ledger"] if d["blocking"]}
    results = []
    for cluster in clusters:
        compared, differing_dimensions, checked = 0, set(), 0
        for key in cluster["keys"]:
            contexts = []
            for source in decisions[tuple(key)]["sources"]:
                if source["evidence_level"] != "A":
                    continue
                sheet = indexed[(source["source_sha256"], source["sheet"])]
                row = ''.join(filter(str.isdigit, source["cell"]))
                values = sheet["blocked_row_contexts"][row]
                original = values.get(source["cell"])
                if isinstance(original, str) and original.startswith("=") or number(original) != number(source["value"]):
                    raise ValueError("blocked_literal_locator_mismatch")
                checked += 1
                # Compare only dimensions that are actually present in both rows.
                dimensions = {}
                for column, label in (sheet["header"] or {}).get("fields", {}).items():
                    if label in {"Cadena", "ITEM", "UPC", "Fecha", "Tipo Compra", "Formato", "Color", "Tamaño",
                                 "PrimeItemNbr", "Prime Item Nbr", "SizeDesc", "ColorDesc"}:
                        dimensions[label] = values.get(column + row)
                contexts.append(dimensions)
            common = set.intersection(*(set(c) for c in contexts)) if contexts else set()
            compared += 1
            differing_dimensions.update(label for label in common if len({str(c[label]) for c in contexts}) > 1)
        results.append({"cluster_id": cluster["cluster_id"], "chain": cluster["chain"],
                        "facts_checked": compared, "literal_locators_verified": checked,
                        "present_dimensions_with_differing_values": sorted(differing_dimensions),
                        "disjoint_grain_proven": False,
                        "authority": "Unresolved unless separately documented; a differing descriptor alone is not a new identity"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=source_spec, action="append", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policy-dir", type=Path, help="Private, administratively reviewed policy files")
    parser.add_argument("--reviewed-evidence", type=Path, help="Explicitly reviewed administrative evidence bundle (not browser input)")
    parser.add_argument("--reviewed-evidence-sha", help="Pinned SHA256 of that reviewed bundle")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    if not output.is_relative_to(root / "outputs"):
        parser.error("private artifacts must stay under ignored outputs/")
    if (output / "conflict_clusters.json").exists():
        parser.error("artifacts already exist; use a fresh output directory")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    before = scan_sources(args.source, hardened=True, baseline=baseline)
    if (before["summary"]["dataset_sha256"] != reference["summary"]["dataset_sha256"]
            or before["summary"]["blocking_keys"] != 712
            or before["summary"]["initial_conflicts"] != 730
            or before["summary"]["initial_matrix"]["AUTO_RESOLVABLE"] != 18):
        raise ValueError("frozen_baseline_not_reproduced")
    clusters = cluster_conflicts(before)
    inspections, registry, rules = inspect_sources(args.source, before)
    grain_analysis = inspect_cluster_grains(clusters, before, inspections)
    hashes = {sha256_file(spec.path) for spec in args.source}
    if args.reviewed_evidence:
        if sha256_file(args.reviewed_evidence) != args.reviewed_evidence_sha:
            raise ValueError("reviewed_evidence_bundle_hash_mismatch")
        reviewed = json.loads(args.reviewed_evidence.read_text(encoding="utf-8"))
        if any(fingerprint(proof) != digest for digest, proof in reviewed.items()):
            raise ValueError("reviewed_evidence_content_mismatch")
        registry.update(reviewed)
    elif args.reviewed_evidence_sha:
        parser.error("reviewed evidence path is required")
    matrix = list(rules)
    for cluster in clusters:
        for digest, sheet, level in cluster["source_pair"]:
            matrix.append(AuthorityRule("U-" + fingerprint([cluster["cluster_id"], digest, sheet])[:12],
                                        cluster["chain"], cluster["period_min"], cluster["period_max"],
                                        cluster["metrics"][0], "*", digest, sheet, cluster["grain"], level,
                                        "UNRESOLVED", "Competing literal detail: business authority not established", ()))
    config = AdjudicationConfig(matrix, [], hashes=hashes, registry=registry)
    if args.policy_dir:
        config = load_adjudication_files(args.policy_dir, hashes=hashes, registry=registry, clusters=clusters)
        matrix = config.rules
    after = scan_sources(args.source, hardened=True, baseline=baseline, adjudication=config)
    reverse = scan_sources(args.source[::-1], hardened=True, baseline=baseline, adjudication=config)
    stable = canonical_signature(after) == canonical_signature(reverse)
    if not stable or hashes != {sha256_file(spec.path) for spec in args.source}:
        raise ValueError("source_or_canonical_dataset_unstable")
    reviews = review_pack(clusters, after)
    # No business authority or comparable-revision proof was supplied in this corpus.
    artifacts = {"conflict_clusters": clusters, "source_authority_matrix": [asdict(r) for r in matrix],
                 "source_inspection": inspections, "verified_evidence_registry": registry,
                 "grain_analysis": grain_analysis,
                 "source_map": [{"source_hash": s["source_hash"], "sheet": s["sheet"], "purpose": s["purpose"],
                                  "grain": (s["header"] or {}).get("fields", {}),
                                  "period_metric_columns": [v for v in (s["header"] or {}).get("fields", {}).values() if HEADER_PERIOD.search(v)],
                                  "direct_vs_derived": "Per-metric header classification; no global sheet preference",
                                  "snapshot_proof": None, "known_issues": s["known_issues"], "authority_status": s["authority_status"]}
                                 for s in inspections],
                 "snapshot_proofs": [asdict(p) for p in config.proofs],
                 "manual_overrides": json.loads((args.policy_dir / "manual_overrides.json").read_text(encoding="utf-8")) if args.policy_dir else [],
                 "manual_review_pack": reviews,
                 "same_snapshot_dossier": [c for c in clusters if c["reason"] == "SAME_SNAPSHOT_CONFLICT"],
                 "pareto": pareto(clusters), "resolution_summary": resolution_counts(before, after),
                 "canonical_preview": after["summary"], "determinism": {"status": "PASS", "counts": True,
                    "versions": True, "conflicts": True, "sha": after["summary"]["dataset_sha256"]}}
    for name, value in artifacts.items():
        write_new(output / f"{name}.json", value)
    for directory, report in (("canonical", after), ("reversed", reverse)):
        for field in ("resolution_ledger", "identity_ledger"):
            write_new(output / directory / f"{field}.json", report[field])
        write_new(output / directory / "reconciliation.json", report)
    summary = "# Grouped human review — NOT APPROVED\n\n"
    for decision in reviews:
        summary += (f"## {decision['decision_id']}\n\n{decision['affected_facts']} facts; {decision['chain']}; "
                    f"{decision['period_min']}..{decision['period_max']}; {', '.join(decision['metrics'])}.\n\n"
                    f"Pattern: {decision['difference_pattern']}.\n\n"
                    f"{decision['recommended_interpretation']}\n\nAlternative: {decision['alternative_interpretation']}\n\n"
                    f"Risk: {decision['risk']}\n\nSee private JSON for hashes, sheets, cluster IDs and evidence.\n\n")
    write_new(output / "manual_review_summary.md", summary)
    print(json.dumps({"pareto": pareto(clusters), "resolution": resolution_counts(before, after),
                      "manual_decisions": len(reviews), "canonical": after["summary"],
                      "publication": "BLOCKED" if after["blocking"] or reviews else "PREFLIGHT_REQUIRED"}, indent=2))


if __name__ == "__main__":
    main()
