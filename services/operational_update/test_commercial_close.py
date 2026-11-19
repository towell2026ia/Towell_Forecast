"""Synthetic December context, observed-only evaluation and ML target isolation."""

from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from services.forecast_engine.engine import (
    ForecastPolicy,
    add_month,
    normalize_retrospective_dataset,
)
from services.forecast_engine.retrospective import forecast_chain
from services.ml_engine.engine import LinearGlobal
from services.statistical_engine.engine import MODELS
from services.operational_update.planning_engine import (
    run_planning,
    scenario_products,
    _candidate,
)
from services.operational_update.test_planning_engine import fixture


def scenario():
    rows, metadata, context = fixture()
    for row in rows:
        row["basis"] = "OBSERVED"
    for i, meta in enumerate(metadata):
        for j in range(3):
            rows.append(
                {
                    **next(r for r in rows if r["product_id"] == meta["product_id"]),
                    "period": add_month("2026-10", j),
                    "value": str(250 + i * 40 + j * 20),
                    "basis": "COMMERCIAL_CLOSE",
                    "source_reference": f"Base_2024_2026!R{200 + i * 3 + j}",
                    "source_id": f"SCENARIO_{i}_{j}",
                }
            )
    context.update(
        training_cutoff="2026-12",
        model_cutoff="2026-12",
        observed_cutoff="2026-09",
        planning_basis="OBSERVED_AND_COMMERCIAL_CLOSE",
        scenario_hash="c" * 64,
        generation=6,
    )
    return rows, metadata, context


class CommercialCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.metadata, cls.context = scenario()
        stat = {"Media móvil 3": MODELS["Media móvil 3"]}
        with (
            patch.dict(
                "services.forecast_engine.retrospective.stat.MODELS", stat, clear=True
            ),
            patch.dict(
                "services.forecast_engine.retrospective.MODEL_FACTORIES",
                {LinearGlobal.name: LinearGlobal},
                clear=True,
            ),
        ):
            cls.observed = [deepcopy(r) for r in cls.rows if r["basis"] == "OBSERVED"]
            cls.result = run_planning(
                cls.rows, cls.metadata, cls.context, observed_records=cls.observed
            )
            changed = deepcopy(cls.rows)
            for row in changed:
                if row["basis"] == "COMMERCIAL_CLOSE":
                    row["value"] = str(Decimal(row["value"]) + 500)
            cls.changed = run_planning(
                changed, cls.metadata, cls.context, observed_records=cls.observed
            )

    def test_december_context_produces_january_h1_and_december_h12(self):
        self.assertEqual(self.result["cutoff"], "2026-12")
        self.assertEqual(self.result["observed_cutoff"], "2026-09")
        for product in self.result["products"]:
            for candidate in product["candidates"].values():
                self.assertEqual(
                    [h["target_period"] for h in candidate["horizons"]],
                    [f"2027-{m:02}" for m in range(1, 13)],
                )
                self.assertEqual(
                    [h["forecast_distance"] for h in candidate["horizons"]],
                    list(range(1, 13)),
                )
        self.assertFalse(self.result["champion_published"])

    def test_commercial_plan_changes_effective_stat_and_ml_features_with_fixed_methods(
        self,
    ):
        a, b = self.result["products"][0], self.changed["products"][0]
        self.assertEqual(a["candidates"]["STATISTICAL"]["model"], "Media móvil 3")
        self.assertEqual(a["candidates"]["ML"]["model"], LinearGlobal.name)
        self.assertNotEqual(
            a["candidates"]["STATISTICAL"]["horizons"][0]["value"],
            b["candidates"]["STATISTICAL"]["horizons"][0]["value"],
        )
        self.assertNotEqual(
            a["candidates"]["ML"]["horizons"][0]["value"],
            b["candidates"]["ML"]["horizons"][0]["value"],
        )
        # Feature order is existing month/quarter/sin/cos/horizon/history then lags.
        features = a["input_snapshot"]["ml_prediction_features"][0]["values"]
        self.assertEqual(features[6:9], [290.0, 270.0, 250.0])
        self.assertEqual(a["input_snapshot"]["ml_target_basis"], "OBSERVED_ONLY")
        self.assertEqual(a["input_snapshot"]["ml_feature_origin"], "2026-12")

    def test_plan_never_enters_targets_validation_or_changes_comparable_metrics(self):
        self.assertEqual(
            self.result["observed_training_fingerprint"],
            self.changed["observed_training_fingerprint"],
        )
        for a, b in zip(self.result["products"], self.changed["products"]):
            for family in a["candidates"]:
                self.assertEqual(
                    a["candidates"][family]["metrics"],
                    b["candidates"][family]["metrics"],
                )
        for audit in self.result["audits"]:
            self.assertEqual(audit["model_audit"]["observed_cutoff"], "2026-09")
            self.assertEqual(audit["model_audit"]["ml_target_basis"], "OBSERVED_ONLY")
            for fold in audit["model_audit"]["folds"]:
                self.assertLess(fold["train_target_max"], fold["test_target_min"])
                self.assertLessEqual(fold["train_target_max"], "2026-09")

    def test_model_values_are_exact_sums_of_current_variant_curves(self):
        for model in self.result["models"]:
            for family, candidate in model["candidates"].items():
                for i, h in enumerate(candidate["horizons"]):
                    values = [
                        p["candidates"][family]["horizons"][i]["value"]
                        for p in self.result["products"]
                    ]
                    self.assertEqual(
                        Decimal(h["value"]),
                        sum((Decimal(v) for v in values if v is not None), Decimal(0)),
                    )

    def test_partial_october_cannot_be_disguised_as_observed(self):
        changed = deepcopy(self.rows)
        next(r for r in changed if r["period"] == "2026-10")["basis"] = "OBSERVED"
        with self.assertRaisesRegex(ValueError, "UNACCREDITED"):
            run_planning(
                changed, self.metadata, self.context, observed_records=self.observed
            )

    def test_reference_before_october_is_context_but_cannot_replace_an_observed_key(
        self,
    ):
        changed = deepcopy(self.rows)
        row = next(r for r in changed if r["period"] == "2026-05")
        row["basis"] = "COMMERCIAL_CLOSE"
        with self.assertRaisesRegex(ValueError, "CANNOT_REPLACE_OBSERVED"):
            run_planning(
                changed, self.metadata, self.context, observed_records=self.observed
            )
        observed = [
            r
            for r in self.observed
            if (r["product_id"], r["period"]) != (row["product_id"], row["period"])
        ]
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            result = run_planning(
                changed, self.metadata, self.context, observed_records=observed
            )
        self.assertEqual(
            result["observed_training_fingerprint"],
            __import__(
                "services.operational_update.planning_engine", fromlist=["digest"]
            ).digest(observed),
        )
        self.assertEqual(result["observed_cutoff"], "2026-09")

    def test_no_real_history_has_explicit_commercial_base_and_no_fake_metrics(self):
        rows = [deepcopy(r) for r in self.rows if r["basis"] == "COMMERCIAL_CLOSE"]
        for r in rows:
            r["value"] = "0"
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            result = run_planning(
                rows, self.metadata, self.context, observed_records=[]
            )
        for product in result["products"]:
            stat = product["candidates"]["STATISTICAL"]
            self.assertTrue(stat["commercial_base_only"])
            self.assertTrue(stat["calculable"])
            self.assertTrue(all(Decimal(h["value"]) == 0 for h in stat["horizons"]))
            self.assertIsNone(stat["metrics"]["wape"])
            self.assertFalse(stat["eligible"])


