from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.data_provider import DataProvider
from services.assistant_api.runner import GeneralizedMonthlyForecastRunner
from services.assistant_api.api import create_app
from services.assistant_api.settings import Settings
from services.forecast_engine import ChampionRegistry, forecast_dataset, normalize_dataset
from services.forecast_engine.engine import _metrics, _training_samples, add_month, month_end


def synthetic_rows(months: int = 40) -> list[dict]:
    rows = []
    definitions = (("CHAIN-A", "REGULAR", "TEXTIL", lambda index: 100 + index),
                   ("CHAIN-A", "INTERMITTENT", "TEXTIL", lambda index: 70 if index % 5 == 0 else 0),
                   ("CHAIN-B", "SEASONAL", "HOME", lambda index: 100 + 30 * (index % 12 == 11)),
                   ("CHAIN-B", "NEW", "HOME", lambda index: 25 if index >= months - 4 else 0))
    for chain, product, category, demand in definitions:
        for index in range(months):
            period = add_month("2021-01", index)
            rows.append({"chain_id": chain, "product_id": product, "product_code": product,
                         "description": f"Fixture {product}", "category": category,
                         "variant": "", "period": period, "objective": "Venta",
                         "value": demand(index), "is_missing": False,
                         "available_at": month_end(period)})
    return rows


class GenericEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = synthetic_rows()
        cls.result = forecast_dataset(cls.rows, "2024-04",
                                      incumbent={"strategy": "ml", "statistical_weight": 0.0,
                                                 "version": "PREVIOUS-ML", "certified_wape": 15.0})

    def test_multichain_twelve_horizons_and_reconciliation(self):
        self.assertEqual({row["chain_id"] for row in self.result["chains"]}, {"CHAIN-A", "CHAIN-B"})
        for chain in self.result["chains"]:
            products = chain["forecast_towell"]
            self.assertEqual(len(products), 24)
            self.assertEqual({row["horizon"] for row in products}, set(range(1, 13)))
            for horizon in range(1, 13):
                product_total = round(sum(row["forecast_towell"] for row in products
                                          if row["horizon"] == horizon), 2)
                chain_total = next(row["forecast_towell"] for row in chain["aggregates"]
                                   if row["level"] == "chain" and row["horizon"] == horizon)
                category_total = sum(row["forecast_towell"] for row in chain["aggregates"]
                                     if row["level"] == "category" and row["horizon"] == horizon)
                self.assertEqual(product_total, chain_total)
                self.assertEqual(product_total, round(category_total, 2))
            self.assertEqual(len(chain["by_horizon"]), 12)
            self.assertEqual(chain["selection"]["incumbent"]["strategy"], "ml")
            self.assertFalse(chain["selection"]["automatic_promotion"])
            self.assertEqual(chain["model_audit"]["origins"]["certification"], 3)
        new = [row for row in self.result["chains"][1]["forecast_towell"]
               if row["product_id"] == "NEW"]
        self.assertEqual(len(new), 12)
        self.assertTrue(all(row["forecast_status"] == "COLD_START" for row in new))
        self.assertTrue(all(row["certification_status"] == "PROVISIONAL" for row in new))
        for chain in self.result["chains"]:
            for row in chain["forecast_towell"]:
                band = row["probability"]
                self.assertIsNotNone(band)
                self.assertLessEqual(band["p10"], band["p50"])
                self.assertLessEqual(band["p50"], band["p90"])
                self.assertLessEqual(band["p90"], band["p95"])
        self.assertTrue(all(row["band_basis"] == "category" for row in new))

    def test_prelaunch_zero_and_postlaunch_zero(self):
        products = normalize_dataset(self.rows, "2024-04")
        new = next(item for item in products if item.product_id == "NEW")
        self.assertEqual(len(new.history("2024-04")), 4)
        intermittent = next(item for item in products if item.product_id == "INTERMITTENT")
        self.assertEqual(intermittent.history("2021-02"), [70, 0])
        self.assertNotIn("NEW", [row["product_id"] for row in self.result["chains"][1]["by_product"]])

    def test_objectives_and_identity_are_not_inferred_from_descriptions(self):
        pedido = [{**row, "objective": "Pedido", "value": 999999.0} for row in self.rows]
        modified = [{**row, "description": "same display text"} for row in self.rows]
        with_order = forecast_dataset(self.rows + pedido, "2024-04")
        same_description = forecast_dataset(modified, "2024-04")
        baseline = forecast_dataset(self.rows, "2024-04")
        self.assertEqual(with_order, baseline)
        self.assertEqual([len(chain["forecast_towell"]) for chain in same_description["chains"]], [24, 24])
        self.assertEqual(forecast_dataset(pedido, "2024-04", objective="Pedido")["objective"], "Pedido")
        with self.assertRaisesRegex(ValueError, "unsupported_forecast_objective"):
            forecast_dataset(pedido, "2024-04", objective="Fcst Cliente")

    def test_feature_history_rejects_late_available_observation(self):
        altered = [{**row, "available_at": "2021-08-31"}
                   if row["chain_id"] == "CHAIN-A" and row["product_id"] == "REGULAR"
                   and row["period"] == "2021-03" else row for row in self.rows]
        series = normalize_dataset(altered, "2024-04")
        regular = next(item for item in series if item.product_id == "REGULAR")
        self.assertNotIn(102.0, regular.history("2021-05"))

    def test_future_data_and_research_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "data_leakage_detected"):
            forecast_dataset(self.rows + [{**self.rows[0], "period": "2024-05"}], "2024-04")
        with self.assertRaisesRegex(ValueError, "data_leakage_detected"):
            forecast_dataset([{**row, "available_at": "2024-05-01"} if index == 0 else row
                              for index, row in enumerate(self.rows)], "2024-04")
        with self.assertRaisesRegex(ValueError, "research_leakage_detected"):
            forecast_dataset(self.rows, "2024-04", research={"cutoff_date": "2024-04-30",
                                                            "sources": [{"published_at": "2024-05-01"}]})

    def test_certified_metric_formula_and_split(self):
        self.assertEqual(_metrics([(100, 80), (200, 250)])["wape"], 23.3333)
        self.assertEqual(_metrics([(100, 80), (200, 250)])["bias"], -10.0)
        for chain in self.result["chains"]:
            audit = chain["model_audit"]
            self.assertLess(audit["training_range"][1], audit["validation_range"][0])
            self.assertLess(audit["validation_range"][1], audit["certification_range"][0])
            self.assertTrue(set(audit["validation_targets"]).isdisjoint(audit["certification_targets"]))
            self.assertIsNotNone(audit["certified_wape"])

    def test_tuning_and_ml_training_are_isolated_from_later_actuals(self):
        changed = [{**row, "value": float(row["value"]) + 5000} if row["period"] > "2022-07"
                   else row for row in self.rows]
        original_series = normalize_dataset(self.rows, "2024-04", chain_id="CHAIN-A")
        changed_series = normalize_dataset(changed, "2024-04", chain_id="CHAIN-A")
        identities = sorted({value for item in original_series
                             for value in (item.chain_id, item.category, item.key)})
        original_x, original_y = _training_samples(original_series, "2022-07", identities)
        changed_x, changed_y = _training_samples(changed_series, "2022-07", identities)
        self.assertEqual(original_x, changed_x)
        self.assertEqual(original_y.tolist(), changed_y.tolist())

    def test_certification_actuals_do_not_change_selection(self):
        changed = [{**row, "value": float(row["value"]) + 5000}
                   if row["period"] in {"2024-02", "2024-03", "2024-04"} else row for row in self.rows]
        again = forecast_dataset(changed, "2024-04",
                                 incumbent={"strategy": "ml", "statistical_weight": 0.0,
                                            "version": "PREVIOUS-ML", "certified_wape": 15.0})
        for before, after in zip(self.result["chains"], again["chains"]):
            self.assertEqual(before["model_audit"]["statistical_parameters"],
                             after["model_audit"]["statistical_parameters"])
            self.assertEqual(before["model_audit"]["selected_candidate"]["statistical_weight"],
                             after["model_audit"]["selected_candidate"]["statistical_weight"])
            self.assertEqual(before["model_audit"]["validation_wape"],
                             after["model_audit"]["validation_wape"])

    def test_champion_is_persistent_and_requires_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalPersistenceProvider(Path(directory) / "state.sqlite3")
            registry = ChampionRegistry(storage)
            candidate = {"strategy": "ml", "statistical_weight": 0.0,
                         "version": "ML-1", "certification_status": "CERTIFIED", "certified_wape": 10.0}
            with self.assertRaises(PermissionError):
                registry.publish("CHAIN-A", "Venta", "chain", candidate, actor="manager")
            registry.publish("CHAIN-A", "Venta", "chain", candidate, actor="manager", authorized=True)
            restarted = ChampionRegistry(LocalPersistenceProvider(Path(directory) / "state.sqlite3"))
            self.assertEqual(restarted.current("CHAIN-A", "Venta", "chain")["strategy"], "ml")

    def test_generic_monthly_runner_reads_incumbent_and_persists_products(self):
        class SyntheticProvider(DataProvider):
            def __init__(self):
                self.months = 40

            @property
            def name(self):
                return "synthetic-test"

            def records(self):
                return synthetic_rows(self.months)

            def load(self, name):
                raise AssertionError("must_not_load_pilot_snapshot")

            def decisions(self):
                return []

            def vintages(self):
                return []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalPersistenceProvider(root / "state.sqlite3")
            registry = ChampionRegistry(storage)
            registry.publish("CHAIN-A", "Venta", "chain",
                             {"strategy": "ml", "statistical_weight": 0.0,
                              "version": "ML-PREVIOUS", "certification_status": "CERTIFIED",
                              "certified_wape": 15.0}, actor="manager", authorized=True)
            provider = SyntheticProvider()
            runner = GeneralizedMonthlyForecastRunner(provider, state_dir=root, persistence=storage)
            result = runner.run_month("2024-04")
            self.assertEqual(result["state"], "Completed", result["errors"])
            self.assertEqual(len(result["results"]["chains"]), 2)
            self.assertEqual(len(storage.list("forecast_horizons")), 48)
            self.assertEqual(registry.current("CHAIN-A", "Venta", "chain")["version"], "ML-PREVIOUS")
            self.assertEqual(result["results"]["chains"][0]["selection"]["incumbent"]["strategy"], "ml")
            provider.months = 41
            following = runner.run_month("2024-05")
            self.assertEqual(following["state"], "Completed", following["errors"])
            self.assertEqual(following["results"]["chains"][0]["selection"]["incumbent"]["version"],
                             "ML-PREVIOUS")

    def test_same_input_is_reproducible(self):
        again = forecast_dataset(self.rows, "2024-04",
                                 incumbent={"strategy": "ml", "statistical_weight": 0.0,
                                            "version": "PREVIOUS-ML", "certified_wape": 15.0})
        self.assertEqual(self.result, again)

    def test_api_uses_generic_runner_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings=Settings(state_dir=Path(directory)),
                             historical_state_dir=Path(directory) / "historical")
            self.assertIsInstance(app.state.forecast_runner, GeneralizedMonthlyForecastRunner)

    def test_ml_unavailable_leaves_statistical_forecast(self):
        with patch("services.forecast_engine.engine.MODEL_FACTORIES", {}):
            result = forecast_dataset(self.rows, "2024-04")
        self.assertTrue(all(row["ml"] is None and row["model_strategy"] == "statistical"
                            for chain in result["chains"] for row in chain["forecast_towell"]))

    def test_ensemble_failure_retains_published_vintage_without_republishing(self):
        class SyntheticProvider(DataProvider):
            name = "synthetic-test"

            def records(self):
                return synthetic_rows()

            def load(self, name):
                raise AssertionError("must_not_load_pilot_snapshot")

            def decisions(self):
                return []

            def vintages(self):
                return []

        with tempfile.TemporaryDirectory() as directory:
            storage = LocalPersistenceProvider(Path(directory) / "state.sqlite3")
            runner = GeneralizedMonthlyForecastRunner(SyntheticProvider(),
                                                      state_dir=Path(directory), persistence=storage)
            first = runner.run_month("2024-04")
            self.assertEqual(first["state"], "Completed", first["errors"])
            registry = ChampionRegistry(storage)
            for chain in first["results"]["chains"]:
                registry.publish(chain["chain_id"], "Venta", "chain",
                                 chain["model_audit"]["selected_candidate"], actor="manager", authorized=True)
            with patch("services.forecast_engine.forecast_dataset", side_effect=RuntimeError("ensemble_failed")):
                second = runner.run_month("2024-04")
            self.assertEqual(second["state"], "Completed", second["errors"])
            self.assertEqual(len(storage.list("forecast_vintages")), 1)
            self.assertTrue(all(chain["fallback"] == "retained_published_champion"
                                for chain in second["results"]["chains"]))


if __name__ == "__main__":
    unittest.main()
