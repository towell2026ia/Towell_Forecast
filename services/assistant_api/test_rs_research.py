from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from services.assistant_api.rs_research import RSResearch

SCOPE = {
    "chain_id": "6c07c931-342f-534e-a5fa-f69a2a8f966c", "format": "BD", "model": "FENDI",
    "period": "2026-10", "version_id": "47242c4b-79c9-453f-8548-0b9781c35012", "purchase_type": "RS",
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
                       {"prompt": "send everything"}, {"version_id": "latest"}):
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
        for change in ({"period": "2026-09"}, {"format": "BA"}, {"chain_id": SCOPE["version_id"]}):
            self.assertEqual(self.research.cached({**SCOPE, **change}), {"status": "NOT_REQUESTED"})
        self.assertEqual(self.research.cached({**SCOPE, "version_id": SCOPE["chain_id"]}), {"status": "NOT_REQUESTED"})

    def test_concurrent_instances_reserve_only_once(self):
        def start(_):
            service = RSResearch(self.storage, self.provider, now=lambda: self.clock)
            return service.start(SCOPE)
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(start, range(12)))
        self.assertTrue(all(row["status"] == "PENDING" for row in results))
        self.provider.start_public.assert_called_once()

    def test_global_daily_monthly_budgets_are_persistent(self):
        service = RSResearch(self.storage, self.provider, daily_limit=2, monthly_limit=3, now=lambda: self.clock)
        service.start(SCOPE)
        service.start({**SCOPE, "format": "FORMATO-2"})
        self.assertEqual(service.start({**SCOPE, "format": "FORMATO-3"})["reason"], "BUDGET_EXHAUSTED")
        self.clock += 86400
        restarted = RSResearch(self.storage, self.provider, daily_limit=2, monthly_limit=3, now=lambda: self.clock)
        restarted.start({**SCOPE, "format": "FORMATO-3"})
        self.assertEqual(restarted.start({**SCOPE, "format": "FORMATO-4"})["reason"], "BUDGET_EXHAUSTED")
        self.assertEqual(self.provider.start_public.call_count, 3)

    def test_distinct_concurrent_scopes_share_start_budget(self):
        def start(index):
            service = RSResearch(self.storage, self.provider, daily_limit=2, monthly_limit=3, now=lambda: self.clock)
            return service.start({**SCOPE, "format": f"FORMAT-{index}"})
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(start, range(12)))
        self.assertEqual(sum(row["status"] == "PENDING" for row in results), 2)
        self.assertEqual(self.provider.start_public.call_count, 2)

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

    def test_polling_budget_and_deadline_apply_across_instances(self):
        self.provider.poll.return_value = {"status": "PENDING"}
        service = RSResearch(self.storage, self.provider, global_daily_polls=1, now=lambda: self.clock)
        other = {**SCOPE, "format": "OTHER"}
        service.start(SCOPE)
        service.start(other)
        service.status(SCOPE)
        restarted = RSResearch(self.storage, self.provider, global_daily_polls=1, now=lambda: self.clock)
        self.assertEqual(restarted.status(other)["status"], "PENDING")
        self.assertEqual(self.provider.poll.call_count, 1)
        self.clock += 601
        self.assertEqual(restarted.status(other)["reason"], "TIMEOUT")
        self.assertEqual(restarted.start(other)["reason"], "TIMEOUT")
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
