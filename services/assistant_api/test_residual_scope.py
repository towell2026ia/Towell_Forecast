"""CP01-36: public synthetic proofs, plus complete optional private regression."""
from copy import deepcopy
from dataclasses import asdict, replace
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from services.assistant_api import residual_scope as module
from services.assistant_api.global_certification import certify_global_corpus, compare_global_golden, freeze_global_golden
from services.assistant_api.hierarchical_grain import SourceScope
from services.assistant_api.settings import Settings
from services.assistant_api.source_adjudication import fingerprint
from services.assistant_api.test_business_membership import authority
from services.assistant_api.test_chain_ingestion import HASH, profile, sheet
from services.assistant_api.test_hierarchical_grain import raw, replay
from scripts.close_residual_fact_scope import run
from scripts.certify_global_corpus import verify_pins

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "outputs/prd09_2d232"
RULE = "d" * 64


def fixture(name="Pending", unit="Group::Pending"):
    ps = [raw(), replace(profile(name, unit), sheet_role="CHAIN_CALC"), raw("ConflictA"), raw("ConflictB")]
    books = {HASH: {"Raw": sheet(upc=None), name: sheet(8, item=2002, upc="009876543210"),
                    "ConflictA": sheet(4, item=3003, upc=None), "ConflictB": sheet(9, item=3003, upc=None)}}
    books[HASH][name]["rows"][1]["D"] = "Reviewed independent quantity declaration"
    report, _, scopes, _ = replay(books[HASH], ps, {name: {"fact_grain": "UNKNOWN_SCOPE", "parent_chain": ""}})
    members = authority([ps[1]])
    inventory, registry, adapted, memberships = module.inventory_residuals(report, books, ps, members, scopes)
    return report, books, ps, members, scopes, inventory, registry, adapted, memberships


def rule_for(entry, p, books, kind="PARENT_CHAIN", parent="Holding", unit=""):
    scope = SourceScope(HASH, p.sheet_name, parent, kind, unit, {"chain": p.chain_column, "format": p.format_column},
                        {"source_hash": HASH, "rule_sha256": RULE, "locator": "D1", "basis": "Owner-reviewed synthetic quantity scope",
                         "value_scope_certified": kind == "COMMERCIAL_UNIT"}, True, fingerprint(asdict(p)), RULE)
    return {"owner_reviewed": True, "approved_by": "synthetic_owner", "rule_sha256": RULE,
            "source_hash": HASH, "sheet": p.sheet_name, "profile_sha256": fingerprint(asdict(p)), "source_scope": asdict(scope),
            "evidence_role": {"PARENT_CHAIN": "PARENT_QUANTITY_SCOPE", "COMMERCIAL_UNIT": "CHILD_QUANTITY_SCOPE",
                              "EXPLICIT_ROW_UNIT": "EXPLICIT_ROW_QUANTITY_SCOPE"}[kind],
            "literal_evidence": [{"source_sha256": HASH, "sheet": p.sheet_name, "cell": "D1",
                                  "value_fingerprint": fingerprint(books[HASH][p.sheet_name]["rows"][1]["D"])}],
            "period_min": entry["period"], "period_max": entry["period"], "metrics": [entry["metric"]]}


