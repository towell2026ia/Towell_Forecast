from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

from services.assistant_api.rs_research import RSResearch

SCOPE = {
    "chain_id": "6c07c931-342f-534e-a5fa-f69a2a8f966c", "format": "BD", "model": "FENDI",
    "period": "2026-10", "source_hash": "a" * 64, "purchase_type": "RS",
    "public_product_type": "TOALLA",
}
COMPLETED = {
    "status": "COMPLETED", "summary": "Contexto público de la categoría.",
    "sources": [{"title": "Informe público", "url": "https://www.inegi.org.mx/temas"}],
    "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "web_search_calls": 2},
}


class RSResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = SimpleNamespace(path=Path(temporary.name) / "research.sqlite")
        self.provider = Mock()
        self.provider.start_public.return_value = {"status": "PENDING", "response_id": "resp_fixture"}
        self.provider.poll.return_value = COMPLETED
        self.clock = datetime(2026, 11, 10, 12, tzinfo=timezone.utc).timestamp()
        self.research = RSResearch(self.storage, self.provider, now=lambda: self.clock)

    def test_get_cache_and_unknown_status_never_create_provider_request(self):
        self.assertEqual(self.research.cached(SCOPE), {"status": "NOT_REQUESTED"})
        self.assertEqual(self.research.status(SCOPE), {"status": "NOT_REQUESTED"})
        self.provider.start_public.assert_not_called()
        self.provider.poll.assert_not_called()
        self.research.start(SCOPE)
        self.assertEqual(self.research.cached(SCOPE)["status"], "PENDING")
        self.provider.poll.assert_not_called()
        self.assertEqual(self.research.status(SCOPE)["status"], "COMPLETED")
        self.assertEqual(self.research.cached(SCOPE)["status"], "COMPLETED")
        self.assertEqual(self.provider.start_public.call_count, 1)
        self.assertEqual(self.provider.poll.call_count, 1)

    def test_payload_is_public_static_category_and_validated_month(self):
        self.research.start(SCOPE)
        self.provider.start_public.assert_called_once_with("TEXTILES_TOALLAS_MX", cutoff="2026-10")
        self.assertEqual(self.research.start({**SCOPE, "model": "OTHER", "public_product_type": "TAPETE"})["status"], "PENDING")
        self.provider.start_public.assert_called_with("TEXTILES_TAPETES_MX", cutoff="2026-10")
        self.assertEqual(self.research.start({**SCOPE, "public_product_type": None})["reason"], "PUBLIC_TOPIC_UNVERIFIED")
        self.assertEqual(self.research.start({**SCOPE, "public_product_type": "UNKNOWN"})["reason"], "PUBLIC_TOPIC_UNVERIFIED")
        self.assertEqual(self.research.start({k: v for k, v in SCOPE.items() if k != "public_product_type"})["reason"], "PUBLIC_TOPIC_UNVERIFIED")
        for change in ({"purchase_type": "CE"}, {"period": "2026-13"}, {"chain_id": "private"},
                       {"prompt": "send everything"}, {"source_hash": "latest"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.research.start({**SCOPE, **change})

    def test_dedup_survives_recreation_and_normalizes_scope(self):
        first = self.research.start(SCOPE)
        second = RSResearch(self.storage, self.provider, now=lambda: self.clock)
        self.assertEqual(second.start({**SCOPE, "model": " fendi ", "format": " bd "}), first)
        self.provider.start_public.assert_called_once()
        second.status(SCOPE)
        another = RSResearch(self.storage, self.provider, now=lambda: self.clock)
        self.assertEqual(another.start(SCOPE)["status"], "COMPLETED")
        self.provider.start_public.assert_called_once()

    def test_context_boundaries_have_distinct_cache_keys(self):
        self.research.start(SCOPE)
        for change in ({"period": "2026-09"}, {"format": "BA"}, {"chain_id": "00000000-0000-0000-0000-000000000001"}):
            self.assertEqual(self.research.cached({**SCOPE, **change}), {"status": "NOT_REQUESTED"})
        self.assertEqual(self.research.cached({**SCOPE, "source_hash": "b" * 64}), {"status": "NOT_REQUESTED"})

    def test_concurrent_instances_reserve_only_once(self):
        def start(_):
            service = RSResearch(self.storage, self.provider, now=lambda: self.clock)
            return service.start(SCOPE)
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(start, range(12)))
        self.assertTrue(all(row["status"] == "PENDING" for row in results))
        self.provider.start_public.assert_called_once()

    def test_completed_runs_do_not_fill_calendar_quotas(self):
        for index in range(125):
            scope = {**SCOPE, "format": f"FORMAT-{index}"}
            self.assertEqual(self.research.start(scope)["status"], "PENDING")
            self.assertEqual(self.research.status(scope)["status"], "COMPLETED")
        self.assertEqual(self.provider.start_public.call_count, 125)
        self.assertEqual(self.provider.poll.call_count, 125)

    def test_distinct_concurrent_scopes_share_slots_and_busy_can_retry(self):
        def start(index):
            service = RSResearch(self.storage, self.provider, max_pending=2, now=lambda: self.clock)
            return service.start({**SCOPE, "format": f"FORMAT-{index}"})
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(start, range(12)))
        self.assertEqual(sum(row["status"] == "PENDING" for row in results), 2)
        self.assertEqual(self.provider.start_public.call_count, 2)
        pending_index = next(index for index, row in enumerate(results) if row["status"] == "PENDING")
        busy_index = next(index for index, row in enumerate(results) if row.get("reason") == "BUSY")
        self.assertEqual(results[busy_index], {"status": "UNAVAILABLE", "reason": "BUSY", "retryable": True})
        self.assertEqual(self.research.cached({**SCOPE, "format": f"FORMAT-{busy_index}"}), {"status": "NOT_REQUESTED"})
        self.research.status({**SCOPE, "format": f"FORMAT-{pending_index}"})
        self.assertEqual(start(busy_index)["status"], "PENDING")
        self.assertEqual(self.provider.start_public.call_count, 3)

    def test_polling_is_bounded_and_result_hides_response_id(self):
        service = RSResearch(self.storage, self.provider, max_polls=2, now=lambda: self.clock)
        self.provider.poll.return_value = {"status": "PENDING", "response_id": "resp_fixture"}
        self.assertNotIn("resp_fixture", json.dumps(service.start(SCOPE)))
        service.status(SCOPE)
        service.status(SCOPE)
        self.assertEqual(self.provider.poll.call_count, 1)
        self.clock += 16
        service.status(SCOPE)
        self.clock += 16
        self.assertEqual(service.status(SCOPE)["reason"], "POLL_LIMIT")
        self.assertEqual(self.provider.poll.call_count, 2)

    def test_uncertain_start_is_not_retried_and_no_secret_error_is_returned(self):
        self.provider.start_public.side_effect = RuntimeError("secret sk-private-token and private customer")
        result = self.research.start(SCOPE)
        self.assertEqual(result["reason"], "PROVIDER_ERROR")
        self.assertNotIn("secret", json.dumps(result))
        self.research.start(SCOPE)
        self.provider.start_public.assert_called_once()
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)

    def test_expired_pending_releases_slot_without_polling(self):
        self.provider.poll.return_value = {"status": "PENDING"}
        service = RSResearch(self.storage, self.provider, max_pending=1, now=lambda: self.clock)
        other = {**SCOPE, "format": "OTHER"}
        service.start(SCOPE)
        self.assertEqual(service.start(other)["reason"], "BUSY")
        restarted = RSResearch(self.storage, self.provider, max_pending=1, now=lambda: self.clock)
        self.clock += 601
        self.assertEqual(restarted.start(other)["status"], "PENDING")
        self.assertEqual(restarted.status(SCOPE)["reason"], "TIMEOUT")
        self.assertEqual(restarted.start(SCOPE)["reason"], "TIMEOUT")
        self.provider.poll.assert_not_called()
        self.assertEqual(self.provider.start_public.call_count, 2)

    def test_expired_reservation_releases_slot_and_late_response_is_ignored(self):
        service = RSResearch(self.storage, self.provider, max_pending=1, now=lambda: self.clock)
        started, release = Event(), Event()

        def slow_provider(topic, *, cutoff):
            if cutoff == "2026-10":
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test provider timed out")
            return {"status": "PENDING", "response_id": "resp_fixture"}

        self.provider.start_public.side_effect = slow_provider
        next_scope = {**SCOPE, "period": "2026-11"}
        with ThreadPoolExecutor(max_workers=1) as worker:
            first = worker.submit(service.start, SCOPE)
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(service.start(next_scope)["reason"], "BUSY")
                self.clock += 121
                self.assertEqual(service.start(next_scope)["status"], "PENDING")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["reason"], "TIMEOUT")
        self.assertEqual(service.status(SCOPE)["reason"], "TIMEOUT")
        self.assertEqual(self.provider.start_public.call_count, 2)

    def test_uncited_output_is_unavailable_and_usage_is_whitelisted(self):
        self.research.start(SCOPE)
        self.provider.poll.return_value = {"status": "COMPLETED", "summary": "Sin referencia", "sources": []}
        self.assertEqual(self.research.status(SCOPE)["reason"], "NO_PUBLIC_CITATIONS")
        alternate = {**SCOPE, "format": "SECOND"}
        self.research.start(alternate)
        self.provider.poll.return_value = {**COMPLETED, "response_id": "resp_fixture",
                                           "usage": {**COMPLETED["usage"], "private": "secret", "input_tokens": True}}
        result = self.research.status(alternate)
        self.assertEqual(result["evidence_scope"], "PUBLIC_CATEGORY_CONTEXT")
        self.assertFalse(result["source_publication_dates_verified"])
        self.assertEqual(result["usage"], {"output_tokens": 20, "total_tokens": 30, "web_search_calls": 2})
        self.assertNotIn("resp_fixture", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_disabled_service_does_not_create_database(self):
        other = SimpleNamespace(path=self.storage.path.with_name("disabled.sqlite"))
        disabled = RSResearch(other, None)
        self.assertEqual(disabled.cached(SCOPE), {"status": "DISABLED"})
        self.assertEqual(disabled.start(SCOPE), {"status": "DISABLED"})
        self.assertEqual(disabled.status(SCOPE), {"status": "DISABLED"})
        self.assertFalse(other.path.exists())


if __name__ == "__main__":
    unittest.main()
