"""Regression for a stale method choice whose current product tail is too short."""
import unittest
from unittest.mock import Mock, patch

from services.forecast_engine import retrospective
from services.forecast_engine.engine import ForecastPolicy, forecast_dataset
from services.assistant_api.forecast_execution import execute_forecast


def observations(product, *, gap=False):
    rows = []
    for offset in range(22):
        if gap and offset == 12:
            continue
        period = f"{2025 + offset // 12}-{1 + offset % 12:02d}"
        rows.append({
            "chain_id": "chain", "product_id": product, "product_code": product, "description": product,
            "category": "Toalla", "variant": "Color", "period": period, "objective": "Venta",
            "value": str(100 + offset * 3), "available_at": None,
            "availability_source": "UNKNOWN", "evidence_mode": "RETROSPECTIVE_TRAINING",
            "source_id": product + ":" + period,
        })
    return rows


class ForecastExecutionTests(unittest.TestCase):
    def test_recovers_only_the_proven_method_failure_and_keeps_other_product(self):
        rows = observations("short", gap=True) + observations("complete")
        choose_window = lambda candidate: (candidate["model"] != "Media móvil 12",
                                            candidate.get("retrospective_wape") or 0)
        before = list(rows)
        with patch.object(retrospective, "order", choose_window), patch.dict(retrospective.MODEL_FACTORIES, {}, clear=True):
            result, exclusions = execute_forecast(
                rows, "2026-10", compute=forecast_dataset, chain_id="chain", objective="Venta",
                policy=ForecastPolicy(), evidence_mode="RETROSPECTIVE_TRAINING")
        self.assertEqual(rows, before)
        excluded, = exclusions
        self.assertEqual((excluded["product_id"], excluded["model"], excluded["available_history"],
                          excluded["required_history"], excluded["horizon"]),
                         ("short", "Media móvil 12", 9, 12, 1))
        self.assertEqual(excluded["reason"], "MODEL_CURRENT_HISTORY_INSUFFICIENT")
        self.assertEqual((excluded["history_periods"][0], excluded["history_periods"][-1]), ("2026-02", "2026-10"))
        self.assertEqual({r["product_id"] for r in result["chains"][0]["forecast_towell"]}, {"complete"})
        self.assertEqual(len(result["chains"][0]["forecast_towell"]), 12)

    def test_unknown_value_error_is_propagated_without_retry_or_exclusion(self):
        compute = Mock(side_effect=ValueError("unrelated error"))
        with self.assertRaisesRegex(ValueError, "unrelated error"):
            execute_forecast(observations("product"), "2026-10", compute=compute,
                             evidence_mode="RETROSPECTIVE_TRAINING")
        compute.assert_called_once()

    def test_ordinary_success_and_point_in_time_error_keep_original_contract(self):
        compute = Mock(return_value={"chains": [{"original": True}]})
        result, excluded = execute_forecast([], "2026-10", compute=compute)
        self.assertEqual(result, {"chains": [{"original": True}]})
        self.assertEqual(excluded, [])
        compute.side_effect = ValueError("requiere 12 periodos")
        with self.assertRaisesRegex(ValueError, "requiere 12 periodos"):
            execute_forecast(observations("product"), "2026-10", compute=compute,
                             evidence_mode="POINT_IN_TIME")


if __name__ == "__main__":
    unittest.main()
