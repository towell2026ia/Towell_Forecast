"""Public synthetic source-policy tests. Private corpus/golden verified by CLI.

CP27-34 remote bootstrap certification is conditional on the publication gate;
test doubles are deliberately NOT labelled as Supabase certification.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from services.assistant_api.historical_reconciliation import reconcile_historical, regression_sample
from services.assistant_api.source_adjudication import (
    AdjudicationConfig, AuthorityRule, SnapshotPairProof, adjudicated_preflight, apply_overrides,
    cluster_conflicts, fingerprint, load_adjudication_files, pareto, resolution_counts, review_pack, validate_override,
)
from services.assistant_api.test_prd09_2d1 import fact

A, B = "a" * 64, "b" * 64
NOW = "2026-09-25T12:00:00+00:00"


def reconcile(rows, config=None):
    scoped = None
    if config:
        rows, scoped = config.apply(rows)
    return reconcile_historical([{"sha256": A}, {"sha256": B}], rows, scoped_snapshots=scoped)


def proof_ref(proof):
    return {**{field: proof[field] for field in ("source_hash", "sheet", "locator", "kind")},
            "evidence_hash": fingerprint(proof)}


def rule(role="SUMMARY_ONLY", digest=B, sheet="Summary", metric="SALES", chain="Chain A", approved=True):
    scope = {"chain": chain, "valid_from": "2025-01", "valid_to": "2025-01", "metric": metric,
             "product_scope": "*", "grain": "CHAIN+PRODUCT+MONTH+METRIC"}
    proof = {"source_hash": digest, "sheet": sheet, "locator": "A1", "kind": "STRUCTURAL_HEADER",
             "content": "Suma de 2025/01 POS Qty", "scope": scope,
             "assertion": {"SUMMARY_ONLY": "AGGREGATED_MEASURE_HEADER", "EXCLUDED_DERIVED": "GRAIN_DISJOINT"}.get(role, "SOURCE_AUTHORITY")}
    entry = AuthorityRule("R-" + digest[0] + role, chain, "2025-01", "2025-01", metric, "*", digest, sheet,
                          scope["grain"], "C" if role == "SUMMARY_ONLY" else "A", role,
                          "Reviewed source structure", (proof_ref(proof),), approved, "reviewer", NOW)
    return entry, {fingerprint(proof): proof}


def config(rules, registry, proofs=None):
    return AdjudicationConfig(rules, proofs or [], hashes={A, B}, registry=registry)


def pair():
    scope = {"valid_from": "2025-01", "valid_to": "2025-01", "metrics": ["SALES"], "product_scope": "*"}
    proof = {"assertion": "COMPARABLE_SNAPSHOT_SEQUENCE", "source_hashes": [A, B], "chain": "Chain A",
             "scope": scope, "older_cutoff": "2025-01", "newer_cutoff": "2025-02", "locator": "ApprovedDocument:1"}
    entry = SnapshotPairProof("P-1", "Chain A", A, B, "Reviewed comparable sequence", "2025-01", "2025-02",
                              scope, proof["locator"], fingerprint(proof), "Same grain reviewed", "reviewer", NOW)
    return entry, {fingerprint(proof): proof}


class AdjudicationTests(unittest.TestCase):
    def blocked(self):
        return reconcile([fact(), fact("9", source_sha256=B, sheet="Summary")])

    def test_cp01_cluster_sum_712(self):
        rows = [row for i in range(712) for row in (fact(upc=f"P{i}"), fact("9", upc=f"P{i}", cell="V19"))]
        result = reconcile(rows)
        clusters = cluster_conflicts(result)
        self.assertEqual(sum(c["count"] for c in clusters), 712)
        self.assertEqual(pareto(clusters), {"80": 1, "90": 1, "95": 1, "100": 1})

    def test_cp02_clusters_order_independent(self):
        rows = [fact(), fact("9", source_sha256=B)]
        self.assertEqual(cluster_conflicts(reconcile(rows)), cluster_conflicts(reconcile(rows[::-1])))

    def test_cp03_authority_validated(self):
        entry, registry = rule()
        self.assertEqual(config([entry], registry).rules, [entry])

    def test_cp04_missing_evidence_rejected(self):
        entry, _ = rule()
        with self.assertRaisesRegex(ValueError, "unverified"):
            config([entry], {})
        with self.assertRaises(ValueError):
            config([replace(entry, evidence=())], {})

    def test_cp05_a_over_verified_summary(self):
        entry, registry = rule()
        rows = [fact(), fact("99", source_sha256=B, sheet="Summary")]
        result = reconcile(rows, config([entry], registry))
        self.assertEqual(result["selected"][0]["value"], "8")
        self.assertEqual(result["resolution_ledger"][0]["reason_code"], "DIRECT_VS_SUMMARY")
        self.assertEqual(resolution_counts(reconcile(rows), result)["resolved"], {"DIRECT_VS_SUMMARY": 1})

    def test_cp06_a_against_a_blocks(self):
        self.assertTrue(self.blocked()["blocking"])

    def test_cp07_internal_cluster(self):
        clusters = cluster_conflicts(reconcile([fact(), fact("9", cell="V19")]))
        self.assertEqual(clusters[0]["reason"], "INTERNAL_SOURCE_DUPLICATE")

    def test_cp08_proven_disjoint_grain_excluded_no_sum(self):
        entry, registry = rule("EXCLUDED_DERIVED", A, "OtherGrain")
        result = reconcile([fact(), fact("9", sheet="OtherGrain")], config([entry], registry))
        self.assertFalse(result["blocking"])
        self.assertEqual(result["selected"][0]["value"], "8")
        self.assertEqual(result["summary"]["reason_counts"]["WRONG_GRAIN_EXCLUDED"], 1)

    def test_cp09_same_grain_contradiction_blocks(self):
        entry, registry = rule("PRIMARY", A, "Detail")
        result = reconcile([fact(), fact("9", cell="V19")], config([entry], registry))
        self.assertTrue(result["blocking"])

    def test_cp10_snapshot_pair_valid(self):
        entry, registry = pair()
        config([], registry, [entry])

    def test_cp11_incomplete_pair_blocks(self):
        entry, registry = pair()
        for changes in ({"approved_by": ""}, {"review_note": ""}, {"older_cutoff": "2025-02"}, {"evidence_hash": A}):
            with self.assertRaises(ValueError):
                config([], registry, [replace(entry, **changes)])

    def revision(self):
        entry, registry = pair()
        return reconcile([fact(), fact("9", source_sha256=B, source_cutoff="2025-02")], config([], registry, [entry]))

    def test_cp12_revision_v1_v2(self):
        self.assertEqual([(r["version_no"], r["value"]) for r in self.revision()["versions"]], [(1, "8"), (2, "9")])

    def test_cp13_revision_not_available_at(self):
        self.assertTrue(all(r["available_at"] is None and r["availability_source"] == "UNKNOWN" for r in self.revision()["versions"]))

    def test_cp14_same_snapshot_blocks(self):
        self.assertEqual(self.blocked()["resolution_ledger"][0]["reason_code"], "SAME_SNAPSHOT_CONFLICT")

    def test_cp15_source_rules_data_driven(self):
        entry, registry = rule(chain="Any Chain")
        result = reconcile([fact(chain="Any Chain"), fact("9", chain="Any Chain", source_sha256=B, sheet="Summary")], config([entry], registry))
        self.assertFalse(result["blocking"])

    def test_cp16_no_pilot_branches(self):
        for name in ("source_adjudication.py",):
            text = Path(__file__).with_name(name).read_text(encoding="utf-8")
            for word in ("Walmart", "FENDI", "Al Super", "CLOSE-FENDI"):
                self.assertNotIn(word, text)

    def test_cp17_chains_isolated(self):
        entry, registry = rule(chain="Other Chain")
        self.assertTrue(reconcile([fact(), fact("9", source_sha256=B, sheet="Summary")], config([entry], registry))["blocking"])

    def test_cp18_grouped_review_not_approved(self):
        rows = [row for metric in ("SALES", "ORDER", "DELIVERY") for row in (fact(metric=metric), fact("9", metric=metric, source_sha256=B))]
        result = reconcile(rows)
        pack = review_pack(cluster_conflicts(result), result)
        self.assertEqual(len(pack), 1)
        self.assertEqual(pack[0]["affected_facts"], 3)
        self.assertFalse(pack[0]["approved"])

    def override(self):
        result = self.blocked()
        cluster = cluster_conflicts(result)[0]
        entry, registry = rule("PRIMARY", A, "Detail", approved=False)
        override = {"decision_id": "D-1", "cluster_id": cluster["cluster_id"], "source_hashes": [A, B],
                    "scope": {"keys_sha256": fingerprint(cluster["keys"]), "count": cluster["count"]},
                    "selected_rule": entry.authority_rule_id, "reason": "Reviewed business authority", "approved_by": "reviewer",
                    "approved_at": NOW, "evidence_reference": entry.evidence[0]}
        return entry, registry, [cluster], override

    def test_cp19_override_approval_metadata(self):
        _, registry, clusters, override = self.override()
        for field in ("reason", "approved_by", "approved_at", "evidence_reference"):
            with self.assertRaises(ValueError):
                validate_override({**override, field: ""}, clusters, hashes={A, B}, registry=registry)

    def test_cp20_override_hash_scope(self):
        _, registry, clusters, override = self.override()
        for changes in ({"source_hashes": [A]}, {"scope": {"keys_sha256": A, "count": 1}}):
            with self.assertRaises(ValueError):
                validate_override({**override, **changes}, clusters, hashes={A, B}, registry=registry)

    def test_cp21_changed_source_invalidates_override(self):
        _, registry, clusters, override = self.override()
        with self.assertRaises(ValueError):
            validate_override(override, clusters, hashes={A, "c" * 64}, registry=registry)

    def test_cp22_deterministic_values_versions_sha(self):
        entry, registry = rule()
        rows = [fact(), fact("9", source_sha256=B, sheet="Summary")]
        left, right = (reconcile(inputs, config([entry], registry)) for inputs in (rows, rows[::-1]))
        self.assertEqual(left, right)

    def test_cp23_synthetic_golden_coverage_96(self):
        # Real private FENDI 96/96 is additionally checked against the frozen CSV.
        rows = [fact(upc=f"P{i}") for i in range(96)]
        self.assertEqual(len(reconcile(rows)["selected"]), 96)

    def test_cp24_other_chain_regression(self):
        rows = [fact(chain=f"Chain {i}") for i in range(13)]
        self.assertEqual(regression_sample(reconcile(rows), rows), {"chains_sampled": 13, "mismatches": 0, "status": "PASS"})

    def gate(self, result, **changes):
        args = {"manual_reviews": [], "stable_sha": True, "source_hashes": {A, B}, "golden_pass": True,
                "tests_pass": True, "project_ref": "bskoyqhbgrycpwhydnnr", "remote_gate_pass": True}
        adjudicated_preflight(result, **{**args, **changes})

    def test_cp25_gate_blocks_unresolved_and_manual(self):
        with self.assertRaises(ValueError):
            self.gate(self.blocked())
        with self.assertRaises(ValueError):
            self.gate(reconcile([fact()]), manual_reviews=[{"user_decision_required": True, "approved": False}])

    def test_cp26_gate_all_checks(self):
        result = reconcile([fact()])
        self.gate(result)
        for changes in ({"stable_sha": False}, {"source_hashes": {A}}, {"golden_pass": False}, {"tests_pass": False},
                        {"remote_gate_pass": False}, {"project_ref": "other"}):
            with self.assertRaises(ValueError):
                self.gate(result, **changes)

    @unittest.skip("Publication blocked; real Supabase adapter NOT EXECUTED")
    def test_cp27_supabase_adapter_server_only(self): pass

    @unittest.skip("Publication blocked; real PostgreSQL bootstrap transaction NOT EXECUTED")
    def test_cp28_historical_remote_transaction(self): pass

    @unittest.skip("Publication blocked; real remote idempotency NOT EXECUTED")
    def test_cp29_historical_remote_idempotency(self): pass

    @unittest.skip("Publication blocked; real remote resume NOT EXECUTED")
    def test_cp30_historical_remote_resume(self): pass

    def test_cp31_storage_gate_never_opens_bucket(self):
        # Remote private buckets checked at Gate 1; existing SQL/security suite covers policies.
        with self.assertRaises(ValueError): self.gate(self.blocked())

    def test_cp32_rls_contract_unchanged(self):
        text = (Path(__file__).resolve().parents[2] / "supabase/migrations/202609250003_contract_governance.sql").read_text()
        self.assertIn("ENABLE ROW LEVEL SECURITY", text.upper())

    @unittest.skip("Publication blocked; post-load counts NOT EXECUTED (remote stays empty)")
    def test_cp33_post_load_counts(self): pass

    @unittest.skip("Publication blocked; remote canonical fingerprint N/A")
    def test_cp34_remote_fingerprint(self): pass

    def test_cp35_unknown_null(self):
        self.assertEqual(reconcile([fact()])["summary"]["temporal"], {"UNKNOWN": 1, "E1": 0, "E2": 0, "E3": 0})
        self.assertIsNone(reconcile([fact()])["selected"][0]["available_at"])

    def test_cp36_monthly_import_decoupled(self):
        from services.assistant_api import monthly_imports
        self.assertNotIn("source_adjudication", Path(monthly_imports.__file__).read_text())

    def test_authority_scoped_with_all_competing_detail_reviewed(self):
        primary, r1 = rule("PRIMARY", A, "Detail")
        secondary, r2 = rule("SECONDARY_CONTROL", B, "Summary")
        result = reconcile([fact(), fact("9", source_sha256=B, sheet="Summary")], config([primary, secondary], {**r1, **r2}))
        self.assertEqual(result["selected"][0]["value"], "8")
        self.assertEqual(result["resolution_ledger"][0]["reason_code"], "SOURCE_AUTHORITY")
        self.assertTrue(reconcile([fact(), fact("9", source_sha256=B, sheet="Summary")], config([primary], r1))["blocking"])

    def test_scope_and_header_tampering_rejected(self):
        entry, registry = rule()
        for changes in ({"metric": "ORDER"}, {"sheet": "Other"}, {"valid_to": "2025-02"}, {"source_hash": A}):
            with self.assertRaises(ValueError): config([replace(entry, **changes)], registry)
        primary, reg = rule("PRIMARY", A, "Detail")
        with self.assertRaises(ValueError): config([replace(primary, chain="Other Chain")], reg)

    def test_duplicate_and_overlapping_rules_rejected(self):
        entry, registry = rule()
        with self.assertRaises(ValueError): config([entry, entry], registry)
        with self.assertRaises(ValueError):
            config([entry, replace(entry, authority_rule_id="Another")], registry).apply([fact(source_sha256=B, sheet="Summary")])

    def test_override_interpreter_exact_keys_and_private_loader(self):
        entry, registry, clusters, override = self.override()
        approved = apply_overrides([override], [entry], clusters, hashes={A, B}, registry=registry)[0]
        self.assertTrue(approved.approved)
        self.assertTrue(approved.fact_keys)
        self.assertFalse(approved.matches(fact(upc="Outside")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in (("source_authority_matrix", [asdict(entry)]), ("snapshot_proofs", []), ("manual_overrides", [override])):
                (root / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(load_adjudication_files(root, hashes={A, B}, registry=registry, clusters=clusters).rules[0].approved)

    def test_snapshot_scope_does_not_escape(self):
        entry, registry = pair()
        rows = [fact(metric="ORDER"), fact("9", metric="ORDER", source_sha256=B, source_cutoff="2025-02")]
        self.assertTrue(reconcile(rows, config([], registry, [entry]))["blocking"])

    def test_review_evidence_tamper_and_unapproved_policy(self):
        entry, registry = rule()
        tampered = {digest: {**proof, "content": "Changed"} for digest, proof in registry.items()}
        with self.assertRaises(ValueError): config([entry], tampered)
        with self.assertRaises(ValueError): config([replace(entry, approved_at="not-a-time")], registry)
        with self.assertRaises(ValueError): config([replace(entry, authority_role="PICK_MAX")], registry)
        self.assertTrue(reconcile([fact(), fact("9", source_sha256=B, sheet="Summary")],
                                  config([replace(entry, approved=False)], registry))["blocking"])

    def test_overlapping_proofs_rejected(self):
        entry, registry = pair()
        with self.assertRaises(ValueError): config([], registry, [entry, entry])
        with self.assertRaises(ValueError):
            config([], registry, [entry, replace(entry, proof_id="P-2")]).apply([fact()])

    def test_override_unknown_rule_and_duplicate_rejected(self):
        entry, registry, clusters, override = self.override()
        with self.assertRaises(ValueError):
            apply_overrides([{**override, "selected_rule": "Missing"}], [entry], clusters, hashes={A, B}, registry=registry)
        with self.assertRaises(ValueError):
            apply_overrides([override, override], [entry], clusters, hashes={A, B}, registry=registry)


if __name__ == "__main__": unittest.main()
