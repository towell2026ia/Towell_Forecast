from __future__ import annotations

import copy
from decimal import Decimal
import unittest
from unittest.mock import patch

from services.forecast_engine.engine import (
    HORIZONS, ForecastPolicy, _training_samples, add_month, normalize_retrospective_dataset,
)
from services.forecast_engine.retrospective import forecast_chain, identities_at
from services.operational_update.planning_engine import (
    DISTANCES, MASTER_SHA256, _bridge, _candidate, aggregate_models, evaluation_origins, run_planning, validate_input,
)


def fixture():
    metadata = [{"chain_id": "SYNTHETIC_CHAIN", "product_id": f"SYNTHETIC_{i}",
                 "account": "ACCOUNT", "format": "FORMAT", "upc": f"000{i}",
                 "identity_key": f"ACCOUNT|FORMAT|000{i}", "model": "MODEL",
                 "presentation": "MEDIO BAÑO", "color": f"COLOR_{i}", "commercial_code": "CODE"}
                for i in range(2)]
    rows = [{"chain_id": p["chain_id"], "product_id": p["product_id"],
             "product_code": p["upc"], "category": "TOALLA", "variant": p["color"],
             "period": add_month("2024-01", j), "value": str(100 + i * 40 + j * 2 + (j % 12) * 3),
             "objective": "Pedido", "metric_code": "ORDER", "training_eligible": True,
             "target_sheet": "Base_2024_2026", "source_file_sha256": MASTER_SHA256,
             "source_reference": f"Base_2024_2026!H{2 + i * 33 + j}",
             "source_id": f"SYNTHETIC_OBS_{i}_{j}", "evidence_mode": "RETROSPECTIVE_TRAINING"}
            for i, p in enumerate(metadata) for j in range(33)]
    context = {"forecast_contract": "pedido-unificado-v1", "target_file_sha256": MASTER_SHA256,
               "target_sheet": "Base_2024_2026", "target_metric": "ORDER", "objective": "Pedido",
               "training_cutoff": "2026-09", "planning_start": "2027-01", "planning_end": "2027-12",
               "source_hash": "a" * 64, "generation": 5}
    return rows, metadata, context


class PlanningCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.metadata, cls.context = fixture()
        cls.planning = run_planning(cls.rows, cls.metadata, cls.context)

    def test_real_engine_plan_distances_and_bridge_are_literal(self):
        self.assertEqual(HORIZONS, tuple(range(1, 13)))
        for product in self.planning["products"]:
            self.assertEqual([p["target_period"] for p in product["bridge_horizons"]],
                             ["2026-10", "2026-11", "2026-12"])
            for candidate in product["candidates"].values():
                self.assertEqual([p["horizon"] for p in candidate["horizons"]], list(range(1, 13)))
                self.assertEqual([p["forecast_distance"] for p in candidate["horizons"]], list(range(4, 16)))
                self.assertEqual([p["target_period"] for p in candidate["horizons"]],
                                 [f"2027-{m:02}" for m in range(1, 13)])
        self.assertFalse(self.planning["champion_published"])

    def test_global_ml_is_original_chain_scope_and_extended_training_in_domain(self):
        products = normalize_retrospective_dataset(self.rows, "2026-09", objective="Pedido")
        x, _ = _training_samples(products, "2026-09", identities_at(products, "2026-09"), horizons=DISTANCES)
        self.assertEqual(max(row[4] for row in x), 15)
        old_x, _ = _training_samples(products, "2026-09", identities_at(products, "2026-09"))
        self.assertEqual(max(row[4] for row in old_x), 12)
        audit = self.planning["audits"][0]
        self.assertEqual(tuple(audit["evaluation_origins"]), ("2025-04", "2025-05", "2025-06"))
        self.assertEqual(len(audit["model_audit"]["ml_candidates"]), 3)
        for fold in audit["model_audit"]["folds"]:
            self.assertLess(fold["train_target_max"], fold["test_target_min"])

    def test_model_is_decimal_sum_of_variants_for_every_family_and_month(self):
        model = self.planning["models"][0]
        for family, candidate in model["candidates"].items():
            for i, point in enumerate(candidate["horizons"]):
                values = [p["candidates"][family]["horizons"][i]["value"] for p in self.planning["products"]]
                expected = sum((Decimal(v) for v in values if v is not None), Decimal(0))
                self.assertEqual(Decimal(point["value"]), expected)
                self.assertEqual(point["expected_variants"], 2)

    def test_uncertified_or_foreign_training_source_cannot_enter(self):
        for key, value in (("metric_code", "SALES"), ("source_file_sha256", "b" * 64),
                           ("training_eligible", False), ("period", "2026-10"),
                           ("target_sheet", "Plan2026")):
            rows = copy.deepcopy(self.rows)
            rows[0][key] = value
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "UNACCREDITED"):
                validate_input(rows, self.context)

    def test_optional_stock_and_commercial_plan_cannot_influence_engine(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["inventory"] = "999999999"
            row["commercial_plan_2026"] = "888888888"
            row["retired_forecast"] = "777777777"
        changed = run_planning(rows, self.metadata, self.context)
        for base, other in zip(self.planning["products"], changed["products"]):
            self.assertEqual(base["candidates"], other["candidates"])
            self.assertEqual(base["bridge_horizons"], other["bridge_horizons"])

    def test_calendar_origin_choice_is_quantity_independent(self):
        self.assertEqual(evaluation_origins(), ("2025-04", "2025-05", "2025-06"))
        self.assertEqual(self.planning["evaluation_origins"], list(evaluation_origins()))

    def test_missing_ml_does_not_suppress_statistical_or_statistical_only_ensemble(self):
        # Fit actual statistical methods; only simulate unavailable ML factories.
        with patch.dict("services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True):
            result = run_planning(self.rows, self.metadata, self.context)
        for product in result["products"]:
            stat, ml, ensemble = (product["candidates"][f] for f in ("STATISTICAL", "ML", "ENSEMBLE"))
            self.assertTrue(stat["eligible"])
            self.assertTrue(ensemble["eligible"])
            self.assertEqual(ensemble["model"], "Estadístico solo")
            self.assertEqual([p["value"] for p in stat["horizons"]], [p["value"] for p in ensemble["horizons"]])
            self.assertTrue(all(p["statistical_weight"] == 1 and p["ml_weight"] == 0 for p in ensemble["horizons"]))
            self.assertTrue(all(p["ensemble_ml_component"] is None for p in ensemble["horizons"]))
            self.assertFalse(ml["eligible"])
            self.assertTrue(all(p["value"] is None for p in ml["horizons"]))

    def test_missing_variant_remains_expected_and_model_metrics_require_identical_members(self):
        metadata = copy.deepcopy(self.metadata)
        metadata.append({**metadata[0], "product_id": "SYNTHETIC_MISSING", "upc": "0009", "color": "MISSING"})
        result = run_planning(self.rows, metadata, self.context)
        model = result["models"][0]["candidates"]["STATISTICAL"]
        self.assertEqual(model["horizons"][0]["expected_variants"], 3)
        self.assertEqual(model["horizons"][0]["covered_variants"], 2)
        self.assertEqual(model["horizons"][0]["status"], "TOTAL_PARCIAL")
        self.assertIsNone(model["metrics"]["wape"])
        self.assertGreater(model["micro_observations"], 0)


class LegacyDefaultCalendarTests(unittest.TestCase):
    def test_existing_quantities_survive_missing_evaluation_without_champion_eligibility(self):
        raw = {"model_audit": {"product_candidates": []}}
        forecast = [{"horizon": h, "target_period": add_month("2026-09", h),
                     "statistical": 123.45, "statistical_model": "Naive", "ml": 234.56,
                     "ml_model": "Regresión Lineal Global", "forecast_towell": 123.45,
                     "classification": "Regular", "model_strategy": "statistical"}
                    for h in range(1, 16)]
        for family, expected in (("statistical", "123.45"), ("ml", "234.56"), ("ensemble", "123.45")):
            candidate = _candidate(raw, family, "P", forecast, [])
            self.assertFalse(candidate["eligible"])
            self.assertTrue(all(p["value"] == expected for p in candidate["horizons"]))
            self.assertTrue(all(p["status"] == "PROVISIONAL_WITHOUT_EVALUATION" for p in candidate["horizons"]))

    def test_unfitted_empty_history_default_zero_is_not_a_computed_candidate(self):
        raw = {"model_audit": {"product_candidates": []}}
        forecast = [{"horizon": h, "target_period": add_month("2026-09", h),
                     "statistical": 0, "statistical_model": "Naive", "ml": None,
                     "forecast_towell": 0, "classification": "Sin historial", "model_strategy": "statistical"}
                    for h in range(1, 16)]
        candidate = _candidate(raw, "statistical", "P", forecast, [])
        self.assertTrue(all(p["value"] is None for p in candidate["horizons"]))
        self.assertTrue(all(p["reason"] == "NO_CURRENT_CONTIGUOUS_HISTORY" for p in candidate["horizons"]))

    def test_bridge_retains_independent_ml_and_ensemble_component_separately(self):
        raw = {"model_audit": {"product_candidates": [
            {"family": "ml", "product_id": "P", "model": "INDEPENDENT_ML",
             "horizons": [{"horizon": 1, "value": 99}]},
            {"family": "ensemble", "product_id": "P", "model": "weighted",
             "statistical_weight": .5, "ensemble_ml_model": "SCOPE_COMPONENT_ML",
             "horizons": [{"horizon": 1, "value": 63.5, "ml_component": 77}]},
        ]}}
        forecast = [{"horizon": 1, "target_period": "2026-10", "statistical": 50,
                     "ml": 77, "forecast_towell": 50, "model_strategy": "statistical"}]
        point = _bridge(raw, "P", forecast)[0]
        self.assertEqual(point["ml_value"], "99")
        self.assertEqual(point["ensemble_ml_component"], "77")
        self.assertEqual(point["ensemble_value"], "63.5")
        self.assertEqual(point["ml_model"], "INDEPENDENT_ML")
        self.assertEqual(point["ensemble_ml_model"], "SCOPE_COMPONENT_ML")

    def test_explicit_old_calendar_is_identical_to_preexisting_call_contract(self):
        rows, _, _ = fixture()
        products = normalize_retrospective_dataset(rows[:10], "2024-10", objective="Pedido")
        default = forecast_chain(products, "2024-10", ForecastPolicy(), None)
        explicit = forecast_chain(products, "2024-10", ForecastPolicy(), None,
                                  horizons=HORIZONS, evaluation_horizons=(1, 3, 6, 12), max_folds=12)
        self.assertEqual(default, explicit)

    def test_invalid_calendar_rejected(self):
        rows, _, _ = fixture()
        products = normalize_retrospective_dataset(rows[:10], "2024-10", objective="Pedido")
        with self.assertRaisesRegex(ValueError, "invalid_forecast_calendar"):
            forecast_chain(products, "2024-10", ForecastPolicy(), None, horizons=(0, 1))


if __name__ == "__main__":
    unittest.main()
