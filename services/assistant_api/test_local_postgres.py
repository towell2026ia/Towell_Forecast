"""The local adapter cannot change hosted authentication or database targets."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from services.assistant_api.settings import Settings


class LocalPostgresBoundaryTests(unittest.TestCase):
    def local(self):
        return Settings(local_postgres_config=Path(__file__).resolve(),
            data_provider="supabase", supabase_enabled=True, operational_preview_enabled=True,
            supabase_url="http://127.0.0.1:8000", supabase_publishable_key="sb_publishable_local_development_only")

    def test_explicit_loopback_development_is_accepted(self):
        self.assertEqual(self.local().validate().supabase_url, "http://127.0.0.1:8000")

    def test_hosted_processes_and_other_targets_are_rejected(self):
        for changes in ({"app_env": "production"}, {"app_env": "staging"}, {"railway_runtime": True},
                {"api_host": "0.0.0.0"}, {"local_postgres_config": Path("relative.json")},
                *({"supabase_url": url} for url in ("http://localhost:8000", "http://127.0.0.1:9999",
                    "http://user:password@127.0.0.1:8000", "http://127.0.0.1:8000/rest/v1",
                    "http://127.0.0.1:8000?remote=true", "https://example.supabase.co"))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.local(), **changes).validate()

    def test_normal_configuration_still_rejects_http(self):
        with self.assertRaisesRegex(ValueError, "invalid_supabase_url"):
            replace(self.local(), local_postgres_config=None).validate()

    def test_no_local_secret_read_before_production_rejection(self):
        from services.assistant_api.local_postgres import LocalPostgresGateway
        with patch("pathlib.Path.read_text", side_effect=AssertionError("read must not happen")):
            with self.assertRaises(ValueError):
                LocalPostgresGateway(replace(self.local(), railway_runtime=True))
