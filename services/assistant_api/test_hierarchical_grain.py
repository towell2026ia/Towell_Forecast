"""Public synthetic grain contracts; optional local-only real-source certification."""
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.assistant_api.business_membership import certify_scopes
from services.assistant_api.hierarchical_grain import (
    GrainResolver, SourceScope, aggregate_scope, coverage, load_scope_authority,
    lookup_reference, reconcile_grain, scope_id, trace_baseline, value_conflicts,
)
from services.assistant_api.historical_corpus import sha256_file
from services.assistant_api.hierarchical_certification import certify_hierarchy, legacy_cohort, read_value_golden
from services.assistant_api.settings import Settings
from services.assistant_api.source_adjudication import fingerprint
from services.assistant_api.test_business_membership import authority, old_decision
from services.assistant_api.test_chain_ingestion import HASH, profile, sheet

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT/"outputs/prd09_2d23"
RULE = "c"*64


def raw(name="Raw"):
    return replace(profile(name, ""), sheet_role="RAW_BASE", membership_role="NONE")


def replay(sheets, profiles, policies=None):
    membership = authority([p for p in profiles if p.sheet_role in {"CHAIN_DETAIL", "CHAIN_CALC"}])
    books = {HASH: sheets}
    adapted, _ = certify_scopes(books, profiles, membership, allow_empty=True)
    records = []
    for p in adapted:
        if p.value_role != "ACTUAL":
            continue
        kind = "EXPLICIT_ROW_UNIT" if p.chain_column else "PARENT_CHAIN" if p.sheet_role == "RAW_BASE" else "COMMERCIAL_UNIT"
        policy = (policies or {}).get(p.sheet_name, {})
        kind = policy.get("fact_grain", kind)
        record = SourceScope(p.source_hash, p.sheet_name, policy.get("parent_chain", "Holding" if kind == "PARENT_CHAIN" else p.parent_chain),
                             kind, p.canonical_chain_code if kind == "COMMERCIAL_UNIT" else "",
                             {"chain":p.chain_column,"format":p.format_column},
                             {"source_hash":HASH,"rule_sha256":RULE,"locator":"SHEET:"+p.sheet_name,"basis":"Synthetic independent scope",
                              "value_scope_certified":kind == "COMMERCIAL_UNIT"}, True, fingerprint(asdict(p)), RULE)
        records.append(asdict(record))
    config = {"approved":True,"rule_sha256":RULE,"source_scopes":records}
    report, resolver = reconcile_grain(books, [{"sha256":HASH,"source_file":"synthetic.xlsx"}], profiles, membership, config)
    return report, resolver, config, adapted


def parent_fixture(children=True):
    sheets, profiles = {"Raw":sheet(upc=None)}, [raw()]
    if children:
        sheets["Blue"] = sheet("=1+1")
        profiles.append(profile())
    return replay(sheets, profiles)


