"""Synthetic coverage of individual ML versus literal Ensemble provenance."""
from copy import deepcopy
from dataclasses import asdict
import unittest

from services.forecast_engine.engine import ForecastPolicy, add_month
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner
from services.assistant_api.preview_representation import project_pedido_individual_ml


def fixture():
    products = []
    for pid, offset in (("one", 0), ("two", 100)):
        products.append({"product_id": pid, "category_id": "SYNTHETIC_CATEGORY", "forecast_status": "ACTIVE",
            "horizons": [{"horizon": h, "target_period": add_month("2026-09", h), "category": "SYNTHETIC_CATEGORY",
                "statistical_value": 100 + offset + h, "statistical": 100 + offset + h,
                "ml_value": 300 + offset + h, "ml": 300 + offset + h, "ml_model": "Scope RF",
                "forecast_towell": 200 + offset + h, "statistical_weight": .5, "ml_weight": .5,
                "ensemble_ml_model": "Scope RF", "ensemble_ml_component": 300 + offset + h}
                for h in range(1, 13)]})
    products.append({"product_id": "stock-only", "category_id": "SYNTHETIC_CATEGORY", "forecast_status": "INSUFFICIENT", "horizons": []})
    candidate = {"product_id": "one", "family": "ml", "model": "Own GB", "evidence_mode": "RETROSPECTIVE_TRAINING",
        "wape": 12.5, "bias": 1.25, "observations": 6, "windows": 6,
        "horizons": [{"horizon": h, "target_period": add_month("2026-09", h), "value": 50 + h} for h in range(1, 13)]}
    return {"chain_id": "synthetic-chain", "objective": "Pedido", "issue_period": "2026-09", "mode": "RETROSPECTIVE_TRAINING",
        "evidence_mode": "RETROSPECTIVE_TRAINING", "dataset_hash": "synthetic-dataset", "products": products,
        "eligibility": {"visible_products": 3, "ml_eligible": 2}, "selection": {"product_candidates": [candidate]},
        "ml": {"status": "COMPLETED", "leader": "Scope RF", "retrospective_wape": 40, "retrospective_bias": 4},
        "aggregates": [{"level": level, "key": group, "horizon": h, "target_period": add_month("2026-09", h),
            "statistical_value": 300 + 2*h, "ml_value": 700 + 2*h, "forecast_towell": 500 + 2*h}
            for level, group in (("chain", "synthetic-chain"), ("category", "SYNTHETIC_CATEGORY")) for h in range(1, 13)]}


