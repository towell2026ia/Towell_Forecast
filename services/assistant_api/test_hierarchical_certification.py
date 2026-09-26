"""Synthetic CP31 contracts, including negative mutation controls for public CI."""
import copy
from pathlib import Path
import tempfile
import unittest

from services.assistant_api.hierarchical_certification import certify_hierarchy, legacy_cohort, read_value_golden
from services.assistant_api.historical_corpus import sha256_file
from services.assistant_api.test_hierarchical_grain import parent_fixture, replay, raw
from services.assistant_api.test_chain_ingestion import profile, sheet


class HierarchicalGoldenContracts(unittest.TestCase):
    def fixture(self, child=False):
        if child:
            report,*_=replay({"Raw":sheet(upc=None),"Blue":sheet(12)},[raw(),profile()])
            fact=next(f for f in report["selected"] if f["fact_grain"]=="COMMERCIAL_UNIT")
        else:
            report,*_=parent_fixture(False)
            fact=report["selected"][0]
        legacy=copy.deepcopy(fact)
        legacy["key"]=["OldUnprovenChild","7500000000001",*fact["key"][2:]]
        policy={"legacy_scope":"OldUnprovenChild","product_prefix":"7500","period_prefix":"2024",
                "expected_count":1,"expected_scope_counts":{fact["canonical_code"]:1},
                "parent_periods":[] if child else ["2024-01"]}
        return report,legacy,policy

    def certify(self,report,legacy,policy):
        return certify_hierarchy([legacy],report,reference={"type":"LEGACY_VALUE_GOLDEN"},policy=policy)

    def test_values_not_legacy_scope_authority(self):
        report,legacy,policy=self.fixture()
        before=copy.deepcopy(report)
        certificate=self.certify(report,legacy,policy)
        self.assertEqual(certificate["status"],"PASS")
        self.assertEqual(certificate["summary"]["value_matches"],1)
        self.assertEqual(report,before)
        self.assertNotEqual(certificate["matches"][0]["scope"],legacy["key"][0])

    def test_supported_child_preserved(self):
        report,legacy,policy=self.fixture(True)
        legacy["key"][1]=next(f for f in report["selected"] if f["fact_grain"]=="COMMERCIAL_UNIT")["key"][1]
        self.assertEqual(self.certify(report,legacy,policy)["status"],"PASS")

    def test_cohort_exact_and_unique(self):
        _,legacy,policy=self.fixture()
        self.assertEqual(legacy_cohort({"selected":[legacy]},policy),[legacy])
        for rows in ([],[legacy,legacy]):
            with self.assertRaises(ValueError):
                legacy_cohort({"selected":rows},policy)

    def test_value_change_fails(self):
        report,legacy,policy=self.fixture()
        report["selected"][0]["value"]="999"
        certificate=self.certify(report,legacy,policy)
        self.assertEqual(certificate["status"],"FAIL")
        self.assertEqual(certificate["summary"]["changed_values"],1)

    def test_missing_or_shifted_period_metric_fails(self):
        for component in (2,3,None):
            report,legacy,policy=self.fixture()
            if component is None:
                report["selected"]=[]
            else:
                report["selected"][0]["key"][component]="2031-01" if component==2 else "ORDER"
            self.assertEqual(self.certify(report,legacy,policy)["summary"]["missing"],1)

    def test_duplicate_fails(self):
        report,legacy,policy=self.fixture()
        report["selected"].append(copy.deepcopy(report["selected"][0]))
        certificate=self.certify(report,legacy,policy)
        self.assertEqual(certificate["status"],"FAIL")
        self.assertEqual(certificate["summary"]["duplicates"],1)

    def test_product_mutation_fails(self):
        report,legacy,policy=self.fixture()
        report["selected"][0]["key"][1]="DifferentProduct"
        self.assertEqual(self.certify(report,legacy,policy)["status"],"FAIL")

    def test_unsupported_child_fails_not_parent_fallback(self):
        report,legacy,policy=self.fixture(True)
        child=next(f for f in report["selected"] if f["fact_grain"]=="COMMERCIAL_UNIT")
        legacy["key"][1]=child["key"][1]
        for a in report["source_assignments"]:
            if a["fact_grain"]=="COMMERCIAL_UNIT":
                a["scope_evidence"]["value_scope_certified"]=False
        certificate=self.certify(report,legacy,policy)
        self.assertEqual(certificate["status"],"FAIL")
        self.assertGreater(certificate["summary"]["unsupported_child_assignments"],0)

    def test_legacy_file_bytes_frozen_and_no_scope_dependency(self):
        _,legacy,_=self.fixture()
        text="upc,period,metric,value\n7500000000001,2024-01,Venta,"+legacy["value"]+"\n"
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.csv"
            path.write_text(text,encoding="utf-8")
            digest=sha256_file(path)
            reference=read_value_golden(path,expected_sha=digest,cohort=[legacy],metric_map={"Venta":"SALES"})
            self.assertEqual(reference["type"],"LEGACY_VALUE_GOLDEN")
            self.assertEqual(sha256_file(path),digest)
            self.assertTrue(reference["scope_is_not_authority"])
            path.write_text(text+"\n",encoding="utf-8")
            with self.assertRaises(ValueError):
                read_value_golden(path,expected_sha=digest,cohort=[legacy],metric_map={"Venta":"SALES"})

    def test_conflicting_or_missing_legacy_value_fails(self):
        _,legacy,_=self.fixture()
        for content in ("upc,period,metric,value\n", "upc,period,metric,value\n7500000000001,2024-01,Venta,7\n"):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"legacy.csv"
                path.write_text(content,encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_value_golden(path,expected_sha=sha256_file(path),cohort=[legacy],metric_map={"Venta":"SALES"})


if __name__=="__main__":
    unittest.main()
