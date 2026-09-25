"""Reproducible, synthetic PRD 09.1B certification gate. No private data."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.forecast_engine import forecast_dataset  # noqa: E402
from services.forecast_engine.test_engine import synthetic_rows  # noqa: E402
from services.assistant_api.api import create_app  # noqa: E402
from services.assistant_api.availability import _identity  # noqa: E402
from services.assistant_api.data_provider import DataProvider  # noqa: E402
from services.assistant_api.generalized_historical import GeneralizedHistoricalForecastRunner  # noqa: E402
from services.assistant_api.historical_runner import HistoricalForecastRunner  # noqa: E402
from services.assistant_api.ingestion import (  # noqa: E402
    AvailabilitySource, ImportService, REQUIRED_OBSERVATION_FIELDS, resolve_availability,
)
from services.assistant_api.persistence import LocalPersistenceProvider  # noqa: E402
from services.assistant_api.runner import GeneralizedMonthlyForecastRunner  # noqa: E402
from services.assistant_api.settings import Settings  # noqa: E402


class _Rows(DataProvider):
    def __init__(self, rows):
        self._rows = rows

    @property
    def name(self):
        return "synthetic"

    def load(self, name):
        return {}

    def records(self):
        return self._rows

    def decisions(self):
        return []

    def vintages(self):
        return []


def runtime_checks() -> dict[str, bool]:
    row = {"chain_id": "CHAIN-X", "product_id": "ITEM-X", "product_code": "X",
           "description": "Synthetic", "category": "HOME", "variant": "",
           "objective": "Venta", "period": "2026-08", "value": 10, "is_missing": False}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        storage = LocalPersistenceProvider(root / "state.sqlite3")
        app = create_app(provider=_Rows([]), persistence=storage, settings=Settings(state_dir=root))
        received = datetime(2026, 9, 4, 10, 32, 16, tzinfo=timezone.utc)
        batch = ImportService(storage, clock=lambda: received).ingest(
            b"synthetic", source_name="monthly", filename="source.csv",
            period="2026-08", uploaded_by="audit", rows=[{**row, "available_at": "2026-08-31"}])
        imported = storage.list("normalized_observations")[0]
        unknown = {**row, "available_at": "2026-08-31"}
        historical = GeneralizedHistoricalForecastRunner(_Rows([unknown]), storage,
                                                          state_dir=root / "historical")
        unknown_blocked = historical.run("CHAIN-X", "2026-08", "2026-08")["status"] == "BLOCKED_AVAILABILITY"
        resolved = {**row, "source_sha256": "a" * 64, "availability_rule_id": "TEST_V1",
                    "availability_confidence": "documented", "available_at": "2026-08-25T00:00:00Z"}
        class Auditor:
            def resolve(self, source):
                return resolved
            def _assignments(self):
                return {_identity(source): {"available_at": resolved["available_at"],
                                            "source_sha256": resolved["source_sha256"]}}
        source = {**row, "source_sha256": "a" * 64}
        unregistered = resolve_availability(source, storage, legacy_auditor=Auditor()) is None
        storage.put("source_evidence", "EM-AUDIT", {
            "evidence_manifest_id": "EM-AUDIT", "evidence_level": "E3",
            "sha256": "a" * 64, "evidence_date": "2026-08-25",
            "rule_id": "TEST_V1", "verified_cells": 1, "review": "synthetic review"})
        evidence = resolve_availability(source, storage, legacy_auditor=Auditor())
        return {
            "No active pilot hardcodes": not any(
                name in (ROOT / relative).read_text(encoding="utf-8").casefold()
                for relative in ("services/assistant_api/api.py",
                                 "services/assistant_api/generalized_historical.py",
                                 "services/forecast_engine/engine.py")
                for name in ("fendi", "walmart", "total-fendi-bd")),
            "Generic monthly runner": isinstance(app.state.forecast_runner, GeneralizedMonthlyForecastRunner),
            "Generic historical runner": isinstance(app.state.historical, GeneralizedHistoricalForecastRunner),
            "Legacy isolation": app.state.legacy_historical is None and not app.state.settings.legacy_pilot_enabled
                                and not isinstance(app.state.historical, HistoricalForecastRunner),
            "Availability contract": REQUIRED_OBSERVATION_FIELDS.issubset(imported)
                                     and imported["available_at"] == batch.uploaded_at
                                     and imported["availability_source"] == AvailabilitySource.SYSTEM_INGESTION,
            "System ingestion availability": batch.uploaded_at == "2026-09-04T10:32:16Z"
                                             and imported["available_at"] != "2026-08-31",
            "Unknown history blocked": unknown_blocked and unregistered,
            "Evidence availability": evidence is not None and
                                     evidence["availability_source"] == AvailabilitySource.LEGACY_EVIDENCE
                                     and evidence["availability_evidence_id"] == "EM-AUDIT",
            "No fabricated historical dates": historical.validate_temporal_readiness("2026-08")["status"] == "BLOCKED_AVAILABILITY"
                                              and resolve_availability(unknown, storage) is None,
        }


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
    checks.update(runtime_checks())
    return checks


if __name__ == "__main__":
    results = check()
    print("FORECAST ENGINE AUDIT\n---------------------")
    for name, passed in results.items():
        print(f"{name:.<28} {'PASS' if passed else 'FAIL'}")
    passed = all(results.values())
    print(f"\nFINAL STATUS: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)