class PreviewRepresentationTests(unittest.TestCase):
    def test_individual_candidate_only_and_ensemble_literal_are_separate_and_idempotent(self):
        saved = fixture(); original = deepcopy(saved)
        out = project_pedido_individual_ml(saved)
        self.assertEqual(saved, original)
        self.assertEqual(out["eligibility"]["ml_eligible"], 1)
        self.assertEqual(out["ml"]["models"], {"Own GB": 1})
        self.assertEqual(out["ml"]["leader"], "Own GB")
        self.assertEqual(out["ml"]["scope_leader"], "Scope RF")
        self.assertIsNone(out["ml"]["retrospective_wape"])
        self.assertEqual(out["ml"]["scope_retrospective_wape"], 40)
        for index, product in enumerate(out["products"][:2]):
            for h, row in enumerate(product["horizons"], 1):
                self.assertEqual(row["ml_value"], 50+h if index == 0 else None)
                for field in ("forecast_towell", "statistical_value", "ensemble_ml_model", "ensemble_ml_component", "statistical_weight", "ml_weight"):
                    self.assertEqual(row[field], original["products"][index]["horizons"][h-1][field])
        for row in out["aggregates"]:
            self.assertEqual((row["ml_covered_products"], row["ml_expected_products"], row["ml_partial"]), (1, 3, True))
            self.assertEqual(row["ml_value"], 50+row["horizon"])
            self.assertEqual(row["ensemble_ml_component"], 700+2*row["horizon"])
            self.assertEqual(row["scope_ml_value"], 700+2*row["horizon"])
        self.assertEqual(project_pedido_individual_ml(out), out)

    def test_bad_cached_candidate_is_isolated_without_suppressing_statistical_or_ensemble(self):
        original = fixture()
        variants = []
        duplicate = deepcopy(original); duplicate["selection"]["product_candidates"] *= 2; variants.append(duplicate)
        for change in (None, "invalid", {"horizon": []}, {"horizon": 1, "target_period": "2026-11", "value": 1}):
            altered = deepcopy(original); altered["selection"]["product_candidates"][0]["horizons"][0] = change; variants.append(altered)
        wrong_mode = deepcopy(original); wrong_mode["selection"]["product_candidates"][0]["evidence_mode"] = "POINT_IN_TIME"; variants.append(wrong_mode)
        for value in variants:
            with self.subTest(value=value["selection"]):
                out = project_pedido_individual_ml(value)
                self.assertEqual(out["eligibility"]["ml_eligible"], 0)
                self.assertTrue(all(row["ml_value"] is None for p in out["products"] for row in p["horizons"]))
                self.assertTrue(all(row["ml_value"] is None and row["ml_partial"] and row["ml_expected_products"] == 3 for row in out["aggregates"]))
                self.assertEqual(out["products"][1]["horizons"][0]["forecast_towell"], 301)
                self.assertEqual(out["products"][1]["horizons"][0]["ensemble_ml_component"], 401)

    def test_legacy_unknown_category_does_not_claim_full_coverage(self):
        value = fixture(); value["products"][2]["category_id"] = "00000000-0000-4000-8000-000000000001"
        out = project_pedido_individual_ml(value)
        chain = next(r for r in out["aggregates"] if r["level"] == "chain")
        category = next(r for r in out["aggregates"] if r["level"] == "category")
        self.assertEqual(chain["ml_expected_products"], 3)
        self.assertEqual(category["ml_unknown_category_products"], 1)
        self.assertTrue(category["ml_partial"])

    def test_venta_representation_is_unchanged(self):
        value = fixture(); value["objective"] = "Venta"
        self.assertIs(project_pedido_individual_ml(value), value)

    def test_compatible_old_cache_is_projected_on_read_without_put_or_recomputation(self):
        saved = fixture(); original = deepcopy(saved)
        class Store:
            def get(self, entity, key):
                if (entity, key) != ("forecast_previews", "saved"):raise AssertionError("unexpected read")
                return saved
            def put(self, *args):raise AssertionError("read projection must never overwrite cache")
        runner = OperationalMultiChainForecastRunner.__new__(OperationalMultiChainForecastRunner)
        runner.persistence = Store()
        job = {"objective": "Pedido", "requested_product_ids": [p["product_id"] for p in saved["products"]]}
        response = runner._render_result(job, [{"chain_id": saved["chain_id"], "preview_id": "saved"}], None)
        self.assertEqual(response["scopes"][0]["eligibility"]["ml_eligible"], 1)
        self.assertEqual(response["scopes"][0]["products"][1]["horizons"][0]["ml_value"], None)
        self.assertEqual(saved, original)
        single = runner._render_result(job, [{"chain_id": saved["chain_id"], "preview_id": "saved"}], "one")
        self.assertEqual(single["scopes"][0]["ml"]["retrospective_wape"], 12.5)
        self.assertEqual(saved, original)

    def test_new_result_uses_projection_without_changing_applied_engine_curve(self):
        saved = fixture()
        forecast = []
        for p in saved["products"][:2]:
            for row in p["horizons"]:
                forecast.append({**row, "product_id": p["product_id"], "statistical_model": "SYNTHETIC STAT", "probability": {}})
        result = {"forecast_towell": forecast, "aggregates": saved["aggregates"],
            "model_audit": {"product_candidates": saved["selection"]["product_candidates"]},
            "selection": {"applied_strategy": {"statistical_weight": .5}}}
        runner = OperationalMultiChainForecastRunner.__new__(OperationalMultiChainForecastRunner)
        runner.policy = ForecastPolicy(); runner.environment = "test"
        states = [{k:v for k,v in p.items() if k != "horizons"} for p in saved["products"]]
        out = runner._result("synthetic-chain", "2026-09", "2026-09", "synthetic-digest",
            {"objective": "Pedido", "mode": "RETROSPECTIVE_TRAINING", "creator_id": "synthetic"}, states, {}, result)
        self.assertEqual(out["policy"], asdict(ForecastPolicy()))
        self.assertEqual(out["eligibility"]["ml_eligible"], 1)
        self.assertEqual(out["products"][0]["horizons"][0]["ml_value"], 51)
        self.assertEqual(out["products"][1]["horizons"][0]["ml_value"], None)
        self.assertEqual(out["products"][1]["horizons"][0]["ensemble_ml_component"], 401)
        self.assertEqual(out["products"][1]["horizons"][0]["forecast_towell"], 301)


if __name__ == "__main__":
    unittest.main()