class CurrentMethodEligibilityTests(unittest.TestCase):
    def test_no_calculable_method_is_missing_not_a_hidden_naive_or_zero(self):
        rows, metadata, context = scenario()
        current = scenario_products(rows)
        observed = normalize_retrospective_dataset(
            [r for r in rows if r["basis"] == "OBSERVED"], "2026-09", objective="Pedido"
        )

        def invalid_model(history, steps):
            raise ValueError("synthetic_current_model_failure")

        with (
            patch.dict(
                "services.forecast_engine.retrospective.stat.MODELS",
                {"Naive": invalid_model},
                clear=True,
            ),
            patch.dict(
                "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
            ),
        ):
            result = forecast_chain(
                current,
                "2026-12",
                ForecastPolicy(),
                None,
                evaluation_products=observed,
                observed_cutoff="2026-09",
            )
        self.assertEqual(result["forecast_towell"], [])
        self.assertEqual(len(result["model_audit"]["unavailable_products"]), 2)
        self.assertTrue(
            all(
                p["reason"] == "NO_CALCULABLE_CURRENT_METHOD"
                for p in result["model_audit"]["unavailable_products"]
            )
        )

    def test_ensemble_metrics_compare_its_saved_component_not_the_individual_ml_winner(
        self,
    ):
        def curve(value, component=None):
            return [
                {
                    "horizon": h,
                    "target_period": add_month("2026-12", h),
                    "value": value,
                    "ml_component": component,
                }
                for h in range(1, 13)
            ]

        raw = {
            "model_audit": {
                "product_candidates": [
                    {
                        "family": "ensemble",
                        "product_id": "SYNTHETIC",
                        "model": "weighted",
                        "statistical_weight": 0.75,
                        "ensemble_ml_model": "SCOPE_ML",
                        "horizons": curve(87.5, 50),
                    },
                    {
                        "family": "ml",
                        "product_id": "SYNTHETIC",
                        "model": "OWN_ML",
                        "horizons": curve(200),
                    },
                ]
            }
        }
        forecast = [
            {
                "chain_id": "SYNTHETIC_CHAIN",
                "category": "SYNTHETIC_CATEGORY",
                "product_id": "SYNTHETIC",
                "horizon": h,
                "target_period": add_month("2026-12", h),
                "classification": "Regular",
                "statistical": 100,
                "statistical_model": "STAT",
                "ml": 50,
                "ml_model": "SCOPE_ML",
                "forecast_towell": 87.5,
                "model_strategy": "ensemble",
            }
            for h in range(1, 13)
        ]
        pairs = [
            {
                "product_id": "SYNTHETIC",
                "origin": "2025-07",
                "target_period": add_month("2025-07", h),
                "horizon": h,
                "actual": 100,
                "selected_statistical_model": "STAT",
                "selected_ml_model": "OWN_ML",
                "statistical": {"STAT": 100},
                "ml": {"OWN_ML": 200, "SCOPE_ML": 50},
                "ensemble_ml_model": "SCOPE_ML",
                "ensemble_statistical_weight": 0.75,
            }
            for h in range(1, 13)
        ]
        ensemble = _candidate(
            raw,
            "ensemble",
            "SYNTHETIC",
            forecast,
            pairs,
            distances=tuple(range(1, 13)),
            cutoff="2026-12",
        )
        individual = _candidate(
            raw,
            "ml",
            "SYNTHETIC",
            forecast,
            pairs,
            distances=tuple(range(1, 13)),
            cutoff="2026-12",
        )
        self.assertEqual(ensemble["metrics"]["wape"], 12.5)
        self.assertEqual(individual["metrics"]["wape"], 100)
        self.assertTrue(
            all(
                p["value"] == "87.5"
                and p["ensemble_ml_component"] == "50"
                and p["ensemble_ml_model"] == "SCOPE_ML"
                and p["statistical_weight"] == 0.75
                for p in ensemble["horizons"]
            )
        )

    def test_historical_seasonal_score_cannot_win_a_three_month_current_tail(self):
        rows, metadata, context = scenario()
        rows = [
            r
            for r in rows
            if r["product_id"] == metadata[0]["product_id"]
            and (r["period"] <= "2025-12" or r["basis"] == "COMMERCIAL_CLOSE")
        ]
        for i, r in enumerate(rows):
            if r["basis"] == "OBSERVED":
                r["value"] = str(100 + (i % 12) * 20)
        current = scenario_products(rows)
        observed = normalize_retrospective_dataset(
            [r for r in rows if r["basis"] == "OBSERVED"], "2026-09", objective="Pedido"
        )
        names = {k: MODELS[k] for k in ("Naive estacional", "Media móvil 3", "Naive")}
        with (
            patch.dict(
                "services.forecast_engine.retrospective.stat.MODELS", names, clear=True
            ),
            patch.dict(
                "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
            ),
        ):
            result = forecast_chain(
                current,
                "2026-12",
                ForecastPolicy(),
                None,
                evaluation_products=observed,
                observed_cutoff="2026-09",
            )
        selected = {r["statistical_model"] for r in result["forecast_towell"]}
        self.assertNotIn("Naive estacional", selected)
        seasonal = next(
            c
            for c in result["model_audit"]["candidate_models"]
            if c["model"] == "Naive estacional"
        )
        self.assertFalse(seasonal["calculable"])
        self.assertEqual(seasonal["reason"], "REQUIRES_12_PERIODS")
        self.assertEqual(len(result["forecast_towell"]), 12)


if __name__ == "__main__":
    unittest.main()
