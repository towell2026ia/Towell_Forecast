"""Synthetic stored-result reads: exact curves, scopes and no forecast calls."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from services.assistant_api.rs_assistant import RSAssistantRequest, RSAssistantWorkflow
from services.assistant_api.rs_assistant_forecasts import stored_rs_forecast
from services.assistant_api.supabase_access import PreviewError
from services.forecast_engine.engine import add_month


CHAIN = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
BLUE = "00000000-0000-4000-8000-000000000011"
GREY = "00000000-0000-4000-8000-000000000012"
C1 = "00000000-0000-4000-8000-000000000021"
C2 = "00000000-0000-4000-8000-000000000022"
C3 = "00000000-0000-4000-8000-000000000023"
CONTEXT = {"source_hash": "a" * 64, "generation": 1, "first_period": "2025-01", "last_period": "2026-10"}


def header(calc=C1, product=BLUE, number=1, created="2026-11-11T17:00:00+00:00", engine="existing-e4"):
    return {"id": calc, "chain_id": CHAIN, "product_id": product, "objective": "Venta",
            "calculation_no": number, "calculation_code": "C" + str(number),
            "issue_period": "2026-10-01", "status": "READY_FOR_DECISION", "created_at": created,
            "engine_version": engine, "evidence_mode": "RETROSPECTIVE_TRAINING",
            "input_snapshot": {"source_context": dict(CONTEXT), "product": {"offline_provenance": {
                "emitted_at": "2026-11-11T15:40:40.957051+00:00"}}}}


def curve(calc=C1, product=BLUE, value="10.123", ml=None):
    return [{"calculation_id": calc, "chain_id": CHAIN, "product_id": product, "horizon": h,
             "target_period": add_month("2026-10", h) + "-01", "statistical_value": value,
             "ml_value": ml, "ensemble_value": "0"} for h in range(1, 13)]


class Client:
    def __init__(self):
        self.headers = [header(), header(C2, GREY)]
        self.horizons = curve() + curve(C2, GREY, "20.000", ml="25.000")
        self.context = dict(CONTEXT)
        self.calls = []

    def rows(self, table, params):
        self.calls.append((table, copy.deepcopy(params)))
        if table == "forecast_calculations":
            return copy.deepcopy(self.headers)
        if table == "forecast_calculation_horizons":
            ids = params["calculation_id"][4:-1].split(",")
            return copy.deepcopy([r for r in self.horizons if r["calculation_id"] in ids])
        raise AssertionError("unexpected_data_source:" + table)

    def get(self, path):
        self.calls.append((path, {}))
        assert path == "/rest/v1/rpc/operational_read_context"
        return [copy.deepcopy(self.context)]


class StoredRsForecastTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def read(self, **overrides):
        return stored_rs_forecast(self.client, CONTEXT, **{**dict(chain_id=CHAIN, product_ids=[BLUE, GREY], period="2026-10"), **overrides})

    def test_exact_model_sum_partial_ml_zero_and_original_emission(self):
        value = self.read()
        self.assertEqual(value["origin"], "STORED_E4")
        self.assertEqual(value["source_context"], {"source_hash": "a" * 64, "generation": 1})
        self.assertEqual([r["period"] for r in value["periods"]], [add_month("2026-10", h) for h in range(1, 13)])
        for month in value["periods"]:
            self.assertEqual(month["statistical"], {"pieces": "30.123", "covered": 2, "eligible": 2})
            self.assertEqual(month["ml"], {"pieces": "25.000", "covered": 1, "eligible": 2})
            self.assertEqual(month["ensemble_value"], {"pieces": "0", "covered": 2, "eligible": 2})
        self.assertIsNone(value["live"])
        self.assertFalse(value["temporal_certification"])
        self.assertEqual(value["retrospective"], {})  # Product WAPEs are never averaged.
        self.assertEqual(value["calculations"][0]["original_emitted_at"], "2026-11-11T15:40:40.957051+00:00")
        self.assertEqual(value["calculations"][0]["created_at"], "2026-11-11T17:00:00+00:00")
        calls = dict(self.client.calls)
        self.assertEqual(calls["forecast_calculations"]["chain_id"], "eq." + CHAIN)
        self.assertEqual(calls["forecast_calculations"]["product_id"], "in.(" + BLUE + "," + GREY + ")")
        self.assertEqual(calls["forecast_calculations"]["issue_period"], "eq.2026-10-01")

    def test_per_variant_latest_compatible_matches_periods_anchor(self):
        self.client.headers = [header(), header(C3, BLUE, 2, "2026-11-12T00:00:00Z", engine="other-engine"),
                               header(C2, GREY, 1, "2026-11-13T00:00:00Z")]
        self.client.horizons += curve(C3, BLUE, "999999", ml="999999")
        result = self.read()
        self.assertEqual(result["periods"][0]["statistical"]["pieces"], "30.123")
        self.assertEqual({r["calculation_id"] for r in result["calculations"]}, {C1, C2})
        self.client.headers[1]["engine_version"] = "existing-e4"
        result = self.read()
        self.assertEqual(result["periods"][0]["statistical"]["pieces"], "1000019.000")
        self.assertEqual(len(result["calculations"]), 2)

    def test_legacy_source_and_other_generation_are_not_a_fallback(self):
        for changed in ({"source_hash": "b" * 64}, {"generation": 2}):
            with self.subTest(changed=changed):
                self.client.headers = [header()]
                self.client.headers[0]["input_snapshot"]["source_context"].update(changed)
                self.assertIsNone(self.read())
                self.assertNotIn("forecast_calculation_horizons", dict(self.client.calls))
                self.client.calls.clear()

    def test_source_change_at_end_rejects_entire_answer(self):
        self.client.context["generation"] = 2
        with self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_CHANGED"):
            self.read()

    def test_missing_variant_is_explicit_partial_and_missing_date_stays_unknown(self):
        self.client.headers = [header()]
        self.client.headers[0]["input_snapshot"]["product"] = {}
        result = self.read()
        self.assertEqual(result["periods"][0]["statistical"], {"pieces": "10.123", "covered": 1, "eligible": 2})
        self.assertEqual(result["periods"][0]["ml"], {"pieces": None, "covered": 0, "eligible": 2})
        self.assertIsNone(result["calculations"][0]["original_emitted_at"])
        self.client.headers = []
        self.assertIsNone(self.read())

    def test_cross_chain_product_and_cut_are_rejected(self):
        for changes in ({"chain_id": OTHER}, {"product_id": C3}, {"issue_period": "2026-07-01"}):
            with self.subTest(changes=changes):
                self.client.headers = [{**header(), **changes}]
                with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
                    self.read()

    def test_incomplete_duplicate_bad_month_and_partial_candidate_fail_closed(self):
        original = copy.deepcopy(self.client.horizons)
        cases = [original[:-1], original + [original[0]],
                 [{**original[0], "target_period": "2025-11-01"}] + original[1:],
                 [{**original[0], "product_id": GREY}] + original[1:],
                 [{**original[0], "ml_value": "3"}] + original[1:],
                 [{**original[0], "statistical_value": True}] + original[1:]]
        for rows in cases:
            with self.subTest(rows=rows[:1]):
                self.client.horizons = rows
                with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
                    self.read()

    def test_answer_uses_saved_e4_without_cache_reads_writes_or_engine(self):
        saved = self.read()
        storage, compute = Mock(), Mock(side_effect=AssertionError("engine_called"))
        storage.get.side_effect = AssertionError("independent_cache_read")
        workflow = RSAssistantWorkflow(storage, compute=compute)
        scoped = {"scope": {"chain_id": CHAIN, "model": "DESIGN", "format": "FORMAT", "period": "2026-10"},
                  "metrics": {}, "quality": {}, "records": [], "stored_forecast": saved}
        body = RSAssistantRequest(chain_id=CHAIN, purchase_type="RS", format="FORMAT", model="DESIGN",
                                  period="2026-10", message="Consulta los pronósticos guardados")
        result = workflow.answer(body, scoped)
        self.assertIs(result["forecast"], saved)
        self.assertNotIn("No hay un cálculo", result["message"])
        self.assertNotIn("forecast", workflow.answer(body, {**scoped, "stored_forecast": None}))
        storage.get.assert_not_called()
        storage.put.assert_not_called()
        compute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
