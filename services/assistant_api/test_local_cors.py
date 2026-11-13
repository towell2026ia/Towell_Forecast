"""Synthetic CORS preflight checks; no PostgreSQL, hosted Auth or network calls."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from services.assistant_api.api import create_app
from services.assistant_api.local_hosted_auth import AUTH_URL
from services.assistant_api.settings import Settings


class LocalCorsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = replace(Settings(), app_env="test", frontend_url="http://127.0.0.1:5173",
            state_dir=root, data_provider="supabase", supabase_enabled=True, operational_preview_enabled=True,
            supabase_url="http://127.0.0.1:8000", supabase_publishable_key="sb_publishable_synthetic_data_only",
            local_postgres_config=root / "not-read-config.json", local_supabase_auth_url=AUTH_URL,
            local_supabase_auth_publishable_key="sb_publishable_synthetic_auth_only", ai_assistant_api_enabled=False)

    def app(self, config=None):
        def unexpected_network(_request):
            raise AssertionError("CORS preflight must not contact Auth or data services")
        self.gateway = Mock(transport=httpx.MockTransport(unexpected_network))
        with patch("services.assistant_api.local_postgres.LocalPostgresGateway", return_value=self.gateway):
            return create_app(provider=SimpleNamespace(state_dir=Path(self.temp.name)),
                settings=config or self.config, auth_provider=Mock())

    def preflight(self, client, *, origin="http://127.0.0.1:5173", method="GET", headers="authorization,apikey,x-retry-count"):
        return client.options("/rest/v1/forecast_live_evaluations", headers={
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers})

    def test_postgrest_get_and_head_retry_preflight_accepts_exact_local_origin(self):
        with TestClient(self.app()) as client:
            for method in ("GET", "HEAD"):
                with self.subTest(method=method):
                    response = self.preflight(client, method=method)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["access-control-allow-origin"], self.config.frontend_url)
                    allowed = {header.strip().casefold() for header in response.headers["access-control-allow-headers"].split(",")}
                    self.assertTrue({"authorization", "apikey", "x-retry-count"} <= allowed)
                    self.assertNotIn("access-control-allow-credentials", response.headers)
        self.gateway.handle.assert_not_called()

    def test_retry_preflight_does_not_expand_origin_allowlist(self):
        with TestClient(self.app()) as client:
            for origin in ("https://foreign.example", "http://localhost:5173", "http://127.0.0.1:5174", "null"):
                with self.subTest(origin=origin):
                    response = self.preflight(client, origin=origin)
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn("access-control-allow-origin", response.headers)
        self.gateway.handle.assert_not_called()

    def test_unknown_header_and_mutating_delete_preflight_remain_rejected(self):
        with TestClient(self.app()) as client:
            self.assertEqual(self.preflight(client, headers="authorization,apikey,x-retry-count,x-unapproved-header").status_code, 400)
            self.assertEqual(self.preflight(client, method="DELETE").status_code, 400)
        self.gateway.handle.assert_not_called()

    def test_retry_header_is_not_enabled_for_nonlocal_deployment(self):
        remote = replace(self.config, local_postgres_config=None, local_supabase_auth_url="",
            local_supabase_auth_publishable_key="", supabase_url=AUTH_URL)
        with TestClient(self.app(remote)) as client:
            response = self.preflight(client)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("x-retry-count", response.headers["access-control-allow-headers"].casefold())


if __name__ == "__main__":
    unittest.main()