class HierarchicalContracts(unittest.TestCase):
    def test_CP01_parent_accepted(self):
        report, *_ = parent_fixture()
        self.assertEqual(report["selected"][0]["fact_grain"], "PARENT_CHAIN")
        self.assertEqual(report["selected"][0]["key"][1], "1001")

    def test_CP02_membership_not_ownership(self):
        report, *_ = parent_fixture()
        self.assertEqual(report["selected"][0]["canonical_code"], "Holding")
        self.assertEqual(report["selected"][0]["children"], ["Parent::Blue"])

    def multiple(self):
        return replay({"Raw":sheet(upc=None),"Blue":sheet("=1+1"),"Red":sheet("=1+1")},
                      [raw(),profile(),profile("Red","Other")])

    def test_CP03_many_memberships(self):
        report, *_ = self.multiple()
        self.assertEqual(len(report["commercial_memberships"]), 2)
        self.assertEqual(len(report["selected"][0]["children"]), 2)

    def test_CP04_no_fanout(self):
        report, *_ = self.multiple()
        self.assertEqual(len(report["selected"]), 1)
        self.assertFalse(report["blocking"])

    def test_CP05_parent_without_children(self):
        report, *_ = parent_fixture(False)
        self.assertTrue(report["selected"][0]["parent_only"])
        self.assertFalse(report["blocking"])

    def test_CP06_unknown_scope_blocks(self):
        report, *_ = replay({"Raw":sheet(upc=None)}, [raw()], {"Raw":{"fact_grain":"UNKNOWN_SCOPE","parent_chain":""}})
        self.assertFalse(report["selected"])
        self.assertEqual(report["resolution_ledger"][0]["reason_code"], "TRUE_SCOPE_BLOCKER")

    def test_CP07_explicit_row_wins(self):
        p = replace(raw(), chain_column="E", format_column="F")
        report, *_ = replay({"Raw":sheet(parent="New",fmt="Format")}, [p], {"Raw":{"parent_chain":"Other"}})
        self.assertEqual(report["selected"][0]["canonical_code"], "New::Format")
        self.assertEqual(report["selected"][0]["fact_grain"], "EXPLICIT_ROW_UNIT")
        invalid, *_ = replay({"Raw":sheet(parent="=E3",fmt="Format")}, [p], {"Raw":{"parent_chain":"Other"}})
        self.assertFalse(invalid["selected"])

    def test_CP08_formula_membership(self):
        report, *_ = parent_fixture()
        self.assertEqual(report["commercial_memberships"][0]["evidence_type"], "BUSINESS_SHEET_MEMBERSHIP")

    def test_CP09_formula_not_fact(self):
        report, *_ = parent_fixture()
        self.assertEqual(len(report["selected"]), 1)
        self.assertEqual(report["summary"]["excluded"]["FORMULA_UNVERIFIED"], 1)

    def test_membership_alone_does_not_certify_literal_grain(self):
        p=replace(profile(),sheet_role="CHAIN_CALC")
        report,*_=replay({"Blue":sheet()},[p],{"Blue":{"fact_grain":"UNKNOWN_SCOPE","parent_chain":""}})
        self.assertEqual(len(report["commercial_memberships"]),1)
        self.assertFalse(report["selected"])
        self.assertEqual(report["fact_scope_catalog"][1]["scope_type"],"UNKNOWN_SCOPE")

    def test_CP10_lookup_excluded_with_lineage(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet('=IFERROR(_xlfn.XLOOKUP($A:$A,Raw!$A:$A,Raw!$C:$C),0)')}, [raw(),profile()])
        self.assertEqual(len(report["derived_lineage"]), 1)
        self.assertEqual(len(report["selected"]), 1)
        self.assertEqual(report["derived_lineage"][0]["canonical_raw_keys"][0][0], scope_id("PARENT_CHAIN","Holding"))

    def test_CP11_parent_conflict(self):
        report, *_ = replay({"Raw":sheet(upc=None),"OtherRaw":sheet(8,upc=None)}, [raw(),raw("OtherRaw")])
        conflicts = value_conflicts(report, [])
        self.assertEqual(conflicts["current_counts"], {"TRUE_PARENT_VALUE_CONFLICT":1})
        self.assertFalse(report["selected"])

    def test_CP12_parent_child_different_keys(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet(8)}, [raw(),profile()])
        self.assertEqual(len(report["selected"]), 2)
        self.assertEqual(len({r["fact_scope_id"] for r in report["selected"]}), 2)

    def test_CP13_forbid_mixed_rollup(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet()}, [raw(),profile()])
        with self.assertRaises(ValueError):
            aggregate_scope(report["selected"],fact_scope_id=report["selected"][0]["fact_scope_id"])
        self.assertEqual(aggregate_scope(report["selected"][:1],fact_scope_id=report["selected"][0]["fact_scope_id"]),Decimal(5))

    def test_CP14_three_memberships(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet("=1"),"Red":sheet("=1"),"Green":sheet("=1")},
                           [raw(),profile(),profile("Red","R"),profile("Green","G")])
        self.assertEqual(len(report["selected"][0]["children"]),3)

    def test_CP15_overlap_does_not_choose_child(self):
        self.test_CP04_no_fanout()

    def test_CP16_another_overlap(self):
        self.test_CP14_three_memberships()

    def test_CP17_review_group_trace(self):
        report, *_ = self.multiple()
        trace = trace_baseline(old_decision("MULTIPLE_COMMERCIAL_MEMBERSHIP", [{"source_sha256":HASH,"sheet":"Raw","cell":"C2"}]),report)
        self.assertEqual(len(trace["review_decisions"]),1)
        self.assertEqual(trace["review_decisions"][0]["new_results"],{"CHILD_MEMBERSHIP_MULTI":1})

    def test_CP18_trace_missing_child_not_fact_block(self):
        report, *_ = parent_fixture(False)
        old = old_decision("NO_COMMERCIAL_MEMBERSHIP",[{"source_sha256":HASH,"sheet":"Raw","cell":"C2"}])
        row = trace_baseline(old,report)["baseline_membership"][0]
        self.assertEqual(row["final_status"],"PARENT_VALID_CHILD_UNKNOWN")
        self.assertFalse(row["fact_scope_blocking"])
        report["source_assignments"] = []
        self.assertTrue(trace_baseline(old,report)["baseline_membership"][0]["fact_scope_blocking"])

    def test_CP19_value_trace_distinct_scopes_not_winner(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet(8)}, [raw(),profile()])
        sources=[{"source_sha256":HASH,"sheet":name,"cell":"C2"} for name in ("Raw","Blue")]
        trace=trace_baseline(old_decision("INTERNAL_SOURCE_DUPLICATE",sources),report)["baseline_values"]
        self.assertEqual(trace[0]["final_status"],"RESOLVED_DISTINCT_FACT_SCOPES")
        self.assertTrue(trace[0]["no_value_winner_chosen"])

    def test_CP20_separate_coverages(self):
        report,resolver,*_ = parent_fixture(False)
        facts,children = coverage(report,resolver)
        self.assertEqual(facts["selected_observations"],1)
        self.assertEqual(children["parent_products_parent_only"],1)

    def test_CP21_parent_eligible(self):
        report,resolver,*_ = parent_fixture(False)
        facts,_=coverage(report,resolver)
        self.assertEqual(facts["series"][0]["eligibility"],"FORECAST_PARENT_ELIGIBLE")

    def test_CP22_child_catalog_only(self):
        report,resolver,*_ = parent_fixture()
        facts,_=coverage(report,resolver)
        self.assertIn("CATALOG_ONLY",{s["eligibility"] for s in facts["series"]})

    def test_CP23_child_eligible(self):
        report,resolver,*_ = replay({"Blue":sheet()},[profile()])
        facts,_=coverage(report,resolver)
        self.assertEqual(facts["series"][0]["eligibility"],"FORECAST_CHILD_ELIGIBLE")

    def test_CP24_dynamic_parent(self):
        report,*_=replay({"Raw":sheet(upc=None)},[raw()],{"Raw":{"parent_chain":"UnseenParent"}})
        self.assertEqual(report["selected"][0]["canonical_code"],"UnseenParent")

    def test_CP25_dynamic_independent_unit(self):
        report,*_=replay({"Unseen":sheet()},[profile("Unseen","Independent")])
        self.assertEqual(report["selected"][0]["canonical_code"],"Independent")
        self.assertEqual(report["selected"][0]["parent_chain"],None)
        self.assertNotEqual(scope_id("COMMERCIAL_UNIT","Independent"),scope_id("PARENT_CHAIN","Independent"))

    def test_CP26_27_no_pilot_branch(self):
        for filename in ("services/assistant_api/hierarchical_grain.py","scripts/reconstruct_hierarchical_grain.py"):
            text=(ROOT/filename).read_text(encoding="utf-8").casefold()
            for forbidden in ("walmart","fendi","750189","101285"):
                self.assertNotIn(forbidden,text)

    def test_CP28_order_independent(self):
        sheets={"Raw":sheet(upc=None),"Blue":sheet()}
        ps=[raw(),profile()]
        a,*_=replay(sheets,ps)
        b,*_=replay(dict(reversed(list(sheets.items()))),ps[::-1])
        self.assertEqual(a,b)

    def test_CP29_duplicates_not_added(self):
        report,*_=replay({"Raw":sheet(upc=None),"OtherRaw":sheet(upc=None)},[raw(),raw("OtherRaw")])
        self.assertEqual(len(report["selected"]),1)
        fact=report["selected"][0]
        with self.assertRaises(ValueError):
            aggregate_scope([fact,fact],fact_scope_id=fact["fact_scope_id"])

    def test_CP30_unknown_available_at(self):
        report,*_=parent_fixture()
        self.assertTrue(all(f["available_at"] is None and f["availability_source"]=="UNKNOWN" for f in report["versions"]))

    def test_CP33_missing_not_zero_future_monthly(self):
        report,*_=replay({"Raw":sheet(None)},[raw()])
        self.assertFalse(report["selected"])
        self.assertEqual(report["summary"]["excluded"]["MISSING"],1)
        p=replace(raw(),period_layout={"cutoff":"2030-01"},metric_layout=({"column":"C","period":"2030-01","metric":"SALES"},))
        future,*_=replay({"Raw":sheet(upc=None)},[p])
        self.assertEqual(future["selected"][0]["key"][2],"2030-01")

    def test_CP34_no_schema_or_runtime_import(self):
        self.assertEqual(len(list((ROOT/"supabase/migrations").glob("*.sql"))),7)
        self.assertNotIn("hierarchical_grain",(ROOT/"services/assistant_api/api.py").read_text())

    def test_CP35_runtime_off(self):
        with patch.dict("os.environ",{},clear=True):
            s=Settings.from_env()
        self.assertEqual((s.persistence_provider,s.data_provider),("sqlite","normalized"))
        self.assertFalse(any((s.supabase_enabled,s.openai_enabled,s.deep_research_enabled,s.voice_enabled)))

    def test_scope_configuration_guards(self):
        _,resolver,config,ps=parent_fixture()
        for change in ({"approved":False},{"rule_sha256":""}):
            with self.assertRaises(ValueError):
                GrainResolver(ps,{**config,**change},resolver.memberships)
        with self.assertRaises(ValueError):
            GrainResolver(ps,{**config,"source_scopes":config["source_scopes"][:-1]},resolver.memberships)
        record=SourceScope(**config["source_scopes"][0])
        for change in ({"profile_sha256":"wrong"},{"fact_grain":"BAD"},{"parent_chain":""},
                       {"explicit_unit_columns":{}},{"commercial_unit":"NotAParent"}):
            with self.assertRaises(ValueError):
                replace(record,**change).validate(ps[0],RULE)
        resolver.register("COMMERCIAL_UNIT","Parent::Blue","FirstKnownParent")
        with self.assertRaises(ValueError):
            resolver.register("COMMERCIAL_UNIT","Parent::Blue","Different")

    def test_scope_authority_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            rule,config=Path(directory)/"rule.txt",Path(directory)/"scope.json"
            rule.write_text("Owner rule",encoding="utf-8")
            value={"approved":True,"approved_by":"owner","rule_sha256":sha256_file(rule)}
            config.write_text(json.dumps(value),encoding="utf-8")
            args={"expected_sha":sha256_file(config),"rule_path":rule,"rule_sha":sha256_file(rule)}
            self.assertEqual(load_scope_authority(config,**args),value)
            with self.assertRaises(ValueError):
                load_scope_authority(config,**dict(args,expected_sha="0"*64))
            value["approved"]=False
            config.write_text(json.dumps(value),encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scope_authority(config,**dict(args,expected_sha=sha256_file(config)))

    def test_lookup_strict_no_evaluation(self):
        p=profile()
        self.assertIsNotNone(lookup_reference("=_xlfn.XLOOKUP($A2,'Some Base'!A:A,'Some Base'!C:C)",p,2))
        for formula in ("=XLOOKUP(D2,Raw!A:A,Raw!C:C)","=SUM(A1:A2)","=XLOOKUP(A2,Raw!A1:A10,Raw!C1:C10)",
                        "=XLOOKUP(A2,Raw!A:A,Other!C:C)","=XLOOKUP(A2,Raw!A:A,Raw!C:C)*2"):
            self.assertIsNone(lookup_reference(formula,p,2))

    def test_distinct_scope_resolution_requires_accepted_values(self):
        report, *_ = replay({"Raw":sheet(upc=None),"Blue":sheet(7)}, [raw(),profile()])
        sources = [{"source_sha256":a["source_sha256"],"sheet":a["sheet"],"cell":a["cell"]}
                   for a in report["source_assignments"]]
        old = {"resolution_ledger":[{"blocking":True,"reason_code":"INTERNAL_SOURCE_DUPLICATE",
                                    "key":["Old","1001","2024-01","SALES"],"sources":sources,"values":["5","7"]}]}
        self.assertEqual(trace_baseline(old,report)["baseline_values"][0]["final_status"],"RESOLVED_DISTINCT_FACT_SCOPES")
        report["selected"] = report["selected"][:1]
        trace = trace_baseline(old,report)["baseline_values"][0]
        self.assertEqual(trace["final_status"],"VALUE_REVIEW_REQUIRED")
        self.assertTrue(trace["value_blocking"])


@unittest.skipUnless((PRIVATE/"reconciliation.json").exists(),"Private corpus intentionally excluded from Git")
class PrivateGrainCertification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read=staticmethod(lambda name:json.loads((PRIVATE/(name+".json")).read_text(encoding="utf-8")))
        cls.report=cls.read("reconciliation")
        cls.policy=cls.read("certification_contract")
        cls.legacy_path=ROOT/"outputs/prd09_2d22/reconciliation.json"
        cls.legacy=json.loads(cls.legacy_path.read_text(encoding="utf-8"))
        cls.cohort=legacy_cohort(cls.legacy,cls.policy)
        cls.value_path=ROOT.parent/"outputs/prd01_fendi_bd/data/fendi_bd_facts.csv"
        cls.reference=read_value_golden(cls.value_path,expected_sha=cls.policy["legacy_csv_sha256"],
                                        cohort=cls.cohort,metric_map=cls.policy["metric_map"])
        cls.golden=certify_hierarchy(cls.cohort,cls.report,reference=cls.reference,policy=cls.policy)

    def test_CP17_18_19_exact_baseline(self):
        trace=self.read("membership_reinterpretation")
        self.assertEqual(len(trace["review_decisions"]),12)
        self.assertEqual(sum(g["old_affected_facts"] for g in trace["review_decisions"]),9016)
        self.assertEqual(len(trace["baseline_membership"]),9016)
        self.assertEqual(len({r["old_decision_id"] for r in trace["baseline_membership"]}),9016)
        self.assertEqual(len(trace["baseline_values"]),729)
        self.assertTrue(all(r["original_values"] and r["source_locators"] for r in trace["baseline_values"]))

    def test_CP31A_legacy_values_96_preserved(self):
        self.assertEqual(self.golden["status"],"PASS")
        self.assertEqual(self.golden["summary"]["canonical_facts_matched"],96)
        self.assertEqual(self.golden["summary"]["value_matches"],96)
        self.assertEqual(self.golden,self.read("hierarchical_scope_golden"))

    def test_CP31B_72_supported_BD_scope(self):
        rows=[r for r in self.golden["matches"] if r["scope"]=="Walmart::BD"]
        self.assertEqual(len(rows),72)
        self.assertTrue(all(r["grain"]=="COMMERCIAL_UNIT" for r in rows))

    def test_CP31C_24_December_parent_scope(self):
        rows=[r for r in self.golden["matches"] if r["scope"]=="Walmart"]
        self.assertEqual(len(rows),24)
        self.assertTrue(all(r["grain"]=="PARENT_CHAIN" and r["legacy_identity"][1]=="2024-12" for r in rows))

    def test_CP31D_zero_lost(self):
        self.assertEqual(self.golden["summary"]["missing"],0)
        self.assertEqual({tuple(r["legacy_identity"]) for r in self.golden["matches"]},
                         {tuple(r["key"][1:]) for r in self.cohort})

    def test_CP31E_zero_changed_values(self):
        self.assertEqual(self.golden["summary"]["changed_values"],0)
        self.assertTrue(all(Decimal(r["legacy_value"])==Decimal(r["canonical_value"]) for r in self.golden["matches"]))

    def test_CP31F_zero_duplicates(self):
        self.assertEqual(self.golden["summary"]["duplicates"],0)
        self.assertEqual(self.golden["summary"]["facts_created"],0)
        self.assertEqual(len({tuple(r["canonical_key"]) for r in self.golden["matches"]}),96)

    def test_CP31G_zero_unsupported_child(self):
        self.assertEqual(self.golden["summary"]["unsupported_child_assignments"],0)
        self.assertEqual(self.golden["summary"]["identity_changes"],0)
        self.assertTrue(self.golden["membership_is_not_quantity_authority"])

    def test_CP31H_legacy_bytes_and_content_frozen(self):
        self.assertEqual(sha256_file(self.value_path),self.policy["legacy_csv_sha256"])
        self.assertEqual(sha256_file(self.legacy_path),self.policy["legacy_report_sha256"])
        self.assertEqual(self.reference["content_sha256"],self.policy["legacy_csv_content_sha256"])
        self.assertFalse(self.reference["modified"])

    def test_CP32_all_scope_literals(self):
        certificate=self.read("literal_certificate")
        self.assertEqual(certificate["all_literals_checked"],len(self.report["selected"]))
        self.assertEqual(certificate["units_sampled"],len({f["fact_scope_id"] for f in self.report["selected"]}))
        self.assertEqual(certificate["differences"],0)

    def test_CP28_29_30_hash_and_dates(self):
        self.assertEqual(self.read("determinism")["status"],"PASS")
        digest=hashlib.sha256(json.dumps(self.report["versions"],sort_keys=True,separators=(",",":")).encode()).hexdigest()
        self.assertEqual(digest,self.report["summary"]["dataset_sha256"])
        self.assertTrue(all(f["available_at"] is None and f["availability_source"]=="UNKNOWN" for f in self.report["versions"]))
        self.assertEqual(len({tuple(f["key"]) for f in self.report["selected"]}),len(self.report["selected"]))
        self.assertEqual(self.report["ownership_invariants"]["derived_child_facts_created"],0)


if __name__=="__main__":
    unittest.main()
