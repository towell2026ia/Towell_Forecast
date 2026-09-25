from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from engine import AdjustmentProposal, DecisionConfig, ForecastPoint, approve_decision, create_decision, evaluate_decision


def points(horizons: int = 1) -> list[ForecastPoint]:
    output = []
    for horizon in range(1, horizons + 1):
        output.extend([
            ForecastPoint("chocolate", "2026-11", horizon, 100, f"v-choc-{horizon}", "FT-FENDI-2026-10-V01", "2026-10-01T00:00:00+00:00", 80, 100, 120, 130, color="Chocolate"),
            ForecastPoint("morado", "2026-11", horizon, 80, f"v-mor-{horizon}", "FT-FENDI-2026-10-V01", "2026-10-01T00:00:00+00:00", 60, 80, 100, 110, color="Morado"),
        ])
    return output


def proposal(series="chocolate", horizon=1, method="final_value", value=115, reason="promotion", explanation="Promoción confirmada por el cliente."):
    return AdjustmentProposal(series, horizon, method, value, reason, explanation)


def approved(proposals=None, horizons=1):
    decision = create_decision(points(horizons), proposals or [], "user-a", "manager")
    return approve_decision(decision, "manager-b", "manager")


class DecisionEngineTests(unittest.TestCase):
    def test_cp01_review_without_adjustment(self):
        result = approved([])
        self.assertTrue(all(row["forecast_approved"] == row["forecast_towell"] for row in result["entries"]))

    def test_cp02_positive_adjustment(self):
        result = create_decision(points(), [proposal()], "user-a", "manager")
        row = next(item for item in result["entries"] if item["series_id"] == "chocolate")
        self.assertEqual(row["adjustment"], 15)

    def test_cp03_negative_adjustment(self):
        result = create_decision(points(), [proposal(value=85)], "user-a", "manager")
        row = next(item for item in result["entries"] if item["series_id"] == "chocolate")
        self.assertEqual(row["adjustment"], -15)

    def test_cp04_missing_reason_blocks(self):
        result = create_decision(points(), [proposal(reason="")], "user-a", "manager")
        self.assertEqual(result["status"], "blocked")

    def test_cp05_valid_reason_continues(self):
        self.assertEqual(create_decision(points(), [proposal()], "user-a", "manager")["status"], "adjusted")

    def test_cp06_adjustment_inside_bands(self):
        result = create_decision(points(), [proposal(value=115)], "user-a", "manager")
        row = next(item for item in result["entries"] if item["series_id"] == "chocolate")
        self.assertTrue(row["within_p90"])
        self.assertFalse(row["outside_p95"])

    def test_cp07_adjustment_outside_p95_alerts(self):
        result = create_decision(points(), [proposal(value=150)], "user-a", "manager")
        self.assertTrue(any(row["type"] == "outside_probability_range" for row in result["governance_alerts"]))

    def test_cp08_large_adjustment_alerts(self):
        result = create_decision(points(), [proposal(value=140)], "user-a", "manager", config=DecisionConfig(significant_adjustment_percent=20))
        self.assertTrue(any(row["type"] == "significant_adjustment" for row in result["governance_alerts"]))

    def test_cp09_forecast_towell_is_immutable(self):
        result = create_decision(points(), [proposal()], "user-a", "manager")
        row = next(item for item in result["entries"] if item["series_id"] == "chocolate")
        self.assertEqual(row["forecast_towell"], 100)
        self.assertFalse(result["forecast_towell_changed"])

    def test_cp10_new_decision_creates_version(self):
        result = create_decision(points(), [proposal()], "user-a", "manager", prior_versions=1, previous_decision_hash="a" * 64)
        self.assertEqual(result["decision_version"], "DEC-FENDI-2026-11-V02")

    def test_cp11_approval_freezes_version(self):
        result = approved([proposal()])
        self.assertEqual(result["status"], "frozen")
        self.assertTrue(result["frozen"])

    def test_cp12_real_evaluates_both_forecasts(self):
        evaluation = evaluate_decision(approved([proposal()]), {"chocolate": 112, "morado": 80})
        self.assertIsNotNone(evaluation["towell_wape"])
        self.assertIsNotNone(evaluation["approved_wape"])

    def test_cp13_human_improves_positive_fva(self):
        evaluation = evaluate_decision(approved([proposal(value=112)]), {"chocolate": 112, "morado": 80})
        self.assertEqual(evaluation["classification"], "positive")
        self.assertGreater(evaluation["fva_points"], 0)

    def test_cp14_human_worsens_negative_fva(self):
        evaluation = evaluate_decision(approved([proposal(value=125)]), {"chocolate": 100, "morado": 80})
        self.assertEqual(evaluation["classification"], "negative")

    def test_cp15_no_adjustment_has_no_intervention(self):
        evaluation = evaluate_decision(approved([]), {"chocolate": 100, "morado": 80})
        self.assertEqual(evaluation["classification"], "no_intervention")

    def test_cp16_model_wape_uses_original_towell(self):
        evaluation = evaluate_decision(approved([proposal(value=112)]), {"chocolate": 112, "morado": 80})
        self.assertGreater(evaluation["towell_wape"], 0)

    def test_cp17_decision_wape_uses_approved(self):
        evaluation = evaluate_decision(approved([proposal(value=112)]), {"chocolate": 112, "morado": 80})
        self.assertEqual(evaluation["approved_wape"], 0)

    def test_cp18_champion_ignores_human_adjustments(self):
        evaluation = evaluate_decision(approved([proposal()]), {"chocolate": 112, "morado": 80})
        self.assertFalse(evaluation["champion_challenger_affected"])

    def test_cp19_ml_challenger_remains_independent(self):
        result = create_decision(points(), [proposal()], "user-a", "manager")
        self.assertFalse(result["champion_challenger_affected"])

    def test_cp20_audit_chain_is_persisted(self):
        sql = self._sql()
        for term in ("decision_audit_log", "decision.version_created", "decision.approved_and_frozen"):
            self.assertIn(term, sql)

    def test_cp21_correction_creates_new_version(self):
        result = create_decision(points(), [proposal(value=118)], "user-a", "manager", prior_versions=1, previous_decision_hash="a" * 64, correction_reason="Corrección de captura")
        self.assertEqual(result["revision"], 2)

    def test_cp22_frozen_change_requires_reopening(self):
        blocked = create_decision(points(), [proposal()], "user-a", "manager", prior_versions=1, previous_decision_hash="a" * 64, correction_reason="Cambio", previous_frozen=True)
        self.assertIn("frozen_decision_requires_reopening", blocked["blockers"])
        allowed = create_decision(points(), [proposal()], "user-a", "manager", prior_versions=1, previous_decision_hash="a" * 64, correction_reason="Cambio", previous_frozen=True, period_reopened=True)
        self.assertEqual(allowed["revision"], 2)

    def test_cp23_normalized_contract(self):
        self.assertEqual(create_decision(points(), [], "user-a", "manager")["source_contract"], "normalized_platform_records")

    def test_cp24_excel_is_never_read(self):
        source = Path(__file__).with_name("engine.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("openpyxl", source)
        self.assertNotIn("read_excel", source)

    def test_cp25_dashboard_unchanged(self):
        self._assert_visual_hash("app/executive-dashboard.tsx")

    def test_cp26_navigation_unchanged(self):
        self._assert_visual_hash("app/forecast-towell-app.tsx")

    def test_cp27_components_unchanged(self):
        for path in ("app/forecast-engines-view.tsx", "app/statistical-engine-view.tsx", "app/ml-engine-view.tsx", "app/globals.css"):
            self._assert_visual_hash(path)

    def test_cp28_fva_by_reason(self):
        evaluation = evaluate_decision(approved([proposal(value=112)]), {"chocolate": 112, "morado": 80})
        self.assertTrue(any(row["reason"] == "promotion" for row in evaluation["by_reason"]))

    def test_cp29_fva_by_horizon(self):
        decision = approved([proposal(horizon=h, value=110) for h in range(1, 13)], horizons=12)
        evaluation = evaluate_decision(decision, {"chocolate": 110, "morado": 80})
        self.assertEqual([row["horizon"] for row in evaluation["by_horizon"]], list(range(1, 13)))

    def test_cp30_failed_evaluation_protects_originals(self):
        evaluation = evaluate_decision(approved([]), {})
        self.assertEqual(evaluation["status"], "blocked")
        self.assertTrue(evaluation["originals_protected"])
        self.assertIn("fva_evaluation_failed", Path(__file__).with_name("supabase_runner.py").read_text(encoding="utf-8"))

    def _sql(self):
        return (Path(__file__).parents[2] / "supabase/legacy_migrations/202609200002_prd07_managerial_decisions.sql").read_text(encoding="utf-8")

    def _assert_visual_hash(self, relative_path: str):
        root = Path(__file__).parents[2]
        baseline = json.loads((root / "services/ensemble_engine/visual-baseline.json").read_text(encoding="utf-8"))
        actual = sha256((root / relative_path).read_bytes()).hexdigest().upper()
        self.assertEqual(actual, baseline[relative_path])


if __name__ == "__main__":
    unittest.main()
