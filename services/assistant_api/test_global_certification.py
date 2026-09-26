"""CP36-67: synthetic fail-closed contracts plus optional complete private corpus."""
from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from services.assistant_api import global_certification as module
from services.assistant_api.global_certification import certify_global_corpus, compare_global_golden, freeze_global_golden
from services.assistant_api.historical_corpus import sha256_file
from services.assistant_api.settings import Settings
from services.assistant_api.source_adjudication import fingerprint
from services.assistant_api.test_business_membership import authority
from services.assistant_api.test_chain_ingestion import HASH, profile, sheet
from services.assistant_api.test_hierarchical_grain import raw, replay
from scripts.certify_global_corpus import run, verify_pins

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "outputs/prd09_2d231"


def fixture():
    ps = [raw(), profile()]
    books = {HASH: {"Raw": sheet(upc=None), "Blue": sheet(8)}}
    report, _, scope, _ = replay(books[HASH], ps, {"Raw": {"parent_chain": "Parent"}, "Blue": {"parent_chain": "Parent"}})
    return report, books, ps, authority([ps[1]]), scope


def certify(args):
    return certify_global_corpus(*args, verified_source_hashes={HASH})


class GlobalContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.args = fixture()
        cls.cert = certify(cls.args)

    def test_CP36_exact_count(self):
        self.assertEqual(self.cert["summary"]["selected_observations"], 2)
        self.assertEqual(self.cert["summary"]["certified_observations"], 2)

    def test_CP37_every_selected_certified(self):
        self.assertEqual(self.cert["status"], "PASS")
        self.assertTrue(all(r["certification_status"] == "PASS" for r in self.cert["rows"]))

    def test_CP38_one_certificate_per_key(self):
        self.assertEqual(len({tuple(r["canonical_key"]) for r in self.cert["rows"]}), 2)
        self.assertTrue(all(r[k] for r in self.cert["rows"] for k in
                            ("source_hash", "sheet", "locator", "value_fingerprint", "grain_evidence", "scope_evidence", "identity_evidence")))

    def test_CP39_every_actual_scope_reported(self):
        self.assertEqual({r["fact_scope_id"] for r in self.cert["rows"]}, {s["scope_id"] for s in self.cert["scope_summary"]})

    def test_CP40_scope_accounting(self):
        self.assertEqual(sum(s["total_facts"] for s in self.cert["scope_summary"]), len(self.cert["rows"]))

    def test_CP41_metric_accounting(self):
        self.assertEqual(sum(self.cert["summary"]["metric_counts"].values()), len(self.cert["rows"]))

    def test_CP42_all_literals_rechecked(self):
        self.assertEqual(self.cert["summary"]["literal_matches"], len(self.cert["rows"]))

    def test_CP43_literal_mismatch_blocks_freeze(self):
        args = deepcopy(self.args)
        args[0]["selected"][0]["value"] = "99999"
        bad = certify(args)
        self.assertEqual(bad["status"], "BLOCKED")
        self.assertEqual(bad["summary"]["literal_mismatches"], 1)
        with self.assertRaises(ValueError):
            freeze_global_golden(bad, certified_at="metadata")

    def test_CP44_unsupported_promotion_blocks(self):
        args = deepcopy(self.args)
        args[0]["selected"][0]["canonical_code"] = "Forged::Child"
        self.assertEqual(certify(args)["status"], "BLOCKED")

    def test_CP45_unknown_selected_blocks(self):
        args = deepcopy(self.args)
        args[0]["selected"][0]["fact_grain"] = "UNKNOWN_SCOPE"
        self.assertEqual(certify(args)["summary"]["unknown_scope_selected"], 1)

    def test_CP46_parent_evidence(self):
        row = next(r for r in self.cert["rows"] if r["scope_type"] == "PARENT_CHAIN")
        self.assertEqual(row["scope_evidence"]["fact_grain"], "PARENT_CHAIN")
        self.assertTrue(row["grain_evidence"]["source_scope_sha256"])

    def test_CP47_child_evidence(self):
        row = next(r for r in self.cert["rows"] if r["scope_type"] == "COMMERCIAL_UNIT")
        self.assertTrue(row["grain_evidence"]["sheet_owned_value_certified"])

    def test_explicit_row_unit_evidence(self):
        ps = [replace(raw(), chain_column="E", format_column="F")]
        books = {HASH: {"Raw": sheet(parent="Another", fmt="ActualUnit")}}
        report, _, scope, _ = replay(books[HASH], ps)
        cert = certify((report, books, ps, authority([]), scope))
        self.assertEqual(cert["status"], "PASS")
        self.assertEqual(cert["rows"][0]["scope_type"], "EXPLICIT_ROW_UNIT")
        self.assertEqual(cert["rows"][0]["grain_evidence"]["format_locator"], "F2")

    def test_CP48_membership_never_creates_quantity(self):
        ps = [raw(), profile()]
        books = {HASH: {"Raw": sheet(upc=None), "Blue": sheet("=1+1")}}
        report, _, scope, _ = replay(books[HASH], ps)
        cert = certify((report, books, ps, authority([ps[1]]), scope))
        self.assertEqual(cert["summary"]["certified_observations"], 1)
        self.assertEqual(cert["summary"]["facts_fanned_out_due_to_membership"], 0)

    def test_CP49_fanout_rejected(self):
        args = deepcopy(self.args)
        parent, child = args[0]["selected"]
        child["source_sha256"], child["sheet"], child["cell"] = parent["source_sha256"], parent["sheet"], parent["cell"]
        bad = certify(args)
        self.assertEqual(bad["status"], "BLOCKED")
        self.assertGreater(bad["summary"]["facts_fanned_out_due_to_membership"], 0)

    def test_CP50_independent_parent_child_preserved(self):
        self.assertEqual(len(self.cert["rows"]), 2)
        self.assertEqual(self.cert["independent_scope_representations"][0]["relationship"], "INDEPENDENT_SCOPE_REPRESENTATIONS")

    def test_CP51_no_automatic_parent_child_sum(self):
        self.assertFalse(self.cert["independent_scope_representations"][0]["automatic_aggregation_allowed"])

    def test_CP52_all_products_classified(self):
        coverage = self.cert["product_coverage"]
        self.assertEqual(coverage["actual_scope_product_identities"], 2)
        self.assertEqual(sum(coverage["actual_eligibility_counts"].values()), 2)

    def test_CP53_blocked_scope_not_selected(self):
        ps = [raw()]
        books = {HASH: {"Raw": sheet()}}
        report, _, scope, _ = replay(books[HASH], ps, {"Raw": {"fact_grain": "UNKNOWN_SCOPE", "parent_chain": ""}})
        cert = certify((report, books, ps, authority([]), scope))
        self.assertFalse(cert["rows"])
        self.assertEqual(cert["status"], "BLOCKED")
        self.assertEqual(cert["blocked_trace"]["counts"]["FACT_SCOPE"], 1)

    def test_CP54_value_conflict_not_resolved(self):
        ps = [raw(), raw("Other")]
        books = {HASH: {"Raw": sheet(upc=None), "Other": sheet(8, upc=None)}}
        report, _, scope, _ = replay(books[HASH], ps)
        cert = certify((report, books, ps, authority([]), scope))
        self.assertFalse(cert["rows"])
        self.assertEqual(cert["blocked_trace"]["counts"]["VALUE"], 1)
        self.assertFalse(cert["blocked_trace"]["decisions"][0]["winner_selected"])

    def test_CP55_scope_trace_complete(self):
        trace = module.blocked_trace({**self.args[0], "resolution_ledger": [
            {"key": ["scope", "product", "2024-01", "SALES"], "reason_code": "TRUE_SCOPE_BLOCKER",
             "blocking": True, "sources": [{"source_sha256": HASH, "sheet": "Raw", "cell": "C2"}], "values": ["5"]}]})
        self.assertEqual(sum(g["blocked_keys"] for g in trace["groups"]), 1)
        self.assertEqual(trace["counts"], {"FACT_SCOPE": 1})

    def test_CP56_value_trace_grouped_without_winners(self):
        ps = [raw(), raw("Other")]
        report, *_ = replay({"Raw": sheet(upc=None), "Other": sheet(8, upc=None)}, ps)
        trace = module.blocked_trace(report)
        self.assertEqual(trace["groups"][0]["status"], "BLOCKED")
        self.assertTrue(trace["groups"][0]["question"])
        self.assertEqual(len(trace["decisions"]), sum(g["blocked_keys"] for g in trace["groups"]))

    def test_CP57_arbitrary_new_scope_dynamic(self):
        ps = [profile("NewScope", "Unseen::RetailUnit")]
        books = {HASH: {"NewScope": sheet()}}
        report, _, scope, _ = replay(books[HASH], ps)
        cert = certify((report, books, ps, authority(ps), scope))
        self.assertEqual(cert["status"], "PASS")
        self.assertEqual(cert["scope_summary"][0]["code"], "Unseen::RetailUnit")

    def test_CP58_no_pilot_or_count_hardcodes(self):
        code = inspect.getsource(module)
        for forbidden in ("FENDI", "Walmart", "39270", "39_270", "1010", "1_010"):
            self.assertNotIn(forbidden, code)

    def test_CP59_freeze_requires_pass_and_content_integrity(self):
        golden = freeze_global_golden(self.cert, certified_at="2026-09-26T00:00:00Z")
        self.assertEqual(golden["type"], "GLOBAL_HIERARCHICAL_CORPUS_GOLDEN")
        self.assertEqual(golden["facts"], 2)
        tampered = deepcopy(self.cert)
        tampered["summary"]["certified_observations"] = 999
        with self.assertRaisesRegex(ValueError, "content_changed"):
            freeze_global_golden(tampered, certified_at="metadata")

    def test_CP60_fingerprint_stable_and_detects_all_fact_changes(self):
        golden = freeze_global_golden(self.cert, certified_at="metadata")
        self.assertEqual(compare_global_golden(golden, certify(self.args))["status"], "PASS")
        for field in ("period", "metric", "stable_product", "fact_scope_id", "value_fingerprint", "locator", "source_hash"):
            bad = deepcopy(self.cert)
            bad["rows"][0][field] = "modified"
            self.assertEqual(compare_global_golden(golden, bad)["status"], "BLOCKED", field)
        bad = deepcopy(self.cert)
        bad["summary"]["selected_observations"] = 999
        self.assertEqual(compare_global_golden(golden, bad)["status"], "BLOCKED")

    def test_CP61_reverse_order_determinism(self):
        report, books, ps, membership, scope = self.args
        reverse, *_ = replay(dict(reversed(list(books[HASH].items()))), ps[::-1],
                            {"Raw": {"parent_chain": "Parent"}, "Blue": {"parent_chain": "Parent"}})
        cert = certify((reverse, books, ps[::-1], {**membership, "scopes": membership["scopes"][::-1]},
                        {**scope, "source_scopes": scope["source_scopes"][::-1]}))
        self.assertEqual(cert["certification_sha256"], self.cert["certification_sha256"])

    def test_CP64_unverified_source_rejected_and_pin_changes_detected(self):
        bad = certify_global_corpus(*self.args, verified_source_hashes=set())
        self.assertEqual(bad["status"], "BLOCKED")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source"
            path.write_bytes(b"first")
            pins = {"source": {"path": str(path), "sha256": sha256_file(path)}}
            verify_pins(pins)
            path.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_pins(pins)

    def test_CP65_unknown_requires_null(self):
        self.assertTrue(all(r["available_at"] is None and r["availability_source"] == "UNKNOWN" for r in self.cert["rows"]))
        args = deepcopy(self.args)
        args[0]["selected"][0]["available_at"] = "2026-09-26"
        self.assertEqual(certify(args)["status"], "BLOCKED")

    def test_CP66_certifier_has_no_database_writes(self):
        self.assertNotIn("supabase", inspect.getsource(module).lower())
        self.assertNotIn("sqlite", inspect.getsource(module).lower())

    def test_CP67_runtime_unchanged_off(self):
        settings = Settings()
        self.assertFalse(settings.supabase_enabled)
        self.assertEqual(settings.persistence_provider, "sqlite")
        self.assertEqual(settings.data_provider, "normalized")
        self.assertFalse(settings.openai_enabled)
        self.assertFalse(settings.voice_enabled)
        self.assertFalse(settings.deep_research_enabled)

    def test_duplicate_key_and_selected_blocked_key_rejected(self):
        args = deepcopy(self.args)
        args[0]["selected"].append(deepcopy(args[0]["selected"][0]))
        self.assertEqual(certify(args)["summary"]["duplicate_canonical_facts"], 1)
        args = deepcopy(self.args)
        fact = args[0]["selected"][0]
        args[0]["resolution_ledger"].append({"key": fact["key"], "blocking": True, "reason_code": "TRUE_SCOPE_BLOCKER",
                                              "sources": [fact], "values": [fact["value"]]})
        self.assertEqual(certify(args)["summary"]["blocked_facts_selected"], 1)

    def test_changed_product_period_metric_locator_catalog_or_dataset_rejected(self):
        for index, value in ((1, "forged-product"), (2, "2099-12"), (3, "ORDER")):
            args = deepcopy(self.args)
            args[0]["selected"][0]["key"][index] = value
            self.assertEqual(certify(args)["status"], "BLOCKED")
        for field, value in (("cell", "C999"), ("sheet", "NotPresent")):
            args = deepcopy(self.args)
            args[0]["selected"][0][field] = value
            self.assertEqual(certify(args)["status"], "BLOCKED")
        args = deepcopy(self.args)
        args[0]["fact_scope_catalog"][0]["code"] = "NotApproved"
        self.assertEqual(certify(args)["status"], "BLOCKED")
        args = deepcopy(self.args)
        args[0]["summary"]["dataset_sha256"] = "forged"
        self.assertEqual(certify(args)["status"], "BLOCKED")

    def test_cli_refuses_unpinned_contract_or_non_private_output(self):
        with self.assertRaises(ValueError):
            run(Path("absent"), "invalid", ROOT / "docs")
        with self.assertRaises(ValueError):
            run(Path("absent"), "invalid", ROOT / "outputs/prd09_2d23")


