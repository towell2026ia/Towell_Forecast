"""FT-SELECTION-2.0 fail-closed product comparison and literal band hierarchy."""
from __future__ import annotations

import copy
import unittest

from services.forecast_engine.selection_policy import (
    SELECTION_POLICY, evaluation_signature, select_band_rows, suggest_reference,
)


def candidate(family="statistical", model="SBA", wape=13.29, bias=0.02,
              observations=24, windows=3, signature="same", h1=9800):
    return {"family": family, "model": model, "strategy": family, "product_id": "FENDI-AZUL",
            "wape": wape, "bias": bias, "mae": 4, "rmse": 5, "stability": 2,
            "observations": observations, "windows": windows,
            "evaluation_signature": signature, "evidence_mode": "RETROSPECTIVE_TRAINING",
            "horizons": [{"horizon": h, "target_period": f"2027-{h:02d}", "value": h1}
                         for h in range(1, 13)],
            "by_horizon": [{"horizon": h, "wape": wape} for h in range(1, 13)]}


class SelectionPolicyTests(unittest.TestCase):
    def test_policy_version_and_stat_suggestion_never_champion(self):
        self.assertEqual(SELECTION_POLICY.version, "FT-SELECTION-2.0")
        result = suggest_reference([candidate(), candidate("ml", "Gradient Boosting", 14.2, 0.61)])
        self.assertEqual(result["model"], "SBA")
        self.assertEqual(result["status"], "SUGGESTED_RETROSPECTIVE")
        self.assertEqual(result["confidence"], "Alta")
        self.assertFalse(result["official"])

    def test_ml_or_ensemble_may_win_only_on_same_evidence(self):
        statistical = candidate()
        ml = candidate("ml", "Gradient Boosting", 10, 0.3)
        self.assertEqual(suggest_reference([statistical, ml])["model"], "Gradient Boosting")
        ensemble = candidate("ensemble", "80/20", 9, 0.4)
        self.assertEqual(suggest_reference([statistical, ml, ensemble])["family"], "ensemble")
        ensemble["evaluation_signature"] = "scope-only"
        self.assertEqual(suggest_reference([statistical, ml, ensemble])["family"], "ml")
        self.assertIn("ENSEMBLE_NOT_COMPARABLE", suggest_reference([statistical, ml, ensemble])["reason_codes"])

    def test_tie_bias_and_insufficient_fail_closed(self):
        base = candidate()
        self.assertEqual(suggest_reference([base, candidate("ml", "RF", 13.10, 0.1)])["model"], "SBA")
        self.assertIn("PRACTICAL_TIE_BASELINE_PREFERRED", suggest_reference([base, candidate("ml", "RF", 13.10, 0.1)])["reason_codes"])
        self.assertEqual(suggest_reference([base, candidate("ml", "RF", 10, 6)])["model"], "SBA")
        self.assertEqual(suggest_reference([candidate(observations=5)])["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(suggest_reference([base])["status"], "SUGGESTED_ONLY_ELIGIBLE")
        broken = copy.deepcopy(base); broken["horizons"].pop()
        self.assertEqual(suggest_reference([broken])["status"], "INSUFFICIENT_EVIDENCE")

    def test_critical_horizon_and_signature(self):
        base = candidate()
        ml = candidate("ml", "RF", 10, 0.1)
        ml["by_horizon"][0]["wape"] = 16
        self.assertEqual(suggest_reference([base, ml])["model"], "SBA")
        rows = [{"origin": "2025-01", "target_period": "2025-02", "horizon": 1}]
        first = evaluation_signature(chain_id="Walmart:BD", category_id="Toalla", product_id="FENDI-AZUL",
            objective="Venta", issue_period="2026-07", evaluation_mode="RETROSPECTIVE_EVALUATION", rows=rows)
        other = evaluation_signature(chain_id="Walmart:BD", category_id="Toalla", product_id="FENDI-AZUL",
            objective="Venta", issue_period="2026-07", evaluation_mode="RETROSPECTIVE_EVALUATION",
            rows=[{**rows[0], "target_period": "2025-03"}])
        self.assertNotEqual(first, other)
        physical = evaluation_signature(chain_id="Walmart:BD", category_id="Toalla", product_id="FENDI-AZUL",
            objective="Venta", issue_period="2026-07", evaluation_mode="RETROSPECTIVE_EVALUATION",
            rows=[{**rows[0], "actual_observation_id": "observation-123"}])
        self.assertNotEqual(first, physical)

    def test_band_hierarchy_never_uses_other_horizons(self):
        rows = [{"product_id": "A", "category": "X", "horizon": 1}] * 2 + [
            {"product_id": "B", "category": "X", "horizon": 1}] + [
            {"product_id": "C", "category": "Y", "horizon": 1}] + [
            {"product_id": "A", "category": "X", "horizon": 8}] * 10
        self.assertEqual(select_band_rows(rows, product_id="A", category="X", horizon=1)[0], "CATEGORY")
        self.assertEqual(select_band_rows(rows, product_id="C", category="Y", horizon=1)[0], "CHAIN")
        self.assertEqual(select_band_rows(rows, product_id="A", category="X", horizon=8)[0], "PRODUCT")
        self.assertEqual(select_band_rows(rows, product_id="A", category="X", horizon=12), ("INSUFFICIENT", []))


if __name__ == "__main__":
    unittest.main()
