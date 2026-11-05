import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from openpyxl import Workbook
from services.planning_pilot.engine import (SOURCES, UNKNOWN_UNIT, apply_units, digest, evaluate_live,
    extension, forecast_product, make_version, read_source, tail, wape)
from services.statistical_engine import engine as stat


def sample():
    history = {stat.add_month("2024-01", i): {"value": 10.25+i, "original": 10.25+i, "pieces": None,
        "unit": UNKNOWN_UNIT, "unit_evidence": None, "cell": f"Test!M{i+1}", "pack_factor": 12} for i in range(34)}
    return {"id": "TEST|001", "description": "SYNTHETIC", "history": {s: copy.deepcopy(history) for s in SOURCES},
            "issues": [], "proposal": None}


class PilotTests(unittest.TestCase):
    def test_original_units_never_inherit_packaging(self):
        p = sample()
        result = forecast_product(p, "2026-10")
        self.assertEqual(result["status"], "PROVISIONAL")
        self.assertTrue(all(r["pieces"] is None for r in result["projection"]))
        self.assertIsNone(result["retrospective"]["Pedidos"]["value"])
        self.assertEqual(p["history"]["Towell"]["2024-01"]["value"], 10.25)

    def test_explicit_units_are_source_product_and_period_scoped(self):
        p = sample()
        apply_units([p], {"Towell": {p["id"]: {"unit": "pack", "pieces_per_unit": 6,
            "from": "2026-10", "through": "2026-10", "evidence": "Synthetic signed specification"}}})
        self.assertEqual(p["history"]["Towell"]["2026-10"]["pieces"], 43.25*6)
        self.assertIsNone(p["history"]["Pedidos"]["2026-10"]["pieces"])
        self.assertIsNone(p["history"]["Towell"]["2026-09"]["pieces"])

    def test_blank_breaks_tail_zero_and_decimal_remain(self):
        h = sample()["history"]["Towell"]
        h["2026-08"]["value"] = None
        h["2026-09"]["value"] = 0
        self.assertEqual(tail(h, "2026-10"), (["2026-09", "2026-10"], [0,43.25]))

    def test_extension_preserves_contract_and_requires_evidence(self):
        values = [10.25 + i for i in range(34)]
        initial = stat.clamp(stat.naive(values, 12))
        result = extension("Naive", values, initial)
        self.assertEqual([r["value"] for r in result], [43.25,43.25])
        self.assertTrue(all(r["backtest"]["pairs"] >= 3 for r in result))
        self.assertTrue(all(r["value"] is None for r in extension("Naive", values[:15], stat.clamp(stat.naive(values[:15],12)))))
        self.assertEqual(stat.HORIZONS, (1,3,6,12))
        self.assertTrue(all(r["value"] is None for r in extension("Naive", values, [99]*12)))

    def test_wape_is_ratio_of_sums_and_live_requires_prior_emission(self):
        self.assertEqual(wape([(10,20),(90,90)])["value"], 10)
        self.assertIsNone(wape([(0,0)])["value"])
        args = dict(forecast=20, actual=10, target="2026-11", actual_closed=True, comparable=True)
        self.assertIsNone(evaluate_live(**args, emitted_at="2026-11-01T01:00:00+00:00"))
        self.assertEqual(evaluate_live(**args, emitted_at="2026-10-30T01:00:00+00:00"),100)
        self.assertIsNone(evaluate_live(**{**args,"comparable":False}, emitted_at="2026-10-30T01:00:00+00:00"))

    def test_versions_keep_original_and_reject_overwrite(self):
        p = sample()
        first = make_version([p],{},cutoff="2026-10",issued_at="2026-11-05T00:00:00+00:00",version_id="V1",code_sha="test")
        before = digest(first)
        revised = make_version([p],{},cutoff="2026-12",issued_at="2027-01-05T00:00:00+00:00",version_id="V2",code_sha="test",previous=first)
        self.assertEqual(digest(first),before)
        self.assertEqual(revised["versions"][0],first["versions"][0])
        self.assertEqual(revised["versions"][1]["original_id"],"V1")
        with self.assertRaises(ValueError):
            make_version([p],{},cutoff="2026-12",issued_at="2027-01-05T00:00:00+00:00",version_id="V1",code_sha="test",previous=first)
        tampered=copy.deepcopy(first);tampered["versions"][0]["products"][0]["projection"][0]["value"]+=1
        with self.assertRaisesRegex(ValueError,"Integridad"):
            make_version([p],{},cutoff="2026-12",issued_at="2027-01-05T00:00:00+00:00",version_id="V2",code_sha="test",previous=tampered)

    def test_individual_failure_does_not_abort_batch(self):
        with patch("services.planning_pilot.engine.forecast_product", side_effect=[RuntimeError("Synthetic failure"),forecast_product(sample(),"2026-10")]):
            result=make_version([sample(),sample()],{},cutoff="2026-10",issued_at="2026-11-05T00:00:00Z",version_id="V1",code_sha="test",closure_confirmed_on="2026-11-05")
        self.assertEqual([p["status"] for p in result["versions"][0]["products"]],["PENDING","PROVISIONAL"])
        self.assertEqual(result["versions"][0]["closure_confirmed_on"],"2026-11-05")

    def test_extension_rejects_unstable_jump_even_with_finite_prefix(self):
        values=[10]*34
        def unstable(v,h):
            return [10]*12+[1000]*(h-12) if h>12 else [10]*h
        with patch.dict(stat.MODELS,{"Synthetic unstable":unstable}):
            result=extension("Synthetic unstable",values,[10]*12)
        self.assertTrue(all(r["value"] is None for r in result))

    def test_adapter_union_preserves_history_only_proposal_only_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"synthetic.xlsx"
            book=Workbook();book.remove(book.active)
            for source in SOURCES:
                sheet=book.create_sheet(source)
                row=[None]*44
                row[0:6]=["TEST","TEST CHAIN",1,"FORMAT","0001","VARIOS"]
                row[9:13]=["Octubre",0,2.125,None]
                row[30:34]=["MODEL","SIZE",None,"VARIOS"]
                row[39]=12
                for j,value in enumerate(row,1):sheet.cell(6,j,value)
            sheet=book.create_sheet("Propuesta RS Dic Def")
            for col,value in {1:"TEST",2:"TEST CHAIN",4:"FORMAT",5:"0002",6:"NEW",12:"Noviembre",19:7,22:"MODEL",42:"CODE"}.items():sheet.cell(6,col,value)
            book.create_sheet("Hoja1");book.save(path);book.close()
            products,audit=read_source(path)
            self.assertEqual(len(products),2)
            self.assertEqual(audit["history_only"],1)
            self.assertEqual(audit["proposal_only"],1)
            self.assertEqual(products[0]["history"]["Towell"]["2025-10"]["value"],2.125)
            self.assertIsNone(products[0]["history"]["Towell"]["2026-10"]["value"])


if __name__ == "__main__":
    unittest.main()
