"""Synthetic public membership contracts; real corpus stays private and optional."""
import csv
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from services.assistant_api.business_membership import (
    BusinessMembershipIndex, UNRESOLVED, certify_scopes, commercial_catalog,
    load_authority, manual_membership_review, membership_counts,
    reconcile_business_history, trace_membership_baseline, trace_value_baseline,
)
from services.assistant_api.historical_corpus import sha256_file
from services.assistant_api.settings import Settings
from services.assistant_api.test_chain_ingestion import HASH, profile, sheet

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "outputs/prd09_2d22"
RULE = "b" * 64


def authority(profiles, aliases=()):
    return {"approved": True, "rule_sha256": RULE, "commercial_sheet_aliases": list(aliases),
            "scopes": [{"source_hash": p.source_hash, "sheet": p.sheet_name,
                        "canonical_commercial_unit": p.canonical_chain_code,
                        "classification": "BUSINESS_COMMERCIAL", "original_sheet_role": p.sheet_role,
                        "evidence_id": "PROOF-" + p.sheet_name, "evidence_locator": "SHEET:" + p.sheet_name,
                        "rule_sha256": RULE, "approved": True} for p in profiles]}


def replay(sheets, profiles, approved=None):
    return reconcile_business_history({HASH: sheets}, [{"sha256": HASH, "source_file": "synthetic.xlsx"}],
                                     profiles, approved or authority([p for p in profiles if p.sheet_role == "CHAIN_DETAIL"]))


def raw():
    return replace(profile("Raw", ""), sheet_role="RAW_BASE", membership_role="NONE")


def old_decision(reason="MISSING_CHAIN_MEMBERSHIP", sources=None):
    return {"resolution_ledger": [{"key": ["OLD", "001234567890", "2024-01", "SALES"],
                                  "blocking": True, "reason_code": reason, "values": ["5", "8"],
                                  "sources": sources or [{"source_sha256": HASH, "sheet": "Blue", "cell": "C2"}]}]}


