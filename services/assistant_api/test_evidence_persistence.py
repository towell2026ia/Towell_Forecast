from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.assistant_api.evidence_registry import EvidenceRegistry, VintageRegistry
from services.assistant_api.persistence import LocalPersistenceProvider, SupabasePersistenceProvider
from services.assistant_api.historical_runner import HistoricalForecastRunner
from services.assistant_api.test_historical_runner import MemoryProvider, source_rows


class PersistenceTests(unittest.TestCase):
    def test_immutable_source_evidence_and_append_only_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPersistenceProvider(Path(directory) / "state.db")
            store.put("source_evidence", "E3-1", {"level": "E3", "sha256": "a" * 64})
            store.put("source_evidence", "E3-1", {"level": "E3", "sha256": "a" * 64})
            with self.assertRaisesRegex(ValueError, "immutable_persistence_conflict"):
                store.put("source_evidence", "E3-1", {"level": "E2", "sha256": "a" * 64})
            registry = EvidenceRegistry(store, None)  # external addition does not need an auditor
            external = {"source_id": "SRC-1", "sha256": "a" * 64,
                                      "evidence_date": "2026-08-12", "evidence_level": "E2",
                                      "evidence_reference": "mailbox-message-id:123"}
            with self.assertRaisesRegex(ValueError, "external_evidence_not_verified"):
                registry.append_external(external, lambda _: False)
            registry.append_external(external, lambda _: True)
            self.assertEqual(len(store.list("source_evidence")), 2)
            self.assertEqual(store.get("source_evidence", "E3-1")["level"], "E3")

    def test_vintage_lifecycle_is_one_way(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPersistenceProvider(Path(directory) / "state.db")
            registry = VintageRegistry(store, Path(directory))
            with self.assertRaisesRegex(ValueError, "invalid_vintage_transition"):
                registry.transition("V-1", "FROZEN")
            registry.transition("V-1", "CANDIDATE")
            registry.transition("V-1", "TEMPORALLY_VALID")
            registry.transition("V-1", "VALIDATED", evidence_manifest_id="E3-1",
                                validation_status="VALID_WITH_DOCUMENTED_LOCAL_EVIDENCE")
            registry.transition("V-1", "FROZEN")
            with self.assertRaisesRegex(ValueError, "invalid_vintage_transition"):
                registry.transition("V-1", "VALIDATED")
            self.assertEqual(registry.get("V-1")["status"], "FROZEN")

    def test_supabase_adapter_fails_closed(self) -> None:
        with self.assertRaisesRegex(NotImplementedError, "not_connected"):
            SupabasePersistenceProvider().put("source_evidence", "one", {})

    def test_expansion_without_evidence_creates_no_vintage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = HistoricalForecastRunner(MemoryProvider(source_rows()),
                                              state_dir=Path(directory) / "state")
            result = runner.expand_vintages(Path(directory), "2024-07", "2024-08")
            self.assertEqual(result["new_vintage_ids"], [])
            self.assertEqual(len(result["blocked"]), 2)
            self.assertEqual(runner.vintage_registry.all(), [])


if __name__ == "__main__":
    unittest.main()