class ResidualContracts(unittest.TestCase):
    def setUp(self):
        (self.report, self.books, self.ps, self.authority, self.scopes, self.inventory,
         self.registry, self.adapted, self.memberships) = fixture()
        self.entry = self.inventory[0]["sources"][0]
        self.p = next(p for p in self.adapted if p.sheet_name == "Pending")
        self.rule = rule_for(self.entry, self.p, self.books)
        self.cert = certify_global_corpus(self.report, self.books, self.ps, self.authority, self.scopes, verified_source_hashes={HASH})
        self.golden = freeze_global_golden(self.cert, certified_at="METADATA_ONLY")
        self.expected = {k: self.cert["summary"][k] for k in ("selected_observations", "actual_bearing_scopes", "scope_product_identities")}
        self.expected.update(dataset_sha256=self.cert["dataset_sha256"], certification_sha256=self.cert["certification_sha256"])

    def close(self, rules=()):
        return module.close_residuals(self.report, self.inventory, self.registry, self.books, self.adapted, self.memberships, rules=rules)

    def route(self, rule=None, p=None, entry=None):
        return module.reviewed_route(entry or self.entry, self.books, p or self.p, self.memberships, rule or self.rule)

    def test_CP01_dataset_pin(self):
        self.expected["dataset_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "STOP"):
            module.verify_global_baseline(self.report, self.cert, self.golden, self.expected)

    def test_CP02_certification_pin(self):
        self.expected["certification_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "STOP"):
            module.verify_global_baseline(self.report, self.cert, self.golden, self.expected)

    def test_CP03_baseline_preserved(self):
        result = self.close([self.rule])
        self.assertEqual(result["global_v1_to_v2_trace"]["old_facts_preserved"], len(self.report["selected"]))
        self.assertEqual(result["result"]["added_facts"], 1)
        self.assertEqual(result["global_v1_to_v2_trace"]["status"], "PASS")

    def test_CP04_complete_inventory(self):
        blocked = [d for d in self.report["resolution_ledger"] if d["reason_code"] in module.SCOPE_BLOCKS and d["blocking"]]
        self.assertEqual({module.decision_id(d) for d in blocked}, {r["decision_id"] for r in self.inventory})
        self.assertTrue(all(s[k] for r in self.inventory for s in r["sources"] for k in ("source_hash", "sheet", "locator", "item", "period", "metric")))

    def test_CP05_cluster_accounting(self):
        clusters = module.cluster_inventory(self.inventory)
        self.assertEqual(sum(c["blocker_count"] for c in clusters["clusters"]), len(self.inventory))
        self.assertEqual(sum(clusters["block_type_counts"].values()), len(self.inventory))
        self.assertEqual(clusters["pareto"]["100"]["covered_blockers"], len(self.inventory))

    def test_CP06_no_silent_drop(self):
        with self.assertRaisesRegex(ValueError, "silently_dropped"):
            module.close_residuals(self.report, [], self.registry, self.books, self.adapted, self.memberships)
        self.inventory[0]["sources"] = []
        with self.assertRaisesRegex(ValueError, "source_trace"):
            self.close()

    def test_CP07_parent_evidence_required(self):
        for change in ({"owner_reviewed": False}, {"literal_evidence": []}, {"approved_by": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.route({**self.rule, **change})

    def test_CP08_child_evidence_required(self):
        rule = rule_for(self.entry, self.p, self.books, "COMMERCIAL_UNIT", "Group", "Group::Pending")
        self.assertEqual(self.route(rule)["fact_grain"], "COMMERCIAL_UNIT")
        rule["source_scope"]["evidence"]["value_scope_certified"] = False
        with self.assertRaisesRegex(ValueError, "independent_certification"):
            self.route(rule)

    def test_CP09_explicit_row_preserved(self):
        p = replace(self.p, chain_column="E", format_column="F")
        self.books[HASH]["Pending"]["rows"][2].update(E="NovelParent", F="NovelUnit")
        rule = rule_for(self.entry, p, self.books, "EXPLICIT_ROW_UNIT", "Context")
        fact = self.route(rule, p)
        self.assertEqual(fact["fact_grain"], "EXPLICIT_ROW_UNIT")
        self.assertEqual(fact["canonical_code"], "NovelParent::NovelUnit")

    def test_CP10_membership_not_ownership(self):
        self.assertTrue(self.memberships)
        self.assertEqual(self.close()["result"]["remaining_scope_blockers"], 1)
        with self.assertRaisesRegex(ValueError, "membership_or_context"):
            self.route({**self.rule, "evidence_role": "BUSINESS_SHEET_MEMBERSHIP"})

    def dependency(self):
        self.books[HASH]["Pending"]["rows"][2].update(A=1001, C='=IFERROR(_xlfn.XLOOKUP($A:$A,Raw!$A:$A,Raw!$C:$C),0)')
        source = {"source_sha256": HASH, "sheet": "Pending", "cell": "C2", "key": ["unknown", "1001", "2024-01", "SALES"]}
        return module.prove_dependency(source, self.p, self.books, self.report), source

    def test_CP11_derived_not_fact(self):
        proof, _ = self.dependency()
        self.assertEqual(proof["status"], "DERIVED_REPRESENTATION_NOT_A_FACT")
        self.assertFalse(proof["creates_fact"])
        self.assertFalse(proof["formula_evaluated"])
        self.assertEqual(len(self.report["selected"]), 1)

    def test_CP12_dependency_lineage(self):
        proof, source = self.dependency()
        self.assertEqual(proof["target_locator"], [HASH, "Raw", "C2"])
        self.assertEqual(proof["canonical_target_key"], self.report["selected"][0]["key"])
        self.books[HASH]["Raw"]["rows"][3] = dict(self.books[HASH]["Raw"]["rows"][2])
        self.assertIsNone(module.prove_dependency(source, self.p, self.books, self.report))

    def test_CP13_nonfact_no_observation(self):
        p = replace(self.p, value_role="NONE", sheet_role="FORECAST")
        with self.assertRaises(ValueError):
            self.route(p=p)
        adapted = [p if x.sheet_name == p.sheet_name else x for x in self.adapted]
        result = module.close_residuals(self.report, self.inventory, self.registry, self.books, adapted, self.memberships, rules=[self.rule])
        self.assertEqual(result["result"]["added_facts"], 0)
        self.assertEqual(result["result"]["remaining_scope_blockers"], 1)
        rule = {**self.rule, "disposition": "NON_FACT_SOURCE", "evidence_role": "NON_FACT_SOURCE_EXCLUSION",
                "exclusion_basis": "Independently reviewed source declaration: planning, not actual quantity"}
        result = self.close([rule])
        self.assertEqual(result["result"]["added_facts"], 0)
        self.assertEqual(result["scope_resolution_ledger"][0]["outcome"], "NON_FACT_SOURCE")
        self.assertTrue(result["scope_resolution_ledger"][0]["nonfact_exclusion_proofs"])
        with self.assertRaisesRegex(ValueError, "unreviewed_nonfact"):
            self.close([{**rule, "exclusion_basis": ""}])

    def test_CP14_hash_bound(self):
        with self.assertRaises(ValueError):
            self.route({**self.rule, "source_hash": "e" * 64})
        rule = deepcopy(self.rule)
        rule["literal_evidence"][0]["source_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "scope_bound"):
            self.route(rule)

    def test_CP15_sheet_profile_bound(self):
        for change in ({"sheet": "*"}, {"sheet": "Other"}, {"profile_sha256": "e" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.route({**self.rule, **change})
        rule = deepcopy(self.rule)
        rule["literal_evidence"][0]["sheet"] = "Raw"
        with self.assertRaises(ValueError):
            self.route(rule)

    def test_CP16_ambiguous_parent_blocks(self):
        self.entry["possible_parent"] = ["Group", "Another"]
        self.assertEqual(self.close()["scope_resolution_ledger"][0]["outcome"], "BLOCKED")
        self.assertEqual(module._block_type("TRUE_SCOPE_BLOCKER", raw(), ["A", "B"], []), "MULTIPLE_POSSIBLE_PARENT")

    def test_CP17_ambiguous_child_blocks(self):
        self.entry["possible_child"] = ["Group::A", "Group::B"]
        self.assertEqual(self.close()["result"]["added_facts"], 0)
        self.assertEqual(module._block_type("TRUE_SCOPE_BLOCKER", profile(), ["A"], ["A::X", "A::Y"]), "MULTIPLE_POSSIBLE_CHILD")

    def test_CP18_parent_only_valid(self):
        fact = module.reviewed_route(self.entry, self.books, self.p, [], self.rule)
        self.assertEqual(fact["fact_grain"], "PARENT_CHAIN")
        self.assertTrue(fact["parent_only"])
        self.assertEqual(fact["children"], [])

    def test_CP19_no_fanout(self):
        original = deepcopy(self.entry)
        fact = self.report["selected"][0]
        self.entry.update(source_hash=fact["source_sha256"], sheet=fact["sheet"], locator=fact["cell"])
        self.assertRaises(ValueError, self.close)
        result = self.route(entry=original)
        self.assertEqual(result["fact_grain"], "PARENT_CHAIN")

    def test_CP20_description_not_identity(self):
        fact = self.route()
        self.books[HASH]["Pending"]["rows"][2]["D"] = "Entirely new description"
        self.assertEqual(fact["key"], self.route()["key"])
        self.books[HASH]["Pending"]["rows"][2]["A"] = 9009
        with self.assertRaisesRegex(ValueError, "identity_not_literal"):
            self.route()

    def test_CP21_literal_certification(self):
        fact = self.route()
        self.assertTrue(module.new_proof_valid(fact))
        forged = deepcopy(fact)
        forged["value"] = "999"
        self.assertFalse(module.new_proof_valid(forged))
        self.assertFalse(module.new_proof_valid({"residual_scope_proof": {"literal_evidence": ["fake"]}}))
        self.books[HASH]["Pending"]["rows"][2]["C"] = 999
        with self.assertRaisesRegex(ValueError, "literal_period_metric"):
            self.route()

    def test_CP22_old_values_unchanged(self):
        candidate = deepcopy(self.report)
        candidate["selected"][0]["value"] = "99"
        trace = module.trace_global_v1(self.report, candidate)
        self.assertEqual(trace["changed_values"], 1)
        self.assertEqual(trace["status"], "BLOCKED")

    def test_CP23_no_loss(self):
        candidate = deepcopy(self.report)
        candidate["selected"] = []
        self.assertEqual(len(module.trace_global_v1(self.report, candidate)["missing"]), 1)
        candidate["selected"] = self.report["selected"] * 2
        self.assertEqual(module.trace_global_v1(self.report, candidate)["unexpected_duplicates"], 1)

    def test_CP24_no_regrain(self):
        candidate = deepcopy(self.report)
        candidate["selected"][0]["key"][0] = "invented"
        self.assertEqual(module.trace_global_v1(self.report, candidate)["unsupported_regrain"], 1)

    def test_CP25_conflicts_preserved(self):
        result = self.close([self.rule])
        trace = result["value_conflict_trace"]
        self.assertEqual((trace["before"], trace["remaining_original"], trace["resolved_by_scope_separation"]), (1, 1, 0))
        self.assertTrue(all(not d["winner_selected"] for d in trace["decisions"]))
        originals = [d for d in self.report["resolution_ledger"] if d["reason_code"] in module.VALUE_REASONS]
        after = [d for d in result["canonical_candidate"]["resolution_ledger"] if d["reason_code"] in module.VALUE_REASONS]
        self.assertEqual(originals, after)

    def test_CP26_both_scopes_proven(self):
        a = self.route()
        b = self.route(rule_for(self.entry, self.p, self.books, "COMMERCIAL_UNIT", "Group", "Group::Pending"))
        decision = {"key": a["key"], "sources": [a, b]}
        # Reusing one physical literal in two scopes is forbidden, even with two signed projections.
        self.assertFalse(module.validate_scope_separation(decision, [a, b]))
        self.assertFalse(module.validate_scope_separation(decision, [a]))
        p = replace(self.p, sheet_name="Independent")
        self.books[HASH][p.sheet_name] = deepcopy(self.books[HASH]["Pending"])
        entry = {**self.entry, "sheet": p.sheet_name}
        independent = self.route(rule_for(entry, p, self.books, "COMMERCIAL_UNIT", "Group", "Group::Independent"), p, entry)
        distinct = {"key": a["key"], "sources": [a, independent]}
        self.assertTrue(module.validate_scope_separation(distinct, [a, independent]))
        b["residual_scope_proof"] = {}
        self.assertFalse(module.validate_scope_separation(decision, [a, b]))

    def test_CP27_dynamic_chain(self):
        args = fixture("NovelSheet", "Unseen::Retail")
        report, books, _, _, _, inventory, registry, adapted, memberships = args
        p = next(p for p in adapted if p.sheet_name == "NovelSheet")
        rule = rule_for(inventory[0]["sources"][0], p, books, "COMMERCIAL_UNIT", "Unseen", "Unseen::Retail")
        result = module.close_residuals(report, inventory, registry, books, adapted, memberships, rules=[rule])
        self.assertEqual(result["result"]["added_facts"], 1)
        self.assertTrue(any(f["canonical_code"] == "Unseen::Retail" for f in result["canonical_candidate"]["selected"]))
        catalog = {s["scope_id"]: s for s in result["canonical_candidate"]["fact_scope_catalog"]}
        self.assertTrue(all(s["parent_id"] in catalog for s in catalog.values() if s["parent_id"]))

    def test_CP28_no_retailer_branch(self):
        self.assertNotIn("walmart", inspect.getsource(module).lower())

    def test_CP29_no_product_branch(self):
        self.assertNotIn("fendi", inspect.getsource(module).lower())

    def test_CP30_source_order(self):
        books = {HASH: dict(reversed(list(self.books[HASH].items())))}
        inv, reg, ps, members = module.inventory_residuals(self.report, books, self.ps, self.authority, self.scopes)
        self.assertEqual((inv, reg), (self.inventory, self.registry))
        self.assertEqual(fingerprint(self.close()), fingerprint(module.close_residuals(self.report, inv, reg, books, ps, members)))

    def test_CP31_profile_order(self):
        inv, reg, *_ = module.inventory_residuals(self.report, self.books, self.ps[::-1], self.authority, self.scopes)
        self.assertEqual((inv, reg), (self.inventory, self.registry))

    def test_CP32_unknown_null(self):
        result = self.close([self.rule])
        self.assertTrue(all(f["available_at"] is None and f["availability_source"] == "UNKNOWN" for f in result["canonical_candidate"]["selected"]))

    def test_CP33_global_golden(self):
        before = fingerprint(self.golden)
        self.assertEqual(module.verify_global_baseline(self.report, self.cert, self.golden, self.expected)["status"], "PASS")
        self.close([self.rule])
        self.assertEqual(before, fingerprint(self.golden))
        self.assertEqual(compare_global_golden(self.golden, self.cert)["status"], "PASS")

    def test_CP34_legacy_not_scope_authority(self):
        # Golden arguments are accepted only by baseline regression, never by routing.
        self.assertNotIn("golden", inspect.signature(module.reviewed_route).parameters)
        self.assertNotIn("golden", inspect.signature(module.close_residuals).parameters)

    def test_CP35_runtime_flags(self):
        s = Settings().validate()
        self.assertEqual((s.persistence_provider, s.data_provider), ("sqlite", "normalized"))
        self.assertFalse(any((s.supabase_enabled, s.openai_enabled, s.voice_enabled, s.deep_research_enabled, s.legacy_pilot_enabled)))
        self.assertFalse(self.close()["result"]["runtime_modified"])

    def test_CP36_zero_writes(self):
        self.assertEqual(self.close()["result"]["supabase_writes"], 0)
        self.assertNotIn("supabase.create_client", inspect.getsource(module))
        self.assertNotIn("sqlite3", inspect.getsource(module))

    def test_neighbour_formula_cannot_remove_literal(self):
        self.books[HASH]["Pending"]["rows"][2]["G"] = "=Raw!C2"
        source = {"source_sha256": HASH, "sheet": "Pending", "cell": "C2", "key": self.inventory[0]["canonical_key"]}
        self.assertIsNone(module.prove_dependency(source, self.p, self.books, self.report))

    def test_rule_domain_and_literal_evidence(self):
        for change in ({"metrics": ["DELIVERY"]}, {"period_max": "2023-12"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.route({**self.rule, **change})
        self.books[HASH]["Pending"]["rows"][1]["D"] = "changed declaration"
        with self.assertRaisesRegex(ValueError, "evidence_cell_changed"):
            self.route()

    def test_ambiguous_rules_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_reviewed"):
            self.close([self.rule, self.rule])
        with self.assertRaisesRegex(ValueError, "target_residual"):
            self.close([{**self.rule, "sheet": "Raw"}])

    def matching_parent(self, value):
        self.books[HASH]["Raw"]["rows"][2].update(A=2002, C=value)
        report, _, scopes, _ = replay(self.books[HASH], self.ps, {"Pending": {"fact_grain": "UNKNOWN_SCOPE", "parent_chain": ""}})
        inv, reg, ps, members = module.inventory_residuals(report, self.books, self.ps, self.authority, scopes)
        return module.close_residuals(report, inv, reg, self.books, ps, members, rules=[self.rule]), report

    def test_duplicate_representation_has_explicit_proof(self):
        result, report = self.matching_parent(8)
        self.assertEqual(result["result"]["added_facts"], 0)
        self.assertEqual(result["scope_resolution_ledger"][0]["outcome"], "DUPLICATE_REPRESENTATION")
        self.assertEqual(len(result["duplicate_representation_lineage"]), 1)
        self.assertEqual(result["canonical_candidate"]["selected"], report["selected"])

    def test_new_quantity_conflict_quarantined(self):
        result, report = self.matching_parent(7)
        self.assertEqual(result["result"]["added_facts"], 0)
        self.assertEqual(result["canonical_candidate"]["selected"], report["selected"])
        pending = result["value_conflict_trace"]["new_pending_value_conflicts"]
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["winner_selected"])
        self.assertTrue(result["canonical_candidate"]["blocking"])

    def test_cli_no_overwrite_independent_of_private_corpus(self):
        # CI has no private dataset; construct our own occupied output directory.
        (ROOT / "outputs").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as tmp:
            path = Path(tmp)
            (path / "sentinel").write_text("frozen", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no_overwrites"):
                run(path / "missing_contract", "unused", path)
            self.assertEqual((path / "sentinel").read_text(), "frozen")


@unittest.skipUnless((PRIVATE / "result.json").exists(), "complete private residual corpus not distributed")
class CompletePrivateResidualGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in PRIVATE.glob("*.json")}

    def test_complete_CP01_CP05(self):
        a = self.artifacts
        self.assertEqual(a["baseline_gate"]["observations"], 39270)
        self.assertEqual(len(a["fact_scope_inventory"]), 1538)
        self.assertEqual(sum(c["blocker_count"] for c in a["fact_scope_clusters"]["clusters"]), 1538)
        self.assertEqual(sum(a["fact_scope_clusters"]["block_type_counts"].values()), 1538)

    def test_complete_CP03_CP06_CP22_CP24(self):
        a = self.artifacts
        trace = a["global_v1_to_v2_trace"]
        self.assertEqual(trace["old_facts_preserved"], 39270)
        self.assertEqual(trace["changed_values"], 0)
        self.assertEqual((trace["missing"], trace["changed_facts"], trace["unexpected_duplicates"]), ([], [], 0))
        self.assertEqual(len(a["scope_resolution_ledger"]), 1538)
        self.assertEqual(sum(r["affected_facts"] for r in a["review_decisions"]), a["result"]["remaining_scope_blockers"])

    def test_complete_CP25_CP30_CP32(self):
        a = self.artifacts
        self.assertEqual(a["value_conflict_trace"]["before"], 720)
        self.assertEqual(a["value_conflict_trace"]["remaining_original"], 720)
        self.assertTrue(all(a["determinism"]["checks"].values()))
        self.assertTrue(all(f["available_at"] is None and f["availability_source"] == "UNKNOWN" for f in a["canonical_candidate"]["selected"]))

    def test_complete_CP33_CP34_CP36(self):
        a = self.artifacts
        verify_pins(json.loads((ROOT / "outputs/prd09_2d232_contract.json").read_text(encoding="utf-8"))["files"])
        self.assertEqual(a["regressions"]["global_literal_checks"], 39270)
        self.assertEqual(a["regressions"]["legacy"]["value_matches"], 96)
        self.assertEqual((a["regressions"]["legacy"]["child_scoped"], a["regressions"]["legacy"]["parent_scoped"]), (72, 24))
        self.assertEqual(a["result"]["supabase_writes"], 0)


if __name__ == "__main__":
    unittest.main()
