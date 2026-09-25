from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest

from engine import ActualRecord, ClosureConfig, FrozenForecast, HistoricalMetric, run_closure


PERIOD = "2026-09"


def records() -> list[ActualRecord]:
    return [
        ActualRecord("chocolate", PERIOD, 100, 110, 105, 120, color="Chocolate"),
        ActualRecord("morado", PERIOD, 80, 90, 85, 100, color="Morado"),
        ActualRecord("azul", PERIOD, 0, 0, 0, 0, color="Azul"),
    ]


def forecasts(challenger_values=(95.0, 78.0, 0.0), towell_values=(110.0, 88.0, 0.0)) -> list[FrozenForecast]:
    output: list[FrozenForecast] = []
    ids = ("chocolate", "morado", "azul")
    actual = (100.0, 80.0, 0.0)
    client = (120.0, 100.0, 0.0)
    statistical = (110.0, 88.0, 0.0)
    for horizon in range(1, 13):
        issued_month = 9 - horizon
        issued_year = 2026
        while issued_month <= 0:
            issued_month += 12
            issued_year -= 1
        issued = f"{issued_year:04d}-{issued_month:02d}-01T00:00:00+00:00"
        for index, series_id in enumerate(ids):
            values = {
                "statistical": statistical[index] + (horizon - 1),
                "ml": challenger_values[index] + (horizon - 1),
                "challenger": challenger_values[index] + (horizon - 1),
                "towell": towell_values[index] + (horizon - 1),
            }
            if horizon == 1:
                values["client"] = client[index]
            for engine, value in values.items():
                kwargs = {}
                if engine == "towell":
                    kwargs = {"p10": max(0, actual[index] - 25), "p50": actual[index], "p90": actual[index] + 25, "p95": actual[index] + 35}
                output.append(FrozenForecast(series_id, PERIOD, engine, f"{engine}-v1", issued, horizon, value, **kwargs))
    return output


