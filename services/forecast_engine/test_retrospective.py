from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from services.forecast_engine.engine import ForecastPolicy, add_month, forecast_dataset, normalize_retrospective_dataset
from services.forecast_engine.retrospective import forecast_chain, identities_at, feature_names
from services.ml_engine.engine import MODEL_FACTORIES, Preprocessor
from services.statistical_engine.engine import MODELS


def rows19(products=6):
    return [{"chain_id": "SYNTHETIC", "product_id": str(p), "product_code": str(p),
             "description": "Synthetic regression", "category": "CATEGORY", "variant": "",
             "period": add_month("2025-01", i), "objective": "Venta", "value": 100 * (p + 1) + i * 3,
             "available_at": None, "availability_source": "UNKNOWN"}
            for p in range(products) for i in range(19)]


class RetrospectiveClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = rows19()
        cls.result = forecast_dataset(cls.rows, "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")
        cls.chain = cls.result["chains"][0]
        cls.audit = cls.chain["model_audit"]

    def test_19_month_rigid_split_is_mathematically_empty(self):
        policy = ForecastPolicy()
        old_train_end = add_month("2025-01", policy.min_train_observations - 1)
        self.assertGreater(add_month(old_train_end, 13), "2026-07")
        self.assertEqual(policy.min_train_observations, 18)
        self.assertGreater(self.audit["training_samples"], 0)
        self.assertGreater(self.audit["origins"]["selection"], 0)

    def test_19_period_stat_catalog_and_real_requirements(self):
        candidates = [c for c in self.audit["candidate_models"] if c["family"] == "statistical" and c["product_id"] == "0"]
        self.assertEqual({c["model"] for c in candidates}, set(MODELS))
        self.assertEqual(len(candidates), 14)
        winter = next(c for c in candidates if c["model"] == "Holt-Winters")
        self.assertEqual(winter["status"], "NOT_APPLICABLE")
        self.assertEqual(winter["reason"], "REQUIRES_24_PERIODS")
        self.assertIsNone(winter["retrospective_wape"])
        for name in ("Naive", "Media móvil 3", "Media móvil 6", "Media móvil 12", "SES", "Holt", "Croston", "SBA", "TSB"):
            candidate = next(c for c in candidates if c["model"] == name)
            self.assertEqual(candidate["status"], "EVALUATED")
            self.assertIsNotNone(candidate["retrospective_wape"])
            self.assertGreater(candidate["observations"], 0)

    def test_all_three_ML_trainable_validated_one_selected(self):
        candidates = [c for c in self.audit["candidate_models"] if c["family"] == "ml"]
        self.assertEqual({c["model"] for c in candidates}, set(MODEL_FACTORIES))
        self.assertTrue(all(c["trainable"] and c["validated"] for c in candidates))
        self.assertEqual(sum(c["selected"] for c in candidates), 1)
        self.assertTrue(all(c["retrospective_wape"] is not None and c["validation_observations"] > 0 for c in candidates))
        self.assertGreater(self.audit["training_samples"], 832)

    def test_12_literal_operational_horizons_and_unpublished(self):
        for product in range(6):
            rows = [r for r in self.chain["forecast_towell"] if r["product_id"] == str(product)]
            self.assertEqual([r["horizon"] for r in rows], list(range(1, 13)))
            self.assertEqual(rows[0]["target_period"], "2026-08")
            self.assertEqual(rows[-1]["target_period"], "2027-07")
            self.assertTrue(all(r["ml"] is not None for r in rows))
        self.assertFalse(self.chain["selection"]["automatic_promotion"])
        self.assertFalse(self.chain["selection"]["promotion_allowed"])
        self.assertIsNone(self.chain["selection"]["initial_champion_candidate"])
        self.assertFalse(self.chain["temporal_certification"])
        self.assertIsNone(self.audit["certified_wape"])
        self.assertIsNone(self.audit["certified_bias"])

    def test_origin_train_targets_are_earlier_than_test(self):
        self.assertTrue(self.audit["folds"])
        for fold in self.audit["folds"]:
            self.assertLess(fold["train_target_max"], fold["test_target_min"])
            self.assertGreaterEqual(fold["training_samples"], ForecastPolicy().min_train_observations)

    def test_available_at_unchanged_and_PIT_still_rejects(self):
        before = copy.deepcopy(self.rows)
        normalize_retrospective_dataset(self.rows, "2026-07")
        self.assertEqual(self.rows, before)
        with self.assertRaisesRegex(ValueError, "availability_metadata_missing"):
            forecast_dataset(self.rows, "2026-07")
        with self.assertRaisesRegex(ValueError, "data_leakage_detected"):
            forecast_dataset(self.rows + [{**self.rows[0], "period": "2026-08"}], "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")

    def test_scope_WAPE_uses_pooled_pairs_not_product_mean(self):
        series = normalize_retrospective_dataset(self.rows, "2026-07")
        x = forecast_chain(series, "2026-07", ForecastPolicy(), None)
        metrics = next(c for c in x["model_audit"]["scope_statistical_candidates"] if c["model"] == "Naive")
        # Each Naive error = 3*h; counts follow exact origin/horizon rules.
        pairs = []
        for origin in range(6, 18):
            for p in range(6):
                for h in (1, 3, 6, 12):
                    if origin + h > 18 or h >= 6 and origin + 1 < 12:
                        continue
                    pairs.append((100 * (p + 1) + 3 * (origin + h), 3 * h))
        self.assertAlmostEqual(metrics["retrospective_wape"], round(100 * sum(e for _, e in pairs) / sum(a for a, _ in pairs), 4))

    def test_eval_H12_absent_is_null_not_fabricated_but_future_exists(self):
        candidate = next(c for c in self.audit["candidate_models"] if c["model"] == "Naive")
        self.assertEqual([h["horizon"] for h in candidate["by_horizon"]], [1, 3, 6, 12])
        self.assertIsNone(candidate["by_horizon"][-1]["wape"])
        self.assertEqual(candidate["by_horizon"][-1]["observations"], 0)
        self.assertEqual(self.chain["forecast_towell"][11]["target_period"], "2027-07")

    def test_scope_selected_horizon_metrics_are_literal_and_pooled(self):
        metrics = self.audit["selected_statistical_metrics"]
        self.assertEqual(metrics["retrospective_wape"], self.audit["retrospective_statistical_metrics"]["wape"])
        self.assertEqual([h["horizon"] for h in metrics["by_horizon"]], [1, 3, 6, 12])
        self.assertIsNone(metrics["by_horizon"][-1]["wape"])

    def test_every_preprocessor_fit_uses_only_train_and_fresh_instance(self):
        rows = rows19(1)
        products = normalize_retrospective_dataset(rows, "2026-07")
        fit_calls = []
        original = Preprocessor.fit
        def capture(prep, x):
            fit_calls.append((prep, copy.deepcopy(x)))
            return original(prep, x)
        with patch.object(Preprocessor, "fit", capture):
            result = forecast_chain(products, "2026-07", ForecastPolicy(), None)
        folds = result["model_audit"]["folds"]
        for index, fold in enumerate(folds):
            group = fit_calls[index * 3:index * 3 + 3]
            self.assertEqual(len(group), 3)
            self.assertEqual(len({id(p) for p, _ in group}), 3)
            self.assertTrue(all(len(x) == fold["training_samples"] for _, x in group))
        self.assertGreater(len(fit_calls[-1][1]), len(fit_calls[0][1]))

    def test_future_actual_change_cannot_affect_earlier_fold_predictions(self):
        from services.forecast_engine.engine import _feature, _training_samples
        before = normalize_retrospective_dataset(rows19(1), "2026-07")
        after = normalize_retrospective_dataset([{**r, "value": r["value"] + 9999} if r["period"] > "2025-10" else r for r in rows19(1)], "2026-07")
        ids = identities_at(before, "2025-10")
        xa, ya = _training_samples(before, "2025-10", ids)
        xb, yb = _training_samples(after, "2025-10", ids)
        self.assertEqual(xa, xb)
        self.assertEqual(ya.tolist(), yb.tolist())
        self.assertEqual(_feature(before[0], "2025-10", "2026-01", ids), _feature(after[0], "2025-10", "2026-01", ids))
        self.assertEqual(len(feature_names(ids)), len(xa[0]))


if __name__ == "__main__":
    unittest.main()