@unittest.skipUnless((PRIVATE / "closure.json").exists(), "private corpus absent; no real data shipped")
class PrivateGlobalCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name):
            return json.loads((PRIVATE / (name + ".json")).read_text(encoding="utf-8"))
        cls.cert = read("global_hierarchical_certification")
        cls.golden = read("global_corpus_golden")
        cls.closure = read("closure")
        cls.determinism = read("determinism")
        cls.legacy = read("legacy_regression_reference")

    def test_CP36_43_complete_real_corpus(self):
        summary = self.cert["summary"]
        for key in ("selected_observations", "certified_observations", "literal_facts_checked", "literal_matches"):
            self.assertEqual(summary[key], 39270, key)
        for key in ("uncertified_selected", "literal_mismatches", "duplicate_canonical_facts", "unsupported_selected_scopes",
                    "unknown_scope_selected", "facts_fanned_out_due_to_membership", "blocked_facts_selected", "fabricated_available_at"):
            self.assertEqual(summary[key], 0, key)
        self.assertEqual(len(self.cert["rows"]), 39270)
        self.assertTrue(all(r["certification_status"] == "PASS" for r in self.cert["rows"]))

    def test_CP39_41_52_all_scopes_products_metrics(self):
        self.assertEqual(len(self.cert["scope_summary"]), 18)
        self.assertTrue(all(s["status"] == "PASS" for s in self.cert["scope_summary"]))
        self.assertEqual(sum(s["total_facts"] for s in self.cert["scope_summary"]), 39270)
        self.assertEqual(self.cert["summary"]["scope_product_identities"], 1010)
        self.assertEqual(self.cert["summary"]["categories"], 50)
        self.assertEqual(sum(self.cert["product_coverage"]["actual_eligibility_counts"].values()), 1010)
        self.assertEqual(self.cert["summary"]["metric_counts"], {"SALES": 13153, "ORDER": 13099, "DELIVERY": 13018})

    def test_CP55_56_all_blockers_traced_excluded(self):
        trace = self.cert["blocked_trace"]
        self.assertEqual(trace["counts"], {"FACT_SCOPE": 1538, "VALUE": 720})
        self.assertEqual(sum(g["blocked_keys"] for g in trace["groups"]), 2258)
        self.assertTrue(all(not d["winner_selected"] for d in trace["decisions"]))
        self.assertFalse({tuple(d["canonical_key"]) for d in trace["decisions"]} & {tuple(r["canonical_key"]) for r in self.cert["rows"]})

    def test_CP59_60_61_golden_and_determinism(self):
        self.assertEqual(compare_global_golden(self.golden, self.cert)["status"], "PASS")
        payload = {k: v for k, v in self.cert.items() if k != "certification_sha256"}
        self.assertEqual(fingerprint(payload), self.cert["certification_sha256"])
        self.assertEqual(self.determinism["status"], "PASS")
        self.assertTrue(all(self.determinism["checks"].values()))

    def test_CP62_63_64_legacy_frozen(self):
        summary = self.legacy["summary"]
        self.assertEqual(summary["value_matches"], 96)
        self.assertEqual(summary["child_scoped"], 72)
        self.assertEqual(summary["parent_scoped"], 24)
        self.assertFalse(summary["legacy_golden_modified"])
        contract = json.loads((ROOT / "outputs/prd09_2d231_contract.json").read_text(encoding="utf-8"))
        verify_pins(contract["files"])
        sources = json.loads(Path(contract["files"]["source_rules"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual({sha256_file(Path(p)) for p in sources["files"]}, set(sources["sources"]))

    def test_CP65_66_67_no_temporal_or_runtime_promotion(self):
        self.assertEqual(self.cert["summary"]["temporal"], {"UNKNOWN": 39270, "E1": 0, "E2": 0, "E3": 0})
        self.assertTrue(all(r["available_at"] is None for r in self.cert["rows"]))
        self.assertEqual(self.closure["supabase_writes"], 0)
        self.assertFalse(self.closure["runtime_modified"])


if __name__ == "__main__":
    unittest.main()
