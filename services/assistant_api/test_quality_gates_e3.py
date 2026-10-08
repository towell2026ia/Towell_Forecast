"""E3 deterministic quality/service cases; no network or historical writes."""

from __future__ import annotations

import unittest
from copy import deepcopy
import uuid

import httpx

from services.assistant_api.quality_gates import QualityPolicy, data_quality, forecast_quality, observed_service
from services.assistant_api.operational_preview import snapshot_hash
from services.assistant_api.vintage_service import SupabaseForecastWriteRepository, candidate_payload
from services.assistant_api.supabase_access import PreviewError
from services.statistical_engine.engine import MODELS
from services.ml_engine.engine import MODEL_FACTORIES


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.preview = {"chain_id": "chain-a", "issue_period": "2026-07", "eligibility": {
            "visible_products": 2, "stat_eligible": 1, "ml_eligible": 1},
            "statistical": {"retrospective_wape": 45, "retrospective_bias": 12},
            "ml": {"retrospective_wape": 60, "retrospective_bias": 2},
            "selection": {"preview_leader": {"retrospective_wape": 44,
                "retrospective_bias": 11, "observations": 30}}}
        self.rows = [{"chain_id": "chain-a", "product_id": "product-a",
            "period": f"{year:04d}-{month:02d}", "value": 0,
            "available_at": "2026-07-31T00:00:00Z"}
            for year, month in ([(2025, month) for month in range(1, 13)] +
                                [(2026, month) for month in range(1, 8)])]

    def test_literal_zero_is_observed_and_cold_start_does_not_block_scope(self):
        result = data_quality(self.rows, self.preview)
        self.assertEqual(result["status"], "DATA_QUALITY_READY")
        self.assertEqual(result["history_months"], 19)
        self.assertEqual(result["products_total"], 2)
        self.assertEqual(result["products_with_history"], 1)

    def test_gaps_and_incomplete_history(self):
        rows = [row for row in self.rows if row["period"] not in {"2025-05", "2025-06", "2025-07", "2025-08"}]
        result = data_quality(rows, self.preview)
        self.assertEqual(result["status"], "HISTORICAL_INCOMPLETE")
        self.assertEqual(result["missing_months"], 4)
        self.assertEqual(data_quality(self.rows[-12:], self.preview)["status"], "INSUFFICIENT_HISTORY")
        long_rows = self.rows + [{**row, "period": row["period"].replace("2025", "2024")}
            for row in self.rows if row["period"].startswith("2025")]
        incomplete = [row for row in long_rows if row["period"] not in {"2025-05", "2025-06", "2025-07", "2025-08", "2025-09"}]
        self.assertEqual(data_quality(incomplete, self.preview)["status"], "HISTORICAL_INCOMPLETE")

    def test_identity_negative_future_and_availability(self):
        self.assertEqual(data_quality(self.rows + [deepcopy(self.rows[0])], self.preview)["status"], "IDENTITY_CONFLICT")
        negative = deepcopy(self.rows); negative[0]["value"] = -1
        self.assertEqual(data_quality(negative, self.preview)["status"], "BLOCKED")
        future = self.rows + [{**self.rows[0], "period": "2026-08", "value": -1}]
        self.assertEqual(data_quality(future, self.preview)["status"], "DATA_QUALITY_READY")
        unknown = deepcopy(self.rows); unknown[0]["available_at"] = None
        self.assertEqual(data_quality(unknown, self.preview)["status"], "DATA_QUALITY_WARNING")
        self.assertIsNone(unknown[0]["available_at"])

    def test_scope_specific_no_degradation(self):
        gate = forecast_quality(self.preview)
        self.assertEqual(gate["status"], "FORECAST_QUALITY_READY")
        self.assertEqual(gate["baseline_scope"], "chain-a")
        self.assertEqual(gate["stat_wape"], 45)
        self.assertEqual(gate["ml_wape"], 60)
        worse = deepcopy(self.preview); worse["selection"]["preview_leader"]["retrospective_wape"] = 50
        self.assertEqual(forecast_quality(worse)["status"], "FORECAST_QUALITY_BLOCKED")
        absent = deepcopy(self.preview); absent["statistical"]["retrospective_wape"] = None
        self.assertEqual(forecast_quality(absent)["status"], "NO_REFERENCE")
        self.assertEqual(QualityPolicy().version, "E3-GATES-1.0.0")

    def test_e3_v2_is_versioned_and_requires_homogeneous_scope_baseline(self):
        policy = QualityPolicy.selection_v2()
        self.assertEqual(policy.version, "E3-GATES-2.0.0")
        self.assertEqual(policy.minimum_improvement_points, 0.25)
        preview = deepcopy(self.preview)
        self.assertEqual(forecast_quality(preview, policy)["status"], "INSUFFICIENT_EVIDENCE")
        preview["selection"]["scope_comparable_baseline"] = {
            "retrospective_wape": 45, "retrospective_bias": 12,
            "by_horizon": [{"horizon": h, "wape": 45} for h in (1, 2, 3)]}
        preview["selection"]["scope_leader"] = {"strategy": "ml", "retrospective_wape": 44,
            "retrospective_bias": 11, "observations": 30,
            "by_horizon": [{"horizon": h, "wape": 44} for h in (1, 2, 3)]}
        self.assertEqual(forecast_quality(preview, policy)["status"], "FORECAST_QUALITY_READY")
        preview["selection"]["scope_leader"]["by_horizon"][0]["wape"] = 48
        self.assertEqual(forecast_quality(preview, policy)["status"], "FORECAST_QUALITY_WARNING")

    def test_common_catalog_horizon_warning_and_no_global_wape_cap(self):
        self.assertEqual(len(MODELS), 14)
        self.assertEqual(len(MODEL_FACTORIES), 3)
        high = deepcopy(self.preview)
        high["statistical"]["retrospective_wape"] = 200
        high["selection"]["preview_leader"]["retrospective_wape"] = 150
        self.assertEqual(forecast_quality(high)["status"], "FORECAST_QUALITY_READY")
        high["selection"]["preview_leader"]["validation_by_horizon"] = [{"horizon": 1, "wape": 215}]
        self.assertEqual(forecast_quality(high)["status"], "FORECAST_QUALITY_WARNING")

    def test_incremental_history_reassesses_without_overwriting_old_evidence(self):
        incomplete = [row for row in self.rows if row["period"] not in {"2025-05", "2025-06", "2025-07", "2025-08"}]
        before = deepcopy(incomplete)
        first = data_quality(incomplete, self.preview)
        second = data_quality(self.rows, self.preview)
        self.assertEqual(first["status"], "HISTORICAL_INCOMPLETE")
        self.assertEqual(second["status"], "DATA_QUALITY_READY")
        self.assertEqual(incomplete, before)

    def test_fill_rate_uses_ratio_and_zero_rules(self):
        rows = [
            {"product_id": "p1", "period": "2026-07", "metric_code": "ORDER", "value": 100},
            {"product_id": "p1", "period": "2026-07", "metric_code": "DELIVERY", "value": 90},
            {"product_id": "p2", "period": "2026-07", "metric_code": "ORDER", "value": 1},
            {"product_id": "p2", "period": "2026-07", "metric_code": "DELIVERY", "value": 1},
        ]
        result = observed_service(rows)
        self.assertAlmostEqual(result["observed_fill_rate"], 91 / 101 * 100)
        self.assertEqual(result["status"], "TARGET_NOT_MET")
        self.assertIsNone(result["operational_recommendation"])
        zero = [{**row, "value": 0} for row in rows]
        self.assertEqual(observed_service(zero)["observed_fill_rate"], 100)
        zero[1]["value"] = 2
        self.assertIsNone(observed_service(zero)["observed_fill_rate"])
        self.assertEqual(observed_service(rows[:1])["status"], "NOT_MEASURABLE")
        met = deepcopy(rows); met[1]["value"] = 99
        self.assertEqual(observed_service(met)["status"], "TARGET_MET")
        self.assertEqual(observed_service(met)["target_fill_rate"], 95.0)
        self.assertEqual(observed_service(met + [{"product_id": "p3", "period": "2026-07", "metric_code": "DELIVERY", "value": 8}])["status"], "NOT_MEASURABLE")

    def test_vintage_payload_has_lineage_twelve_horizons_and_deterministic_hash(self):
        chain, product, actor = [str(uuid.UUID(int=i)) for i in (11, 12, 13)]
        sales = [{**row, "chain_id": chain, "product_id": product,
            "source_id": str(uuid.UUID(int=index + 30)), "available_at": None}
            for index, row in enumerate(self.rows)]
        preview = {"status": "PREVIEW", "chain_id": chain, "objective": "Venta", "issue_period": "2026-07",
            "cutoff": "2026-07-31", "engine_version": "fixture-engine", "mode": "RETROSPECTIVE_TRAINING",
            "dataset_hash": snapshot_hash(sales),
            "eligibility": {"visible_products": 1, "stat_eligible": 1, "ml_eligible": 1},
            "statistical": {"retrospective_wape": 12, "retrospective_bias": 2,
                "candidates": [{"model": "Naive", "retrospective_wape": 12}]},
            "ml": {"retrospective_wape": 14, "retrospective_bias": 1,
                "candidates": [{"model": "Random Forest Global", "retrospective_wape": 14}]},
            "selection": {"preview_leader": {"strategy": "ensemble", "validation_wape": 10,
                "validation_bias": 1, "observations": 20}},
            "products": [{"product_id": product, "category_id": None, "forecast_status": "ACTIVE",
                "horizons": [{"horizon": month, "target_period": f"{2026 + (7 + month - 1) // 12}-{(7 + month - 1) % 12 + 1:02d}",
                    "statistical_value": 10, "ml_value": 9, "ensemble_value": 9.5,
                    "forecast_towell": 9.5, "model_strategy": "ensemble", "p10": None, "p50": 9.5,
                    "p90": None, "p95": None, "statistical_model": "Naive", "ml_model": "Random Forest Global"}
                    for month in range(1, 13)]}]}
        first = candidate_payload(preview, sales, [], actor_id=actor, git_sha="test-sha", preview_id="preview-1")
        second = candidate_payload(preview, list(reversed(sales)), [], actor_id=actor, git_sha="test-sha", preview_id="preview-1")
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(len(first["horizons"]), 12)
        self.assertEqual(len(first["aggregates"]), 12)
        self.assertEqual(len(first["inputs"]), len(sales))
        self.assertEqual(first["certification_status"], "PROVISIONAL")
        self.assertEqual(first["evidence_mode"], "RETROSPECTIVE_TRAINING")
        self.assertTrue(all(row.get("operational_value") is None for row in first["horizons"]))
        self.assertEqual(first["metrics"], [{"metric": "WAPE", "value": 10}, {"metric": "BIAS", "value": 1}])
        self.assertEqual(len(first["gates"]), 3)
        changed = deepcopy(sales); changed[0]["value"] = 17
        with self.assertRaisesRegex(PreviewError, "HASH_MISMATCH"):
            candidate_payload(preview, changed, [], actor_id=actor, git_sha="test-sha", preview_id="preview-1")

    def test_write_repository_redacts_key_and_uses_only_rpc(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json=str(uuid.UUID(int=99)))
        key = "synthetic-secret-only-for-tests"
        repo = SupabaseForecastWriteRepository("https://fixture.supabase.co", key,
            transport=httpx.MockTransport(handle))
        self.assertNotIn(key, repr(repo))
        self.assertEqual(repo.rpc("e3_freeze_vintage", {"p_vintage_id": str(uuid.UUID(int=1))}), str(uuid.UUID(int=99)))
        self.assertEqual(calls[0].url.path, "/rest/v1/rpc/e3_freeze_vintage")
        self.assertEqual(calls[0].headers["authorization"], "Bearer " + key)
        with self.assertRaisesRegex(PreviewError, "REQUEST_001"):
            repo.rpc("monthly_observations", {})
        with self.assertRaisesRegex(PreviewError, "REQUIRED_SECRET_MISSING"):
            SupabaseForecastWriteRepository("https://fixture.supabase.co", "").rpc("e3_freeze_vintage", {})


if __name__ == "__main__":
    unittest.main()
