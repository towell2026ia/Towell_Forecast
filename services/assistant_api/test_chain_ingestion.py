"""Public synthetic contracts plus optional, local-only private corpus gates."""
import csv
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula

from scripts.reconstruct_chain_history import build_profiles, inventory, resumen_mapping, write_new
from services.assistant_api.chain_ingestion import (
    Membership, MembershipIndex, SourceSheetProfile, extract_memberships, identifier,
    is_formula, load_profiles, read_sources, reconcile_chain_aware, trace_old_conflicts, unit_key,
)
from services.assistant_api.historical_corpus import SourceSpec, sha256_file
from services.assistant_api.source_adjudication import cluster_conflicts, fingerprint
from services.assistant_api.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "outputs/prd09_2d21"
HASH = "a" * 64


def sheet(value=5, *, item=1001, upc="001234567890", parent=None, fmt=None):
    row = {"A": item, "B": upc, "C": value, "D": "Description is not an identity"}
    if parent is not None:
        row.update(E=parent, F=fmt)
    return {"rows": {1: {"A": "ITEM", "B": "UPC", "C": "2024/01 POS Qty"}, 2: row},
            "formulas": {"C2"} if is_formula(value) else set(), "range": "A1:F2", "max_row": 2, "max_column": 6}


def profile(name="Blue", chain="Parent::Blue", **kwargs):
    return SourceSheetProfile(HASH, name, "CHAIN_DETAIL", chain, chain, "Parent", "Blue", "A", "B", "D",
                              period_layout={"cutoff": "2024-01"},
                              metric_layout=({"column": "C", "period": "2024-01", "metric": "SALES"},),
                              identity_role="EXPLICIT_IDENTIFIERS", membership_role="CHAIN_MEMBERSHIP", value_role="ACTUAL",
                              header_rows=(1,), evidence=({"source_hash": HASH, "locator": "SHEET:" + name, "basis": "Reviewed synthetic source"},),
                              review_required=False, **kwargs)


def reconcile(sheets, profiles):
    return reconcile_chain_aware({HASH: sheets}, [{"sha256": HASH, "source_file": "synthetic.xlsx"}], profiles)