class BusinessContracts(unittest.TestCase):
    def test_CP01_business_certifies(self):
        report, _, members, _ = replay({"Blue": sheet()}, [profile()])
        self.assertEqual(members[0].evidence_type, "BUSINESS_SHEET_MEMBERSHIP")
        self.assertEqual(report["selected"][0]["key"][0], "Parent::Blue")

    def rejected_role(self, role):
        p = replace(profile(), sheet_role=role, value_role="NONE", canonical_chain_code="")
        with self.assertRaises(ValueError):
            certify_scopes({HASH: {"Blue": sheet()}}, [p], authority([p]))

    def test_CP02_raw_no_self_authority(self):
        self.rejected_role("RAW_BASE")

    def test_CP03_summary_no_self_authority(self):
        self.rejected_role("SUMMARY")

    def test_CP04_forecast_no_actual(self):
        p = replace(profile("Projection"), sheet_role="FORECAST", value_role="NONE")
        report, *_ = replay({"Blue": sheet(), "Projection": sheet(999)}, [profile(), p])
        self.assertEqual(len(report["selected"]), 1)

    def test_CP05_formula_membership(self):
        _, _, members, _ = replay({"Blue": sheet("=1+1")}, [profile()])
        self.assertEqual(members[0].observed_periods, ("2024-01",))

    def test_CP06_formula_not_value(self):
        report, *_ = replay({"Blue": sheet("=1+1")}, [profile()])
        self.assertFalse(report["selected"])
        self.assertEqual(report["summary"]["excluded"]["FORMULA_UNVERIFIED"], 1)

    def paired(self, names):
        a, b = names
        ps = [profile(a, "Unit"), replace(profile(b, "Unit"), sheet_role="ALTERNATE_REPRESENTATION")]
        alias = {"source_hash": HASH, "sheet_a": a, "sheet_b": b, "canonical_unit": "Unit",
                 "approved": True, "evidence": "Owner-approved pair", "rule_sha256": RULE}
        return replay({a: sheet(upc=None), b: sheet()}, ps, authority(ps, [alias]))

    def test_CP07_pair_first(self):
        report, _, members, _ = self.paired(("Left", "Left (2)"))
        self.assertEqual({r["key"][0] for r in report["selected"]}, {"Unit"})
        self.assertEqual(members[0].upc, "001234567890")

    def test_CP08_pair_second(self):
        report, *_ = self.paired(("Right", "Right (2)"))
        self.assertEqual(len(report["selected"]), 1)

    def test_CP09_pair_dedup(self):
        _, _, members, _ = self.paired(("Left", "Left (2)"))
        self.assertEqual(len(members), 1)
        self.assertEqual(len(members[0].sources), 2)

    def test_CP10_master_wins(self):
        p = replace(raw(), chain_column="E", format_column="F")
        report, *_ = replay({"Blue": sheet("=1"), "Raw": sheet(parent="New", fmt="Format")}, [profile(), p])
        self.assertEqual(report["selected"][0]["key"][0], "New::Format")
        self.assertEqual(report["source_assignments"][0]["membership_resolution"], "RESOLVED_EXPLICIT_ROW_CHAIN")

    def test_CP11_unique_routes(self):
        report, *_ = replay({"Blue": sheet("=1"), "Raw": sheet(upc=None)}, [profile(), raw()])
        self.assertEqual(report["selected"][0]["key"][:2], ["Parent::Blue", "001234567890"])

    def multiple(self):
        return replay({"Blue": sheet("=1"), "Red": sheet("=1"), "Raw": sheet(upc=None)},
                      [profile(), profile("Red", "Other"), raw()])[0]

    def test_CP12_multiple_blocks(self):
        self.assertEqual(membership_counts(self.multiple())["membership"], {"MULTIPLE_COMMERCIAL_MEMBERSHIP": 1})

    def test_CP13_missing_blocks(self):
        report, *_ = replay({"Blue": sheet("=1"), "Raw": sheet(item=9999, upc=None)}, [profile(), raw()])
        self.assertEqual(membership_counts(report)["membership"], {"NO_COMMERCIAL_MEMBERSHIP": 1})

    def test_CP14_never_fans_out(self):
        report = self.multiple()
        self.assertFalse(report["selected"])
        self.assertEqual(len(report["source_assignments"]), 1)

    def test_CP15_explicit_upc(self):
        report, _, members, scopes = replay({"Blue": sheet()}, [profile()])
        route = BusinessMembershipIndex(members, scopes).route(HASH, "wrong", "001234567890", period="2024-01")
        self.assertEqual(route["unit"], "Parent::Blue")
        self.assertEqual(report["selected"][0]["key"][1], "001234567890")

    def test_CP16_item_bridge(self):
        self.test_CP11_unique_routes()

    def test_CP17_multi_upc_blocks(self):
        data = sheet("=1")
        data["rows"][3] = dict(data["rows"][2], B="001234567891")
        data["max_row"] = 3
        report, *_ = replay({"Blue": data, "Raw": sheet(upc=None)}, [profile(), raw()])
        self.assertEqual(membership_counts(report)["membership"], {"INSUFFICIENT_IDENTITY": 1})

    def test_CP18_description_not_identity(self):
        data = sheet(item=None, upc=None)
        data["rows"][1]["C"] = "Fecha"
        report, _, members, scopes = replay({"Blue": data}, [profile()])
        self.assertFalse(members)
        self.assertFalse(report["selected"])
        self.assertEqual(scopes[HASH, "Blue"]["product_blocks"], 1)

    def test_CP19_period_aware(self):
        _, _, members, scopes = replay({"Blue": sheet("=1")}, [profile()])
        index = BusinessMembershipIndex(members, scopes)
        self.assertEqual(index.route(HASH, "1001", "", period="2024-02")["membership_resolution"], "NO_COMMERCIAL_MEMBERSHIP")
        self.assertEqual(index.route("c"*64, "1001", "", period="2024-01")["unit"], "")

    def test_CP20_absence_not_nonmembership(self):
        _, _, members, scopes = replay({"Blue": sheet("=1")}, [profile()])
        route = BusinessMembershipIndex(members, scopes).route(HASH, "1001", "", period="2024-02")
        self.assertTrue(route["membership_absence_is_not_nonmembership"])
        self.assertEqual(route["outside_observed_units"], ["Parent::Blue"])

    def test_CP21_trace_all_and_missing(self):
        report, *_ = replay({"Blue": sheet()}, [profile()])
        self.assertEqual(trace_membership_baseline(old_decision(), report)[0]["final_status"], "RESOLVED_BUSINESS_SHEET_MEMBERSHIP")
        report["source_assignments"] = []
        self.assertTrue(trace_membership_baseline(old_decision(), report)[0]["still_membership_blocking"])

    def test_CP22_chain_separation(self):
        report, *_ = replay({"Blue": sheet(), "Red": sheet(8)}, [profile(), profile("Red", "Other")])
        sources = [{"source_sha256": HASH, "sheet": name, "cell": "C2"} for name in ("Blue", "Red")]
        trace = trace_value_baseline(old_decision("INTERNAL_SOURCE_DUPLICATE", sources), report)
        self.assertEqual(trace[0]["final_status"], "RESOLVED_BY_CHAIN_SEPARATION")
        report["selected"] = []
        self.assertTrue(trace_value_baseline(old_decision("INTERNAL_SOURCE_DUPLICATE", sources), report)[0]["still_blocking"])

    def test_CP23_value_contradiction_stays(self):
        report, *_ = replay({"Blue": sheet(), "Red": sheet(8)}, [profile(), profile("Red")])
        sources = [{"source_sha256": HASH, "sheet": name, "cell": "C2"} for name in ("Blue", "Red")]
        trace = trace_value_baseline(old_decision("INTERNAL_SOURCE_DUPLICATE", sources), report)
        self.assertTrue(trace[0]["still_blocking"])
        self.assertEqual(len(trace[0]["original_values"]), 2)

    def test_CP24_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            rule, config = Path(directory)/"rule.txt", Path(directory)/"authority.json"
            rule.write_text("Owner reviewed rule", encoding="utf-8")
            value = {**authority([profile()]), "rule_sha256": sha256_file(rule), "approved_by": "owner",
                     "review_note": "Reviewed", "evidence_type": "OWNER_BUSINESS_MEMBERSHIP_RULE"}
            config.write_text(json.dumps(value), encoding="utf-8")
            args = {"expected_sha": sha256_file(config), "rule_path": rule, "rule_sha": sha256_file(rule)}
            self.assertEqual(load_authority(config, **args), value)
            with self.assertRaises(ValueError):
                load_authority(config, **dict(args, expected_sha="0"*64))
            value["approved"] = False
            config.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_authority(config, **dict(args, expected_sha=sha256_file(config)))

    def test_CP25_unapproved(self):
        value = authority([profile()])
        value["scopes"][0]["approved"] = False
        with self.assertRaises(ValueError):
            replay({"Blue": sheet()}, [profile()], value)

    def test_CP26_hash_scope_mismatch(self):
        value = authority([profile()])
        value["scopes"][0]["source_hash"] = "c"*64
        with self.assertRaises(ValueError):
            replay({"Blue": sheet()}, [profile()], value)

    def test_CP27_no_all_sheets(self):
        value = authority([profile()])
        value["scopes"][0]["sheet"] = "*"
        with self.assertRaises(ValueError):
            replay({"Blue": sheet()}, [profile()], value)
        with self.assertRaises(ValueError):
            replay({"Blue": sheet()}, [profile()], {**value, "scopes": []})

    def test_CP28_dynamic_optional_parent(self):
        ps = [profile("NeverSeen", "IndependentCode")]
        report, adapted, members, scopes = replay({"NeverSeen": sheet()}, ps)
        catalog = commercial_catalog(adapted, scopes, members)
        self.assertEqual(catalog[0]["parent_code"], "")
        self.assertEqual(report["selected"][0]["key"][0], "IndependentCode")

    def test_CP29_no_retailer_branch(self):
        source = (ROOT/"services/assistant_api/business_membership.py").read_text().casefold()
        self.assertNotIn("walmart", source)

    def test_CP30_no_pilot_branch(self):
        for filename in ("services/assistant_api/business_membership.py", "scripts/reconstruct_business_membership.py"):
            source = (ROOT/filename).read_text().casefold()
            for forbidden in ("fendi", "750189", "101285"):
                self.assertNotIn(forbidden, source)

    def test_CP33_order_independent(self):
        sheets = {"Blue": sheet(), "Red": sheet(8)}
        ps = [profile(), profile("Red", "Other")]
        a, *_ = replay(sheets, ps)
        b, *_ = replay(dict(reversed(list(sheets.items()))), ps[::-1])
        self.assertEqual(a, b)

    def test_CP34_unknown_null(self):
        report, *_ = replay({"Blue": sheet()}, [profile()])
        self.assertTrue(all(v["availability_source"] == "UNKNOWN" and v["available_at"] is None for v in report["versions"]))

    def test_CP35_monthly_null_not_zero(self):
        report, *_ = replay({"Blue": sheet(None)}, [profile()])
        self.assertFalse(report["selected"])
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual((settings.persistence_provider, settings.data_provider), ("sqlite", "normalized"))
        self.assertFalse(settings.supabase_enabled)

    def test_CP36_no_runtime_import_or_schema(self):
        self.assertNotIn("business_membership", (ROOT/"services/assistant_api/api.py").read_text())
        migrations = list((ROOT/"supabase/migrations").glob("*.sql"))
        self.assertEqual(len(migrations), 7)

    def test_grouped_review_and_scope_guards(self):
        review, count = manual_membership_review(self.multiple())
        self.assertEqual(count, 1)
        self.assertIn("dimensión explícita", review)
        for change in ({"approved": False}, {"rule_sha256": ""}):
            with self.assertRaises(ValueError):
                replay({"Blue": sheet()}, [profile()], {**authority([profile()]), **change})
        with self.assertRaises(ValueError):
            replay({"Blue": sheet(), "Missing": sheet()}, [profile()])


