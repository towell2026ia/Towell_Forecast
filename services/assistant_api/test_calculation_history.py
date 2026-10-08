"""E4 calculation/decision/LIVE rules; all data is synthetic test evidence."""
import copy
import unittest

from services.assistant_api.calculation_history import (
    learning_event, live_metrics, live_rows, selection_curve, snapshot_from_preview, validate_selection,
)
from services.assistant_api.supabase_access import PreviewError
from services.forecast_engine.engine import add_month


CHAIN = "00000000-0000-4000-8000-000000000001"
PRODUCT = "00000000-0000-4000-8000-000000000002"
ACTOR = "00000000-0000-4000-8000-000000000003"


def preview():
    horizons = [{"horizon": n, "target_period": add_month("2026-07", n),
        "statistical_value": 100 + n, "ml_value": 90 + n, "ensemble_value": 95 + n,
        "statistical_model": "SBA", "ml_model": "Random Forest Global",
        "p10": None, "p50": 95 + n, "p90": None, "p95": None,
        "band_basis": "INSUFFICIENT", "band_observations": 0} for n in range(1, 13)]
    return {"chain_id": CHAIN, "objective": "Venta", "issue_period": "2026-07",
        "cutoff": "2026-07-31T23:59:59Z", "status": "PREVIEW", "engine_version": "test",
        "dataset_hash": "a" * 64, "evidence_mode": "RETROSPECTIVE_TRAINING",
        "products": [{"product_id": PRODUCT, "product_code": "SKU", "description": "Fixture",
            "category_id": None, "forecast_status": "ACTIVE", "horizons": horizons}],
        "selection": {"product_candidates": [
            {"family": "statistical", "product_id": PRODUCT, "model": "SBA", "wape": 18.9, "bias": 0.2},
            {"family": "ml", "product_id": PRODUCT, "model": "Random Forest Global", "wape": 19.4, "bias": 0.8,
             "horizons": [{"horizon": n, "target_period": add_month("2026-07", n), "value": 90+n}
                          for n in range(1, 13)]},
            {"family": "ensemble", "product_id": PRODUCT, "strategy": "80/20", "wape": 18.1,
             "bias": 0.5, "statistical_weight": .5, "ensemble_ml_model": "Scope Forest",
             "horizons": [{"horizon": n, "target_period": add_month("2026-07", n),
                           "value": 95+n, "ml_component": 90+n}
                          for n in range(1, 13)]}],
            "suggested_references": {PRODUCT: {"family": "ensemble", "model": "Ensemble ponderado",
                "wape": 18.1, "official": False}}, "scope_leader": {"family": "ensemble"}}}


def calculation():
    data = snapshot_from_preview(preview(), product_id=PRODUCT, preview_id="preview-1", actor_id=ACTOR,
        git_sha="test", input_observation_ids=["observation-1"])
    return {**data, "id": "calc-1", "status": "READY_FOR_DECISION"}


