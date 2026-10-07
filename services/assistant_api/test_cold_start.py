from __future__ import annotations

import unittest

from services.assistant_api.cold_start import launch_forecast
from services.forecast_engine.engine import add_month

CHAIN = "00000000-0000-4000-8000-000000000001"
OTHER_CHAIN = "00000000-0000-4000-8000-000000000002"
CATEGORY = "00000000-0000-4000-8000-000000000003"
OTHER_CATEGORY = "00000000-0000-4000-8000-000000000004"
TARGET = "00000000-0000-4000-8000-000000000005"


def corpus():
    rows = []
    for index in range(8):
        product = f"00000000-0000-4000-8000-{100 + index:012d}"
        for month in range(23):
            rows.append({"chain_id": CHAIN, "product_id": product, "product_code": str(index),
                         "description": f"Toalla MB Familia Color {index}", "category": CATEGORY,
                         "period": add_month("2024-09", month), "objective": "Venta",
                         "value": float((100 + index * 5) * (1 + month / 15)),
                         "available_at": None, "evidence_mode": "RETROSPECTIVE_TRAINING"})
    for month in range(7):
        rows.append({"chain_id": CHAIN, "product_id": TARGET, "product_code": "target",
                     "description": "Toalla MB Familia Oxford", "category": CATEGORY,
                     "period": add_month("2026-01", month), "objective": "Venta",
                     "value": 0.0 if month < 2 else float(80 + month * 20),
                     "available_at": None, "evidence_mode": "RETROSPECTIVE_TRAINING"})
    return rows


def target():
    return {"id": TARGET, "chain_id": CHAIN, "product_code": "target",
            "description": "Toalla MB Familia Oxford", "category_id": CATEGORY}


class ColdStartTests(unittest.TestCase):
    def test_five_sales_months_yield_separate_h1_h12_without_official_metrics(self):
        rows = corpus()
        result = launch_forecast(rows, "2026-07", target())
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "PROVISIONAL_COLD_START")
        self.assertEqual(result["observed_months"], 5)
        self.assertEqual(len(result["comparables"]), 8)
        self.assertEqual([row["horizon"] for row in result["horizons"]], list(range(1, 13)))
        self.assertEqual(result["horizons"][0]["target_period"], "2026-08")
        self.assertEqual(result["horizons"][-1]["target_period"], "2027-07")
        self.assertTrue(all(row["value"] >= 0 for row in result["horizons"]))
        self.assertIsNone(result["target_wape"])
        self.assertFalse(result["official_publication"])
        self.assertFalse(result["point_in_time_certified"])
        self.assertEqual(result, launch_forecast(rows[::-1], "2026-07", target()))

    def test_no_sales_can_use_peers_but_not_fabricated_target_history(self):
        rows = [row for row in corpus() if row["product_id"] != TARGET]
        result = launch_forecast(rows, "2026-07", target())
        self.assertIsNotNone(result)
        self.assertEqual(result["observed_months"], 0)
        self.assertEqual(len(result["horizons"]), 12)

    def test_no_cross_chain_or_category_and_no_future_leakage(self):
        rows = corpus()
        self.assertIsNone(launch_forecast(rows, "2026-07", {**target(), "category_id": OTHER_CATEGORY}))
        foreign = [{**row, "chain_id": OTHER_CHAIN} for row in rows]
        self.assertIsNone(launch_forecast(foreign, "2026-07", target()))
        future = [{**rows[0], "period": "2026-08"}, *rows]
        self.assertIsNone(launch_forecast(future, "2026-07", target()))

    def test_missing_tail_does_not_gain_months_from_prelaunch_zeros(self):
        rows = [row for row in corpus() if not (row["product_id"] == TARGET and row["period"] == "2026-06")]
        result = launch_forecast(rows, "2026-07", target())
        self.assertIsNotNone(result)
        self.assertEqual(result["observed_months"], 1)


if __name__ == "__main__":
    unittest.main()
