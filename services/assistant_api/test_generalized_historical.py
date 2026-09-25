"""Synthetic acceptance checks for generic temporal replay and ingestion."""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from services.assistant_api.api import create_app
from services.assistant_api.availability import _identity
from services.assistant_api.data_provider import DataProvider, PersistedObservationProvider
from services.assistant_api.generalized_historical import GeneralizedHistoricalForecastRunner
from services.assistant_api.historical_runner import HistoricalForecastRunner
from services.assistant_api.ingestion import AvailabilitySource, ImportService, resolve_availability
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.runner import GeneralizedMonthlyForecastRunner
from services.assistant_api.settings import Settings


class Rows(DataProvider):
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


ROW = {"chain_id": "CHAIN-X", "product_id": "PRODUCT-1", "product_code": "P-1",
       "description": "Synthetic", "category": "HOME", "variant": "A", "objective": "Venta",
       "period": "2026-08", "value": 10, "is_missing": False}


class GenericHistoricalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.storage = LocalPersistenceProvider(self.root / "state.sqlite3")

    def runner(self, rows, **kwargs):
        return GeneralizedHistoricalForecastRunner(Rows(rows), self.storage,
            state_dir=self.root / "generic", **kwargs)

    def test_default_runtime_uses_only_generic_runners(self):
        app = create_app(provider=Rows([]), settings=Settings(state_dir=self.root),
                         persistence=self.storage)
        self.assertIsInstance(app.state.forecast_runner, GeneralizedMonthlyForecastRunner)
        self.assertIsInstance(app.state.historical, GeneralizedHistoricalForecastRunner)
        self.assertIsNone(app.state.legacy_historical)
        self.assertFalse(app.state.settings.legacy_pilot_enabled)
        self.assertNotIsInstance(app.state.historical, HistoricalForecastRunner)
        self.assertEqual(PersistedObservationProvider(Rows([]), self.storage).name,
                         "normalized_observations")
        api = TestClient(create_app(provider=Rows([]), settings=replace(
            Settings(state_dir=self.root), ai_assistant_api_enabled=False),
            persistence=self.storage))
        response = api.post("/api/forecast/run", headers={"x-actor-id": "local-manager",
            "idempotency-key": "generic-run-audit"},
            json={"period": "2026-09", "chain_id": "CHAIN-X", "objective": "Venta"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(api.post("/api/historical/first-vintage",
            headers={"x-actor-id": "local-manager", "idempotency-key": "legacy-audit"}).status_code, 503)
        self.assertEqual(api.post("/api/historical/run", headers={"x-actor-id": "local-manager",
            "idempotency-key": "generic-historical-audit"}, json={"chain_id": "CHAIN-X",
            "start_period": "2026-08", "end_period": "2026-08", "objective": "Venta"}).status_code, 503)

    def test_new_import_records_actual_receipt_not_period_or_workbook_date(self):
        received = datetime(2026, 9, 4, 10, 32, 16, tzinfo=timezone.utc)
        service = ImportService(self.storage, clock=lambda: received)
        batch = service.ingest(b"synthetic upload", source_name="monthly", filename="aug.csv",
                               period="2026-08", uploaded_by="tester",
                               rows=[{**ROW, "available_at": "2026-08-31"}])
        persisted = self.storage.list("normalized_observations")[0]
        self.assertEqual(batch.uploaded_at, "2026-09-04T10:32:16Z")
        self.assertEqual(persisted["available_at"], batch.uploaded_at)
        self.assertEqual(persisted["availability_source"], AvailabilitySource.SYSTEM_INGESTION)
        self.assertEqual(persisted["import_batch_id"], batch.batch_id)
        self.assertEqual(batch.sha256, __import__("hashlib").sha256(b"synthetic upload").hexdigest())
        self.assertEqual(self.runner([persisted]).run("CHAIN-X", "2026-08", "2026-08")["status"],
                         "BLOCKED_AVAILABILITY")
        with patch.object(GeneralizedMonthlyForecastRunner, "run_month",
                          return_value={"state": "Completed", "run_id": "RUN-TEST"}) as monthly:
            result = self.runner([persisted]).run("CHAIN-X", "2026-09", "2026-09")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(monthly.call_args.kwargs["chain_id"], "CHAIN-X")
        wrapper = PersistedObservationProvider(Rows([]), self.storage)
        self.assertEqual(len(wrapper.records()), 1)
        self.assertEqual(wrapper.load("unused"), {})
        self.assertEqual(wrapper.decisions(), [])
        self.assertEqual(wrapper.vintages(), [])
        self.assertEqual(wrapper.health()["status"], "healthy")
        audit = self.runner([persisted]).audit_availability("2026-08", "2026-09", "CHAIN-X")
        self.assertEqual(audit["periods_audited"], 2)
        self.assertEqual(audit["ready_count"], 1)
        self.assertEqual(self.runner([persisted]).find_first_temporally_valid_period(
            "2026-08", "2026-09", "CHAIN-X")["period"], "2026-09")

    def test_unknown_history_stays_blocked(self):
        row = {**ROW, "available_at": "2026-08-31"}  # A naked CSV timestamp is not trusted.
        self.assertIsNone(resolve_availability(row, self.storage))
        result = self.runner([row]).run("CHAIN-X", "2026-08", "2026-08")
        self.assertEqual(result["status"], "BLOCKED_AVAILABILITY")
        self.assertEqual(result["runs"][0]["blocked"][0]["reason"], "UNKNOWN_AVAILABILITY")
        with self.assertRaisesRegex(ValueError, "invalid_period_range"):
            self.runner([row]).run("CHAIN-X", "2026-09", "2026-08")
        with self.assertRaisesRegex(ValueError, "unsupported_forecast_objective"):
            self.runner([row]).run("CHAIN-X", "2026-08", "2026-08", objective="Other")
        with self.assertRaisesRegex(ValueError, "unsupported_execution_cutoff"):
            self.runner([row]).validate_temporal_readiness("2026-08", cutoff="2026-08-01")
        self.assertIsNone(self.runner([row]).find_first_temporally_valid_period(
            "2026-08", "2026-08"))
        api = TestClient(create_app(provider=Rows([row]), settings=Settings(state_dir=self.root),
                                    persistence=self.storage))
        headers = {"x-actor-id": "local-manager", "idempotency-key": "unknown-history-audit"}
        request = {"chain_id": "CHAIN-X", "start_period": "2026-08",
                   "end_period": "2026-08", "objective": "Venta"}
        response = api.post("/api/historical/run", headers=headers, json=request)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(api.post("/api/historical/run", headers=headers,
                                  json=request).json()["run_id"], response.json()["run_id"])
        for _ in range(30):
            job = api.get(f"/api/historical/jobs/{response.json()['run_id']}",
                          headers=headers).json()
            if job["status"] != "QUEUED" and job["status"] != "RUNNING":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "BLOCKED_AVAILABILITY")

    def test_import_metadata_is_strict(self):
        service = ImportService(self.storage, clock=lambda: datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "invalid_import_metadata"):
            service.ingest(b"", source_name="x", filename="x.csv", period="2026-08",
                           uploaded_by="user", rows=[ROW])
        with self.assertRaisesRegex(ValueError, "invalid_normalized_observation"):
            service.ingest(b"x", source_name="x", filename="x.csv", period="2026-08",
                           uploaded_by="user", rows=[{**ROW, "period": "2026-07"}])
        with self.assertRaisesRegex(ValueError, "invalid_missing_marker"):
            service.ingest(b"x", source_name="x", filename="x.csv", period="2026-08",
                           uploaded_by="user", rows=[{**ROW, "is_missing": "maybe"}])
        with self.assertRaisesRegex(ValueError, "ingestion_clock_must_be_aware"):
            ImportService(self.storage, clock=lambda: datetime(2026, 9, 4)).ingest(
                b"x", source_name="x", filename="x.csv", period="2026-08",
                uploaded_by="user", rows=[ROW])

    def test_only_registered_evidence_can_resolve_legacy_row(self):
        source = {**ROW, "source_sha256": "a" * 64}
        resolved = {**source, "source_sha256": "a" * 64,
                    "availability_confidence": "documented",
                    "availability_rule_id": "TEST_V1", "available_at": "2026-08-25T00:00:00Z"}
        class Auditor:
            def resolve(self, row):
                return resolved
            def _assignments(self):
                return {_identity(source): {"available_at": resolved["available_at"],
                                            "source_sha256": resolved["source_sha256"]}}
        self.assertIsNone(resolve_availability(source, self.storage, legacy_auditor=Auditor()))
        self.storage.put("source_evidence", "EM-TEST", {
            "evidence_manifest_id": "EM-TEST", "evidence_level": "E3", "sha256": "a" * 64,
            "evidence_date": "2026-08-25", "rule_id": "TEST_V1",
            "verified_cells": 1, "review": "synthetic documented review"})
        eligible = resolve_availability(source, self.storage, legacy_auditor=Auditor())
        self.assertEqual(eligible["availability_source"], AvailabilitySource.LEGACY_EVIDENCE)
        self.assertEqual(eligible["availability_evidence_id"], "EM-TEST")
        self.assertTrue(self.runner([source], legacy_auditor=Auditor()).validate_temporal_readiness(
            "2026-08", chain="CHAIN-X")["ready"])


if __name__ == "__main__":
    unittest.main()