class CalculationHistoryTests(unittest.TestCase):
    def test_snapshot_preserves_three_curves_and_null_bands(self):
        result = calculation()
        self.assertEqual(len(result["horizons"]), 12)
        self.assertEqual(result["suggested_reference"]["family"], "ensemble")
        self.assertEqual(result["horizons"][0]["statistical_value"], 101)
        self.assertEqual(result["horizons"][0]["ml_value"], 91)
        self.assertEqual(result["horizons"][0]["ensemble_value"], 96)
        self.assertIsNone(result["horizons"][0]["p50"])
        self.assertEqual(result["horizons"][0]["band_basis"], "INSUFFICIENT")

    def test_selected_curve_is_all_twelve_and_never_mutates_source(self):
        source = calculation()
        before = copy.deepcopy(source)
        curve = validate_selection(source, "ENSEMBLE", None, None)
        self.assertEqual([row["value"] for row in curve], [row["ensemble_value"] for row in source["horizons"]])
        self.assertEqual(source, before)

    def test_product_ml_curve_cannot_be_replaced_by_scope_ml_curve(self):
        source = preview()
        for horizon in source["products"][0]["horizons"]:
            horizon["ml_value"] = 9999  # unrelated scope-level ML selection
            horizon["ensemble_value"] = 9999
        saved = snapshot_from_preview(source, product_id=PRODUCT, preview_id="p", actor_id=ACTOR, git_sha="test")
        self.assertEqual(saved["horizons"][0]["ml_value"], 91)
        self.assertEqual(saved["horizons"][0]["ensemble_value"], 96)
        self.assertEqual(saved["horizons"][0]["statistical_weight"], .5)

    def test_ensemble_requires_its_actual_ml_component_and_valid_arithmetic(self):
        source = preview()
        source["selection"]["product_candidates"][2]["horizons"][0]["ml_component"] = 9000
        with self.assertRaises(PreviewError):
            snapshot_from_preview(source, product_id=PRODUCT, preview_id="p", actor_id=ACTOR, git_sha="test")

    def test_deviation_requires_reason_and_other_requires_comment(self):
        source = calculation()
        with self.assertRaises(PreviewError):
            validate_selection(source, "STATISTICAL", None, None)
        with self.assertRaises(PreviewError):
            validate_selection(source, "STATISTICAL", "Otro", None)
        self.assertEqual(len(validate_selection(source, "STATISTICAL", "Cambio de precio", None)), 12)

    def test_ml_absent_is_not_fabricated(self):
        source = preview()
        for horizon in source["products"][0]["horizons"]:
            horizon["ml_value"] = horizon["ensemble_value"] = None
        source["selection"]["product_candidates"] = source["selection"]["product_candidates"][:1]
        saved = snapshot_from_preview(source, product_id=PRODUCT, preview_id="p", actor_id=ACTOR, git_sha="test")
        saved["status"] = "READY_FOR_DECISION"
        self.assertTrue(all(row["ml_value"] is None for row in saved["horizons"]))
        with self.assertRaises(PreviewError):
            selection_curve(saved, "ML")

    def test_live_original_horizons_and_ratio_of_sums(self):
        first = calculation()
        second = copy.deepcopy(first)
        second["id"] = "calc-2"
        second["issue_period"] = "2026-08-01"
        second["horizons"] = [{**row, "horizon": row["horizon"]-1} for row in second["horizons"][1:]]
        second["horizons"].append({**second["horizons"][-1], "horizon": 12, "target_period": "2027-08-01"})
        third = copy.deepcopy(second)
        third["id"] = "calc-3"
        third["issue_period"] = "2026-09-01"
        third["horizons"] = [{**row, "horizon": row["horizon"]-1} for row in third["horizons"][1:]]
        third["horizons"].append({**third["horizons"][-1], "horizon": 12, "target_period": "2027-09-01"})
        # One October actual evaluates July H3, August H2 and September H1.
        rows = live_rows([first, second, third], period="2026-10", sale={"id": "sale-v2", "value": 102},
            order={"id": "order-v1", "value": 120}, delivery={"id": "delivery-v1", "value": 90},
            selections={"calc-1": {"selected_candidate": "ENSEMBLE"}})
        self.assertEqual({(row["calculation_id"], row["horizon"]) for row in rows},
            {("calc-1", 3), ("calc-2", 2), ("calc-3", 1)})
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["sale_observation_id"] == "sale-v2" for row in rows))
        metric = live_metrics(rows)["STATISTICAL:H3"]
        self.assertAlmostEqual(metric["fill_rate"], 75)
        self.assertEqual(metric["evaluations"], 1)

    def test_wape_is_ratio_not_average_ape(self):
        rows = [{"calculation_id": "calc-a", "candidate": "STATISTICAL", "horizon": 1, "actual_sale": 100, "absolute_error": 10,
            "signed_error": -10, "order_value": 100, "delivery_value": 90},
            {"calculation_id": "calc-b", "candidate": "STATISTICAL", "horizon": 1, "actual_sale": 10, "absolute_error": 5,
             "signed_error": 5, "order_value": 20, "delivery_value": 20}]
        metric = live_metrics(rows)["STATISTICAL:H1"]
        self.assertAlmostEqual(metric["wape"], 15 / 110 * 100)
        self.assertAlmostEqual(metric["fill_rate"], 110 / 120 * 100)

    def test_learning_reports_regret_without_promotion(self):
        source = calculation()
        rows = live_rows([source], period="2026-08", sale={"id": "sale", "value": 95},
            order={"id": "order", "value": 100}, delivery={"id": "delivery", "value": 70},
            selections={"calc-1": {"selected_candidate": "STATISTICAL"}})
        event = learning_event(rows)
        self.assertEqual(event["best_candidate_actual"], "ENSEMBLE")
        self.assertGreater(event["decision_regret_absolute"], 0)
        self.assertIn("LOW_FILL_RATE", event["signals"])
        self.assertNotIn("champion", event)


if __name__ == "__main__":
    unittest.main()
