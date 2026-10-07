from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from services.assistant_api.cold_start_research import ColdStartResearch
from services.assistant_api.settings import Settings


class ResearchTests(unittest.TestCase):
    def test_feature_is_opt_in_and_key_stays_out_of_repr(self):
        self.assertFalse(Settings().openai_enabled)
        self.assertFalse(Settings().deep_research_enabled)
        with self.assertRaises(ValueError):
            replace(Settings(), openai_enabled=True, deep_research_enabled=True, openai_api_key="sk-test-only").validate()
        self.assertNotIn("sk-test-only", repr(replace(Settings(), openai_api_key="sk-test-only")))
    def test_start_sends_public_descriptors_only_and_no_sales(self):
        research = ColdStartResearch("sk-test-only")
        with patch.object(research, "_request", return_value={"id": "resp_test"}) as send:
            result = research.start(description="Toalla MB Oxford", category="Toallas", chain_name="Walmart")
        self.assertEqual(result["status"], "PENDING")
        method, path, payload = send.call_args.args
        self.assertEqual((method, path), ("POST", "/responses"))
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertGreaterEqual(payload["max_output_tokens"], 3000)
        self.assertNotIn("sk-test", str(payload))
        self.assertNotIn("ventas", payload["input"].lower())
        self.assertIn("Do not estimate quantities", payload["input"])

    def test_poll_keeps_citations_but_rejects_unsafe_urls(self):
        research = ColdStartResearch("sk-test-only")
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Hay lanzamiento público.", "annotations": [
                {"url": "https://example.com/product", "title": "Producto"},
                {"url": "https://user:password@example.com/private", "title": "Unsafe"},
            ]}]}]}
        with patch.object(research, "_request", return_value=response):
            result = research.poll("resp_test")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["sources"], [{"title": "Producto", "url": "https://example.com/product"}])
        self.assertEqual(research.poll("../../private"), {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"})

    def test_failed_start_does_not_block_numeric_forecast(self):
        research = ColdStartResearch("sk-test-only")
        with patch.object(research, "_request", side_effect=ValueError("upstream secret")):
            self.assertEqual(research.start(description="Toalla", category="Textil", chain_name="Cadena"), {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"})

    def test_provider_failure_exposes_only_safe_category(self):
        research = ColdStartResearch("sk-test-only")
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        for status, code, expected in [(401, "invalid_api_key", "AUTHENTICATION"),
                                       (429, "insufficient_quota", "QUOTA"),
                                       (429, "rate_limit_exceeded", "RATE_LIMIT"),
                                       (403, "model_not_found", "MODEL_ACCESS")]:
            response = httpx.Response(status, request=request, json={"error": {"code": code, "message": "private upstream secret"}})
            failure = httpx.HTTPStatusError("private upstream secret", request=request, response=response)
            with patch.object(research, "_request", side_effect=failure):
                result = research.start(description="Toalla", category="Textil", chain_name="Cadena")
            self.assertEqual(result, {"status": "UNAVAILABLE", "reason": expected})
            self.assertNotIn("private", str(result))

    def test_incomplete_or_empty_response_has_explicit_reason(self):
        research = ColdStartResearch("sk-test-only")
        for response, reason in [({"status": "incomplete"}, "INCOMPLETE"),
                                 ({"status": "completed", "output": []}, "EMPTY_RESPONSE")]:
            with patch.object(research, "_request", return_value=response):
                self.assertEqual(research.poll("resp_test"), {"status": "UNAVAILABLE", "reason": reason})


if __name__ == "__main__":
    unittest.main()
