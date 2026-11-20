from __future__ import annotations

import copy
from decimal import Decimal
import unittest
from unittest.mock import patch

from services.assistant_api.cold_start import launch_forecast
from services.forecast_engine.engine import add_month
from services.operational_update.rolling_operation import (
    calculate_rolling_revision,
    _scope_comparison,
)


def corpus():
    metadata = {
        f"P{i}": {
            "product_id": f"P{i}",
            "chain_id": "CHAIN",
            "model": "MODEL",
            "presentation": "SIZE",
            "format": "FORMAT",
            "account": "ACCOUNT",
            "color": str(i),
            "category_id": "CATEGORY",
            "product_type": "CATEGORY",
            "status": "ACTIVE",
        }
        for i in range(7)
    }
    rows = []
    for i in range(6):
        for month in range(38):
            value = (
                0
                if i == 4
                else (
                    0
                    if i == 5 and month >= 32
                    else (0 if month % 3 else 1)
                    if i == 3
                    else 10 + i * 2 + month % 12
                )
            )
            rows.append(
                {
                    "product_id": f"P{i}",
                    "chain_id": "CHAIN",
                    "category": "CATEGORY",
                    "description": f"MODEL SIZE COLOR {i}",
                    "product_code": f"SYNTHETIC_{i}",
                    "period": add_month("2024-01", month),
                    "value": str(value),
                    "objective": "Pedido",
                    "metric_code": "ORDER",
                    "basis": "OBSERVED",
                    "source_file_sha256": "a" * 64,
                    "source_reference": f"Base!H{i * 38 + month + 2}",
                    "source_id": f"SOURCE_{i}_{month}",
                    "evidence_mode": "RETROSPECTIVE_TRAINING",
                }
            )
    context = {
        "objective": "Pedido",
        "target_metric": "ORDER",
        "target_file_sha256": "a" * 64,
        "source_hash": "b" * 64,
        "generation": 7,
        "training_cutoff": "2027-02",
        "forecast_available": True,
        "observed_cutoff": "2027-02",
        "planning_start": "2027-01",
        "planning_end": "2027-12",
    }
    return rows, metadata, context


class RollingOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.metadata, cls.context = corpus()
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            cls.result = calculate_rolling_revision(cls.rows, cls.metadata, cls.context)

    def test_cross_year_twelve_month_run_keeps_original_pin_and_new_id(self):
        result = self.result
        self.assertEqual(result["source_context"], self.context)
        self.assertEqual(
            (result["window_start"], result["window_end"]), ("2027-03", "2028-02")
        )
        for product in result["products"]:
            points = product["candidates"]["STATISTICAL"]["horizons"]
            self.assertEqual(
                [p["target_period"] for p in points],
                [add_month("2027-02", h) for h in range(1, 13)],
            )
        self.assertFalse(result["champion_published"])
        self.assertFalse(result["automatic_adoption"])

    def test_low_intermittent_zero_and_six_zero_products_remain_active_and_calculable(
        self,
    ):
        products = {p["product_id"]: p for p in self.result["products"]}
        for pid in ("P3", "P4", "P5"):
            self.assertEqual(products[pid]["commercial_status"], "ACTIVE")
            self.assertTrue(products[pid]["candidates"]["STATISTICAL"]["calculable"])
        zero = products["P4"]["candidates"]["STATISTICAL"]
        self.assertTrue(all(Decimal(p["value"]) == 0 for p in zero["horizons"]))
        self.assertIn(
            zero["model"], ("Naive", "Media móvil 3", "SES", "Croston", "SBA", "TSB")
        )

    def test_no_own_history_reaches_comparables_in_individual_and_group_scope(self):
        group = next(p for p in self.result["products"] if p["product_id"] == "P6")
        cold = group["candidates"]["COLD_START"]
        self.assertTrue(cold["calculable"])
        self.assertEqual(len(cold["horizons"]), 12)
        self.assertIsNone(cold["target_wape"])
        self.assertEqual(cold["admission"]["status"], "REQUIRES_EXPLICIT_ACCEPTANCE")
        self.assertEqual(cold["incremental_effect"]["status"], "UNDETERMINED")
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            single = calculate_rolling_revision(
                self.rows, self.metadata, self.context, requested_product_ids=["P6"]
            )
        self.assertEqual(len(single["products"]), 1)
        self.assertEqual(single["products"][0]["candidates"]["COLD_START"], cold)
        self.assertGreater(len(single["audits"][0]["comparable_product_ids"]), 1)

    def test_cold_start_is_not_duplicated_as_three_engines_or_added_silently(self):
        missing = next(p for p in self.result["products"] if p["product_id"] == "P6")
        for family in ("STATISTICAL", "ML", "ENSEMBLE"):
            self.assertTrue(
                all(
                    h["value"] is None
                    for h in missing["candidates"][family]["horizons"]
                )
            )
        group = self.result["models"][0]["candidates"]["STATISTICAL"]["horizons"][0]
        values = [
            p["candidates"]["STATISTICAL"]["horizons"][0]["value"]
            for p in self.result["products"]
        ]
        self.assertEqual(
            Decimal(group["value"]),
            sum((Decimal(v) for v in values if v is not None), Decimal(0)),
        )
        self.assertEqual(
            (group["covered_variants"], group["expected_variants"]), (6, 7)
        )

    def test_individual_ml_not_borrowed_from_ensemble_and_zero_denominator_not_certified(
        self,
    ):
        for product in self.result["products"]:
            self.assertTrue(
                all(p["value"] is None for p in product["candidates"]["ML"]["horizons"])
            )
        zero = next(p for p in self.result["products"] if p["product_id"] == "P4")
        self.assertIsNone(zero["candidates"]["STATISTICAL"]["metrics"]["wape"])
        self.assertFalse(zero["candidates"]["STATISTICAL"]["eligible"])

    def test_commercial_input_never_used_as_actual_or_ml_target(self):
        rows, metadata, context = corpus()
        observed = [r for r in rows if r["period"] <= "2026-11"]
        scenario = [
            {**r, "basis": "COMMERCIAL_CLOSE"} if r["period"] > "2026-11" else r
            for r in rows
        ]
        context.update(observed_cutoff="2026-11", scenario_hash="c" * 64)
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            result = calculate_rolling_revision(
                scenario, metadata, context, observed_records=observed
            )
        self.assertTrue(
            all(
                pair["target_period"] <= "2026-11" for pair in result["paired_evidence"]
            )
        )
        cold = next(p for p in result["products"] if p["product_id"] == "P6")[
            "candidates"
        ]["COLD_START"]
        self.assertTrue(
            all(p["target_period"] <= "2026-11" for p in cold["peer_validation_pairs"])
        )

    def test_foreign_metric_duplicate_and_requested_identity_fail_closed(self):
        for field, value in (
            ("objective", "Venta"),
            ("metric_code", "INVENTORY"),
            ("source_file_sha256", "f" * 64),
        ):
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                calculate_rolling_revision(rows, self.metadata, self.context)
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            calculate_rolling_revision(
                [*self.rows, self.rows[0]], self.metadata, self.context
            )
        with self.assertRaisesRegex(ValueError, "UNAUTHORIZED"):
            calculate_rolling_revision(
                self.rows,
                self.metadata,
                self.context,
                requested_product_ids=["FOREIGN"],
            )
        with self.assertRaisesRegex(ValueError, "UNACCREDITED"):
            calculate_rolling_revision(
                self.rows, self.metadata, {**self.context, "forecast_available": False}
            )

    def test_peer_scope_does_not_cross_chain_model_or_presentation(self):
        metadata = copy.deepcopy(self.metadata)
        metadata["P6"]["presentation"] = "OTHER"
        with patch.dict(
            "services.forecast_engine.retrospective.MODEL_FACTORIES", {}, clear=True
        ):
            result = calculate_rolling_revision(
                self.rows, metadata, self.context, requested_product_ids=["P6"]
            )
        self.assertFalse(
            result["products"][0]["candidates"]["COLD_START"]["calculable"]
        )

    def test_pedido_normalizer_is_explicit_and_dr_offline_never_changes_quantities(
        self,
    ):
        target = {**self.metadata["P6"], "id": "P6"}
        result = launch_forecast(self.rows, "2027-02", target, objective="Pedido")
        self.assertIsNotNone(result)
        self.assertEqual(result["objective"], "Pedido")
        self.assertEqual(result["research"], {"status": "DISABLED"})
        self.assertEqual(
            result,
            launch_forecast(
                list(reversed(self.rows)), "2027-02", target, objective="Pedido"
            ),
        )

    def test_scope_metrics_use_identical_pairs_not_mean_product_percentages(self):
        products = [
            {
                "product_id": pid,
                "candidates": {
                    "ENSEMBLE": {
                        "horizons": [
                            {
                                "horizon": 1,
                                "statistical_weight": 0.5,
                                "ensemble_ml_model": "GLOBAL",
                            }
                        ]
                    }
                },
            }
            for pid in ("A", "B")
        ]
        pairs = [
            {
                "product_id": pid,
                "chain_id": "CHAIN",
                "origin": "2025-01",
                "target_period": "2025-02",
                "horizon": 1,
                "actual": actual,
                "statistical": {"S": stat},
                "ml": {"M": ml, "GLOBAL": ml},
                "selected_statistical_model": "S",
                "selected_ml_model": "M",
            }
            for pid, actual, stat, ml in (("A", 100, 50, 100), ("B", 1, 1, 2))
        ]
        result = _scope_comparison(products, pairs)
        self.assertEqual(result["metrics"]["STATISTICAL"]["wape"], 49.505)
        self.assertEqual(result["scope_leader"], "ML")
        missing = {"product_id": "MISSING", "candidates": {}}
        partial = _scope_comparison([*products, missing], pairs)
        self.assertIsNone(partial["scope_leader"])
        self.assertEqual(partial["missing_product_ids"], ["MISSING"])


if __name__ == "__main__":
    unittest.main()