class ClosureEngineTests(unittest.TestCase):
    def result(self, **kwargs):
        return run_closure(PERIOD, kwargs.pop("records", records()), kwargs.pop("forecasts", forecasts()), **kwargs)

    def test_cp01_complete_period_closes(self):
        self.assertEqual(self.result()["status"], "closed")

    def test_cp02_missing_sale_blocks(self):
        source = records()
        source[0] = replace(source[0], sale=None)
        result = self.result(records=source)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("missing_sale:chocolate", result["validation"]["blockers"])

    def test_cp03_confirmed_zero_is_valid(self):
        self.assertEqual(self.result()["totals"]["sale"], 180.0)

    def test_cp04_uses_frozen_vintage(self):
        result = self.result()
        towell = next(row for row in result["metrics"] if row["engine"] == "towell")
        self.assertEqual(towell["versions"], ["towell-v1"])
        self.assertTrue(result["snapshot"]["frozen_forecasts"][0]["frozen"])

    def test_cp05_wape_reproducible(self):
        first = self.result(closed_at="2026-10-01T00:00:00+00:00")
        second = self.result(closed_at="2026-10-01T00:00:00+00:00")
        self.assertEqual(first["metrics"], second["metrics"])
        self.assertEqual(first["snapshot_hash"], second["snapshot_hash"])

    def test_cp06_bias_direction(self):
        towell = next(row for row in self.result()["metrics"] if row["engine"] == "towell")
        self.assertGreater(towell["bias"], 0)
        self.assertEqual(towell["bias_direction"], "overforecast")

    def test_cp07_fill_rate(self):
        self.assertEqual(self.result()["service"]["fill_rate"], 95.0)

    def test_cp08_client_benchmark(self):
        benchmark = self.result()["benchmark"]
        self.assertGreater(benchmark["client_wape"], benchmark["towell_wape"])
        self.assertGreater(benchmark["towell_value_added_points"], 0)

    def test_cp09_horizons_one_through_twelve(self):
        rows = [row for row in self.result()["horizon_accuracy"] if row["engine"] == "towell"]
        self.assertEqual([row["horizon"] for row in rows], list(range(1, 13)))

    def test_cp10_interval_coverage(self):
        coverage = self.result()["interval_coverage"]
        self.assertEqual(len(coverage), 12)
        self.assertEqual(coverage[0]["coverage_p90"], 100.0)
        self.assertEqual(coverage[0]["coverage_p95"], 100.0)

    def test_cp11_champion_wins_and_is_preserved(self):
        result = self.result(forecasts=forecasts(challenger_values=(160, 150, 30)))
        self.assertFalse(result["champion_changed"])
        self.assertNotEqual(result["challenger_validation"]["state"], "candidate_for_promotion")

    def test_cp12_one_challenger_win_does_not_promote(self):
        result = self.result()
        self.assertEqual(result["challenger_validation"]["consecutive_wins"], 1)
        self.assertEqual(result["challenger_validation"]["state"], "in_validation")
        self.assertFalse(result["challenger_validation"]["automatic_promotion"])

    def test_cp13_three_consistent_wins_becomes_candidate(self):
        history = [
            HistoricalMetric("2026-07", "statistical", 24.1, 2), HistoricalMetric("2026-07", "challenger", 22.7, 1),
            HistoricalMetric("2026-08", "statistical", 23.8, 2), HistoricalMetric("2026-08", "challenger", 21.9, 1),
        ]
        result = self.result(history=history, config=ClosureConfig(maximum_challenger_wape_stddev=20))
        self.assertEqual(result["challenger_validation"]["state"], "candidate_for_promotion")
        self.assertFalse(result["challenger_validation"]["automatic_promotion"])

    def test_cp14_unstable_challenger_is_not_promoted(self):
        history = [
            HistoricalMetric("2026-07", "statistical", 24, 2), HistoricalMetric("2026-07", "challenger", 21, 1),
            HistoricalMetric("2026-08", "statistical", 23, 2), HistoricalMetric("2026-08", "challenger", 38, 1),
        ]
        result = self.result(history=history)
        self.assertNotEqual(result["challenger_validation"]["state"], "candidate_for_promotion")

    def test_cp15_drift_registers_signal(self):
        result = self.result(prior_sales={"chocolate": [40, 42, 41, 39]})
        self.assertTrue(result["drift"])

    def test_cp16_retraining_is_queued_not_published(self):
        result = self.result(prior_sales={"chocolate": [40, 42, 41, 39]})
        event = next(row for row in result["learning_events"] if row["type"] == "retraining_requested")
        self.assertEqual(event["status"], "queued")
        self.assertFalse(event["publication_allowed"])

    def test_cp17_correction_creates_new_revision(self):
        first = self.result(closed_at="2026-10-01T00:00:00+00:00")
        corrected = records()
        corrected[0] = replace(corrected[0], sale=105)
        second = self.result(records=corrected, revision=2, previous_snapshot_hash=first["snapshot_hash"], correction_reason="Venta confirmada por cliente", closed_at="2026-10-02T00:00:00+00:00")
        self.assertEqual(second["revision"], 2)
        self.assertNotEqual(first["snapshot_hash"], second["snapshot_hash"])

    def test_cp18_reopening_authorization_is_database_enforced(self):
        sql = (Path(__file__).parents[2] / "supabase/legacy_migrations/202609200001_prd06_closure_learning.sql").read_text(encoding="utf-8")
        self.assertIn("request_period_reopening", sql)
        self.assertIn("manager_role_required", sql)

    def test_cp19_blocked_or_failed_work_never_changes_champion(self):
        source = records()
        source[0] = replace(source[0], sale=None)
        result = self.result(records=source)
        self.assertFalse(result["champion_changed"])

    def test_cp20_normalized_contract(self):
        self.assertEqual(self.result()["snapshot"]["source_contract"], "normalized_platform_records")

    def test_cp21_no_excel_dependency(self):
        source = Path(__file__).with_name("engine.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("openpyxl", source)
        self.assertNotIn("read_excel", source)

    def test_cp22_dashboard_unchanged(self):
        self._assert_visual_hash("app/executive-dashboard.tsx")

    def test_cp23_navigation_unchanged(self):
        self._assert_visual_hash("app/forecast-towell-app.tsx")

    def test_cp24_components_unchanged(self):
        for path in ("app/forecast-engines-view.tsx", "app/statistical-engine-view.tsx", "app/ml-engine-view.tsx", "app/globals.css"):
            self._assert_visual_hash(path)

    def test_cp25_next_cycle_is_prepared(self):
        result = self.result()
        self.assertTrue(result["next_cycle_prepared"])
        self.assertTrue(any(row["type"] == "next_cycle_prepared" for row in result["learning_events"]))

    def _assert_visual_hash(self, relative_path: str):
        root = Path(__file__).parents[2]
        baseline = json.loads((root / "services/ensemble_engine/visual-baseline.json").read_text(encoding="utf-8"))
        digest = sha256((root / relative_path).read_bytes()).hexdigest().upper()
        self.assertEqual(digest, baseline[relative_path])


if __name__ == "__main__":
    unittest.main()