@unittest.skipUnless((PRIVATE/"reconciliation.json").exists(), "Private corpus deliberately excluded from Git")
class PrivateBusinessCertification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read = staticmethod(lambda name: json.loads((PRIVATE/(name+".json")).read_text(encoding="utf-8")))
        cls.report = cls.read("reconciliation")

    def test_CP21_exact_11850(self):
        rows = self.read("membership_resolution")
        self.assertEqual(len(rows), 11850)
        self.assertEqual(len({r["old_decision_id"] for r in rows}), 11850)
        self.assertEqual(Counter(r["old_status"] for r in rows), {"AMBIGUOUS_CHAIN_MEMBERSHIP": 7628, "MISSING_CHAIN_MEMBERSHIP": 4222})

    def test_CP22_23_all_500_preserved(self):
        rows = self.read("value_conflicts_rekeyed")
        self.assertEqual(len(rows), 500)
        self.assertEqual(Counter(r["old_status"] for r in rows), {"INTERNAL_SOURCE_DUPLICATE": 408, "CROSS_SOURCE_CONFLICT": 91, "SAME_SNAPSHOT_CONFLICT": 1})
        self.assertTrue(all(r["original_values"] and r["source_locators"] and r["no_value_authority_added"] for r in rows))
        old = json.loads((ROOT/"outputs/prd09_2d21/reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(trace_value_baseline(old, self.report))), rows)

    def test_CP31_golden_96(self):
        selected = {(r["key"][1], r["key"][2], r["key"][3]): Decimal(r["value"]) for r in self.report["selected"]
                    if r["key"][0] == "Walmart::BD" and r["key"][1].startswith("75018978131") and r["key"][2].startswith("2024")}
        expected = {}
        with (ROOT.parent/"outputs/prd01_fendi_bd/data/fendi_bd_facts.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                metric = {"Venta": "SALES", "Pedido": "ORDER", "Entrega": "DELIVERY"}.get(row.get("metric"))
                if metric and row.get("period", "").startswith("2024") and row.get("upc", "").startswith("75018978131") and row.get("value") not in {None, "", "None"}:
                    expected[row["upc"], row["period"], metric] = Decimal(row["value"])
        self.assertEqual(len(selected), 96)
        self.assertTrue(all(expected[k] == v for k, v in selected.items()))

    def test_CP32_all_unit_literals(self):
        certificate = self.read("literal_certificate")
        self.assertEqual(certificate["status"], "PASS")
        self.assertEqual(certificate["all_literals_checked"], len(self.report["selected"]))
        self.assertEqual(certificate["units_sampled"], len({r["key"][0] for r in self.report["selected"]}))

    def test_CP33_determinism_and_digest(self):
        self.assertEqual(self.read("determinism")["status"], "PASS")
        digest = hashlib.sha256(json.dumps(self.report["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, self.report["summary"]["dataset_sha256"])

    def test_CP34_every_historical_date_unknown(self):
        self.assertTrue(all(v["availability_source"] == "UNKNOWN" and v["available_at"] is None for v in self.report["versions"]))
        self.assertFalse(self.report["summary"]["fabricated_available_at"])
        self.assertTrue(all(r["final_status"] in UNRESOLVED or r["evidence"] for r in self.read("membership_resolution")))


if __name__ == "__main__":
    unittest.main()