class ChainContracts(unittest.TestCase):
    def test_CP09_every_sheet_requires_profile(self):
        with self.assertRaisesRegex(ValueError, "every_sheet"):
            reconcile({"Blue": sheet(), "New": sheet()}, [profile()])

    def test_CP10_unknown_is_recorded(self):
        p = build_profiles({HASH: {"Unknown (2)": sheet()}}, {"sources": {HASH: {}}})[0]
        self.assertEqual((p.sheet_role, p.review_required, p.canonical_chain_code), ("UNKNOWN", True, ""))
        report = reconcile({p.sheet_name: sheet()}, [p])
        self.assertFalse(report["selected"])
        self.assertEqual(len(inventory({HASH: {p.sheet_name: sheet()}}, [p], [{"sha256": HASH, "source_file": "x", "duplicate_relationship": None}], report)), 1)

    def test_CP11_explicit_pair_same_unit(self):
        ps = [profile("Blue"), replace(profile("Blue (2)"), sheet_role="ALTERNATE_REPRESENTATION")]
        self.assertEqual(ps[0].canonical_chain_code, ps[1].canonical_chain_code)

    def test_CP12_no_suffix_heuristic(self):
        config = {"sources": {HASH: {"sheets": {"Red": {"role": "CHAIN_DETAIL", "unit": "Red"},
                                                "Red (2)": {"role": "CHAIN_DETAIL", "unit": "Other"}}}}}
        ps = build_profiles({HASH: {"Red": sheet(), "Red (2)": sheet()}}, config)
        self.assertEqual({p.canonical_chain_code for p in ps}, {"Red", "Other"})

    def test_CP13_pair_dedup_preserves_lineage(self):
        report = reconcile({"Blue": sheet(), "Blue (2)": sheet()}, [profile(), profile("Blue (2)")])
        self.assertEqual(len(report["selected"]), 1)
        self.assertEqual(len(report["selected"][0]["equivalent_sources"]), 2)
        conflict = reconcile({"Blue": sheet(), "Blue (2)": sheet(8)}, [profile(), profile("Blue (2)")])
        self.assertEqual(conflict["summary"]["blocking_keys"], 1)

    def test_CP14_item_membership(self):
        members = extract_memberships({HASH: {"Blue": sheet()}}, [profile()])
        self.assertEqual((members[0].item, members[0].item_locator), ("1001", "A2"))

    def test_CP15_upc_literal_leading_zero(self):
        members = extract_memberships({HASH: {"Blue": sheet()}}, [profile()])
        self.assertEqual(members[0].upc, "001234567890")
        self.assertEqual(identifier("=A2", upc=True), "")
        self.assertEqual(identifier("Description"), "")
        self.assertEqual(identifier(0), "")
        self.assertEqual(identifier("123", upc=True), "")

    def test_CP16_formula_catalog_not_demand(self):
        for formula in ("=XLOOKUP(A2,Base!A:A,Base!C:C)", ArrayFormula(ref="C2", text="=SUM(A1:A2)")):
            report = reconcile({"Blue": sheet(formula)}, [profile()])
            self.assertEqual(len(report["chain_membership_ledger"]), 1)
            self.assertFalse(report["selected"])
            self.assertEqual(report["summary"]["excluded"]["FORMULA_UNVERIFIED"], 1)

    def test_CP17_raw_not_implicit_chain(self):
        with self.assertRaisesRegex(ValueError, "raw_base"):
            replace(profile(), sheet_role="RAW_BASE").validate(HASH, {"Blue"})
        p = replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")
        report = reconcile({"Raw": sheet()}, [p])
        self.assertFalse(report["selected"])
        self.assertEqual(report["resolution_ledger"][0]["reason_code"], "MISSING_CHAIN_MEMBERSHIP")

    def test_CP18_summary_never_fact(self):
        with self.assertRaisesRegex(ValueError, "non_fact"):
            replace(profile(), sheet_role="SUMMARY").validate(HASH, {"Blue"})

    def test_CP19_forecast_not_actual(self):
        p = replace(profile(), sheet_role="FORECAST", value_role="NONE")
        self.assertFalse(reconcile({"Blue": sheet()}, [p])["selected"])

    def test_CP20_explicit_dimensions_win(self):
        p = profile(chain_column="E", format_column="F")
        report = reconcile({"Blue": sheet(parent="NewParent", fmt="NewFormat")}, [p])
        self.assertEqual(report["selected"][0]["key"][0], "NewParent::NewFormat")

    def test_CP21_unique_membership_maps_raw(self):
        p = replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")
        report = reconcile({"Blue": sheet("=C3"), "Raw": sheet(upc=None)}, [profile(), p])
        self.assertEqual(report["selected"][0]["key"][:2], ["Parent::Blue", "001234567890"])

    def test_CP22_ambiguous_never_fans_out(self):
        p = replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")
        report = reconcile({"Blue": sheet("=C3"), "Red": sheet("=C3"), "Raw": sheet(upc=None)},
                           [profile(), profile("Red", "Parent::Red"), p])
        self.assertFalse(report["selected"])
        blockers = [d for d in report["resolution_ledger"] if d["blocking"]]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["reason_code"], "AMBIGUOUS_CHAIN_MEMBERSHIP")
        self.assertEqual(sum(c["count"] for c in cluster_conflicts(report)), 1)

    def test_CP23_cross_chain_same_upc_not_merged(self):
        report = reconcile({"Blue": sheet(), "Red": sheet(8)}, [profile(), profile("Red", "Parent::Red")])
        self.assertEqual(len(report["selected"]), 2)
        self.assertFalse(report["blocking"])

    def test_CP24_resumen_literal_code_optional(self):
        data = {"rows": {1: {"A": "GRAL", "B": "CADENA", "C": "Modelo", "D": "CODIGO NEW"},
                         2: {"A": "Z", "B": "NewChain", "C": "No atomic ID", "D": "P12"},
                         3: {"A": "Z", "B": "NewChain", "C": "No code"},
                         4: {"A": "=A2", "B": "Other", "D": "P13"}}}
        mapping = resumen_mapping({HASH: {"Overview": data}})
        self.assertEqual(len(mapping), 2)
        self.assertIsNone(mapping[1]["commercial_code"])
        self.assertTrue(all(m["not_operational_item_or_upc"] for m in mapping))

    def test_CP26_resumen_not_exhaustive(self):
        ps = build_profiles({HASH: {"Extra": sheet()}}, {"sources": {HASH: {"sheets": {"Extra": {"unit": "Extra", "role": "CHAIN_DETAIL"}}}}})
        self.assertEqual(ps[0].canonical_chain_code, "Extra")

    def test_CP27_dynamic_creation(self):
        report = reconcile({"Blue": sheet(parent="NeverPreviouslySeen", fmt="Special")}, [profile(chain_column="E", format_column="F")])
        self.assertIn("NeverPreviouslySeen::Special", report["summary"]["coverage"])

    def test_CP28_29_30_no_pilot_branches(self):
        for path in (ROOT / "services/assistant_api/chain_ingestion.py", ROOT / "scripts/reconstruct_chain_history.py"):
            source = path.read_text(encoding="utf-8").casefold()
            for forbidden in ("fendi", "walmart", "750189", "101285"):
                self.assertNotIn(forbidden, source)

    def test_CP32_blocking_count_exact(self):
        data = sheet()
        data["rows"][3] = dict(data["rows"][2], C=8)
        data["max_row"] = 3
        p = replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")
        report = reconcile({"Raw": data}, [p])
        self.assertEqual(report["summary"]["blocking_keys"], 1)
        self.assertEqual(len(report["resolution_ledger"][0]["sources"]), 2)
        self.assertEqual(sum(c["count"] for c in cluster_conflicts(report)), 1)

    def test_CP33_profile_order_independent(self):
        sheets = {"Blue": sheet(), "Blue (2)": sheet()}
        ps = [profile(), profile("Blue (2)")]
        a, b = reconcile(sheets, ps), reconcile(sheets, ps[::-1])
        for field in ("summary", "selected", "versions", "resolution_ledger", "chain_membership_ledger", "source_assignments"):
            self.assertEqual(a[field], b[field])

    def test_CP34_byte_copy_dedup_and_formula_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            book = Workbook()
            book.active.append(["ITEM", "UPC", "2024/01 POS Qty"])
            book.active.append([1001, "001234567890", "=1+1"])
            book.save(path)
            digest = sha256_file(path)
            books, manifests = read_sources([SourceSpec(path), SourceSpec(path)])
            self.assertEqual(len(books), 1)
            self.assertEqual(manifests[1]["duplicate_relationship"], digest)
            self.assertEqual(books[digest]["Sheet"]["formulas"], {"C2"})
            self.assertEqual(sha256_file(path), digest)

    def test_CP35_missing_not_zero(self):
        report = reconcile({"Blue": sheet(None)}, [profile()])
        self.assertFalse(report["selected"])
        self.assertEqual(report["summary"]["excluded"]["MISSING"], 1)

    def test_CP36_prelaunch_no_invented_zeros(self):
        data = sheet(0)
        data["rows"][3] = dict(data["rows"][2], C=8)
        data["max_row"] = 3
        p = replace(profile(), metric_layout=({"column": "C", "period": "2024-01", "metric": "SALES", "row_to": 2},
                                              {"column": "C", "period": "2024-02", "metric": "SALES", "row_from": 3}), period_layout={"cutoff": "2024-02"})
        report = reconcile({"Blue": data}, [p])
        self.assertEqual(len(report["selected"]), 1)
        self.assertEqual(report["summary"]["excluded"]["PRE_LAUNCH_ZERO"], 1)

    def test_CP37_non_demand_bands_excluded(self):
        data = {"rows": {1: {"C": "Venta Mensual", "E": "Existencia Mensual", "G": "Fill Rate Mensual"},
                         2: {"A": "ITEM", "B": "UPC", "C": "Ene", "D": "Feb", "E": "Ene", "F": "Feb", "G": "Ene"}},
                "max_row": 2}
        p = build_profiles({HASH: {"Blue": data}}, {"sources": {HASH: {"year": 2024, "sheets": {"Blue": {"role": "CHAIN_DETAIL", "unit": "X", "monthly_bands": True}}}}})[0]
        self.assertEqual([f["column"] for f in p.metric_layout], ["C", "D"])

    def test_CP38_unknown_availability_null(self):
        report = reconcile({"Blue": sheet()}, [profile()])
        self.assertTrue(all(r["availability_source"] == "UNKNOWN" and r["available_at"] is None for r in report["versions"]))
        self.assertEqual(report["summary"]["fabricated_available_at"], 0)

    def test_CP43_runtime_flags_unchanged(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(any((settings.supabase_enabled, settings.openai_enabled, settings.voice_enabled, settings.deep_research_enabled)))
        self.assertEqual((settings.persistence_provider, settings.data_provider), ("sqlite", "normalized"))

    def test_profile_rejects_forgery_and_invalid_roles(self):
        invalid = [replace(profile(), source_hash="b"*64), replace(profile(), evidence=()),
                   replace(profile(), evidence=({"source_hash": "b"*64, "locator": "x", "basis": "x"},)),
                   replace(profile(), membership_role="GUESS"), replace(profile(), metric_layout=({"metric": "INVENTORY", "column": "C"},))]
        for p in invalid:
            with self.assertRaises(ValueError):
                p.validate(HASH, {"Blue"})

    def test_private_profile_roundtrip_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            write_new(path, [asdict(profile())])
            self.assertEqual(load_profiles(path), [profile()])
            with self.assertRaises(FileExistsError):
                write_new(path, [])
            other = Path(directory) / "duplicate.json"
            write_new(other, [asdict(profile()), asdict(profile())])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_profiles(other)

    def test_membership_source_scoped_and_explicit_upc(self):
        m = Membership(HASH, "Blue", 2, "X", "X", "", "1001", "001234567890", "CHAIN_MEMBERSHIP", "A2", "B2")
        index = MembershipIndex([m])
        self.assertEqual(index.resolve("b"*64, "1001", "")[2], "MISSING_CHAIN_MEMBERSHIP")
        self.assertEqual(index.resolve(HASH, "", m.upc)[0], "X")
        self.assertEqual(index.resolve(HASH, "1001", m.upc, explicit_chain="Y")[0], "Y")
        self.assertEqual(unit_key("X", "X"), "X")
        self.assertEqual(unit_key("", "Blue"), "")

    def test_unmapped_segment_cannot_make_raw_membership_unique(self):
        data = sheet("=C3")
        segment = replace(profile("Segment", ""), membership_role="SEGMENT_MEMBERSHIP")
        raw = replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")
        report = reconcile({"Blue": data, "Segment": data, "Raw": sheet(upc=None)}, [profile(), segment, raw])
        self.assertFalse(report["selected"])
        self.assertEqual(report["summary"]["reason_counts"]["AMBIGUOUS_CHAIN_MEMBERSHIP"], 1)

    def test_formula_chain_or_format_never_sheet_fallback(self):
        for parent, fmt in (("=E3", "Special"), ("New", "=F3"), (None, "Special")):
            p = profile(chain_column="E", format_column="F")
            report = reconcile({"Blue": sheet(parent=parent, fmt=fmt)}, [p])
            self.assertFalse(report["selected"])

    def test_explicit_item_multi_upc_catalog_blocks_without_bridge(self):
        m = Membership(HASH, "Blue", 2, "X", "X", "", "1001", "001234567890", "CHAIN_MEMBERSHIP", "A2", "B2")
        index = MembershipIndex([m, replace(m, row=3, upc="001234567891")])
        self.assertEqual(index.resolve(HASH, "1001", "", explicit_chain="X")[2], "AMBIGUOUS_CHAIN_MEMBERSHIP")

    def test_explicit_master_date_layout_and_cutoff(self):
        data = {"rows": {1: {"A": "ITEM", "B": "UPC", "C": "Cadena", "D": "Formato", "E": "Fecha", "F": "Venta", "G": "Pedido", "H": "Entrega"},
                         2: {"A": 1001, "B": "001234567890", "C": "New", "D": "Special", "E": datetime(2026, 7, 1), "F": 4, "G": 6, "H": 8},
                         3: {"A": 1001, "B": "001234567890", "C": "New", "D": "Special", "E": datetime(2026, 8, 1), "F": 7}},
                "formulas": set(), "max_row": 3}
        config = {"sources": {HASH: {"cutoff": "2026-07", "sheets": {"Master": {"role": "RAW_BASE", "values": True}}}}}
        ps = build_profiles({HASH: {"Master": data}}, config)
        report = reconcile({"Master": data}, ps)
        self.assertEqual(len(report["selected"]), 3)
        self.assertEqual({r["key"][0] for r in report["selected"]}, {"New::Special"})

    def test_dated_blocks_preserve_actual_year(self):
        data = {"rows": {1: {"C": datetime(2015, 1, 1)}, 2: {"A": "ITEM", "B": "UPC"}, 3: {"A": 1001, "B": "001234567890", "C": 9}}, "max_row": 3, "formulas": set()}
        config = {"sources": {HASH: {"year": 2023, "sheets": {"Old": {"role": "CHAIN_DETAIL", "unit": "X", "dated_sales": True, "values": True}}}}}
        ps = build_profiles({HASH: {"Old": data}}, config)
        self.assertEqual(ps[0].metric_layout[0]["period"], "2015-01")
        self.assertFalse(reconcile({"Old": data}, ps)["selected"])

    def test_trace_resolutions_and_missing_never_silently_resolved(self):
        old_decision = {"key": ["Old", "001234567890", "2024-01", "SALES"], "blocking": True,
                        "sources": [{"source_sha256": HASH, "sheet": "Blue", "cell": "C2", "evidence_level": "A"}]}
        old = {"resolution_ledger": [old_decision], "summary": {"blocking_keys": 1}}
        clusters = [{"cluster_id": "C-test", "keys": [old_decision["key"]]}]
        report = reconcile({"Blue": sheet()}, [profile()])
        self.assertEqual(trace_old_conflicts(old, report, clusters)[0]["new_resolution"], "RESOLVED_DUPLICATE_REPRESENTATION")
        report["resolution_ledger"] = []
        self.assertEqual(trace_old_conflicts(old, report, clusters)[0]["new_resolution"], "AMBIGUOUS_CHAIN_MEMBERSHIP")
        old["summary"]["blocking_keys"] = 2
        with self.assertRaisesRegex(ValueError, "accounting"):
            trace_old_conflicts(old, report, clusters)


@unittest.skipUnless((PRIVATE / "reconciliation.json").exists(), "Private source corpus is deliberately not published")
class PrivateCorpusCertification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name):
            return json.loads((PRIVATE / (name + ".json")).read_text(encoding="utf-8"))
        cls.config, cls.report, cls.inventory = read("source_rules"), read("reconciliation"), read("sheet_inventory")
        cls.profiles, cls.trace, cls.clusters = read("sheet_profiles"), read("old_to_new_conflict_map"), read("conflict_clusters")

    def test_CP01_08_hashes_sheet_counts(self):
        counts = [26, 10, 10, 7, 13, 13]
        for path, count in zip(self.config["files"], counts):
            digest = sha256_file(Path(path))
            self.assertIn(digest, self.config["sources"])
            self.assertEqual(sum(r["filename"] == Path(path).name for r in self.inventory), count)
        self.assertNotEqual(sha256_file(Path(self.config["files"][1])), sha256_file(Path(self.config["files"][2])))

    def test_CP11_12_25_private_pairs_and_82_lines(self):
        for digest in list(self.config["sources"])[1:3]:
            ps = {p["sheet_name"]: p for p in self.profiles if p["source_hash"] == digest}
            for name in ("BD", "SC"):
                self.assertEqual(ps[name]["canonical_chain_code"], ps[name+" (2)"]["canonical_chain_code"])
        mapping = json.loads((PRIVATE / "resumen_mapping.json").read_text(encoding="utf-8"))
        self.assertEqual(len(mapping), 82)

    def test_CP31_32_33_594_accounting(self):
        self.assertEqual(len(self.trace), 594)
        self.assertEqual(len({r["old_decision_id"] for r in self.trace}), 594)
        self.assertEqual(sum(r["count"] for r in self.clusters), self.report["summary"]["blocking_keys"])
        self.assertEqual(json.loads((PRIVATE / "determinism.json").read_text())["status"], "PASS")
        digest = hashlib.sha256(json.dumps(self.report["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, self.report["summary"]["dataset_sha256"])

    def test_CP39_golden_96_no_special_ingestion(self):
        selected = {(r["key"][1], r["key"][2], r["key"][3]): Decimal(r["value"]) for r in self.report["selected"]
                    if r["key"][0] == "Walmart::BD" and r["key"][1].startswith("75018978131") and r["key"][2].startswith("2024")}
        expected = {}
        with (ROOT.parent / "outputs/prd01_fendi_bd/data/fendi_bd_facts.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                metric = {"Venta": "SALES", "Pedido": "ORDER", "Entrega": "DELIVERY"}.get(row.get("metric"))
                if metric and row.get("period", "").startswith("2024") and row.get("upc", "").startswith("75018978131") and row.get("value") not in {None, "", "None"}:
                    expected[(row["upc"], row["period"], metric)] = Decimal(row["value"])
        self.assertEqual(len(selected), 96)
        self.assertTrue(all(expected[k] == v for k, v in selected.items()))

    def test_CP40_multichain_source_regression(self):
        regression = self.report["summary"]["source_regression"]
        self.assertEqual(regression["status"], "PASS")
        self.assertGreater(regression["chains_sampled"], 13)
        self.assertTrue(all(r["available_at"] is None and r["availability_source"] == "UNKNOWN" for r in self.report["versions"]))


if __name__ == "__main__":
    unittest.main()
