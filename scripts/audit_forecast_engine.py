"""Reproducible, synthetic PRD 09.1B certification gate. No private data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.forecast_engine import forecast_dataset  # noqa: E402
from services.forecast_engine.test_engine import synthetic_rows  # noqa: E402


def check() -> dict[str, bool]:
    rows = synthetic_rows()
    incumbent = {"strategy": "ml", "statistical_weight": 0.0,
                 "version": "PREVIOUS-ML", "certified_wape": 15.0}
    result = forecast_dataset(rows, "2024-04", incumbent=incumbent)
    chains = result["chains"]
    def rejects(changed: list[dict], *, research: dict | None = None) -> bool:
        try:
            forecast_dataset(changed, "2024-04", research=research)
        except ValueError:
            return True
        return False
    try:
        leakage = rejects([{**rows[0], "available_at": "2024-05-01"}, *rows[1:]])
        research_leakage = rejects(rows, research={"cutoff_date": "2024-04-30",
                                                   "sources": [{"published_at": "2024-05-01"}]})
    except Exception:
        leakage = research_leakage = False
    checks = {
        "Temporal leakage": leakage,
        "Research leakage": research_leakage,
        "Model isolation": all(chain["model_audit"]["training_range"][1] <
                               chain["model_audit"]["validation_range"][0] <
                               chain["model_audit"]["certification_range"][0] and
                               set(chain["model_audit"]["validation_targets"]).isdisjoint(
                                   chain["model_audit"]["certification_targets"]) for chain in chains),
        "Product granularity": {chain["chain_id"] for chain in chains} == {"CHAIN-A", "CHAIN-B"}
        and all(len(chain["forecast_towell"]) == 24 for chain in chains),
        "12 horizons": all({row["horizon"] for row in chain["forecast_towell"]} == set(range(1, 13))
                           for chain in chains),
        "Champion continuity": all(chain["selection"]["incumbent"]["version"] == "PREVIOUS-ML"
                                   and not chain["selection"]["automatic_promotion"] for chain in chains),
        "Common backtest": all(chain["model_audit"]["observations"]["common_selection"] > 0
                               and chain["model_audit"]["observations"]["common_certification"] > 0
                               for chain in chains),
        "Certified metrics": all(chain["certification_status"] == "CERTIFIED"
                                 and chain["model_audit"]["certified_wape"] is not None
                                 and len(chain["by_horizon"]) == 12 for chain in chains),
        "Aggregation reconcile": all(
            round(sum(row["forecast_towell"] for row in chain["forecast_towell"]
                      if row["horizon"] == horizon), 2) ==
            next(row["forecast_towell"] for row in chain["aggregates"]
                 if row["level"] == "chain" and row["horizon"] == horizon)
            for chain in chains for horizon in range(1, 13)),
        "Cold start": all(row["forecast_status"] == "COLD_START"
                          and row["certification_status"] == "PROVISIONAL"
                          for chain in chains for row in chain["forecast_towell"]
                          if row["product_id"] == "NEW"),
        "Empirical bands": all(row["probability"] and row["band_observations"] > 0
                               and row["probability"]["p10"] <= row["probability"]["p50"] <=
                               row["probability"]["p90"] <= row["probability"]["p95"]
                               for chain in chains for row in chain["forecast_towell"]),
        "Generic core names": not any(name in (ROOT / relative).read_text(encoding="utf-8").casefold()
                                      for relative in ("services/forecast_engine/engine.py",
                                                       "services/forecast_engine/registry.py",
                                                       "services/forecast_engine/identifiers.py")
                                      for name in ("fendi", "walmart")),
        "Reproducibility": result == forecast_dataset(rows, "2024-04", incumbent=incumbent),
    }
    return checks


if __name__ == "__main__":
    results = check()
    print("FORECAST ENGINE AUDIT\n---------------------")
    for name, passed in results.items():
        print(f"{name:.<28} {'PASS' if passed else 'FAIL'}")
    passed = all(results.values())
    print(f"\nFINAL STATUS: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)
