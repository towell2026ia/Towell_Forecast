from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.assistant_api.api import create_app
from services.assistant_api.auth import LocalAuthProvider, SignedAuthProvider
from services.assistant_api.data_provider import NormalizedDataProvider
from services.assistant_api.orchestrator import LocalAssistantProvider
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.persistence_migration import export_bundle, import_bundle, reconcile, validate_bundle
from services.assistant_api.settings import Settings
from services.assistant_api.test_historical_runner import FakePipeline
from services.assistant_api.runtime import SlidingWindowRateLimiter, retry
from services.assistant_api.future_providers import OpenAIAssistantProvider, OpenAIDeepResearchProvider, DisabledVoiceProvider


class BrokenAssistant(LocalAssistantProvider):
    def health(self) -> dict[str, str]:
        return {"status": "degraded", "provider": "broken"}

    def render(self, intent, data):
        raise RuntimeError("controlled_failure")


class FastForecastPipeline(FakePipeline):
    def ml(self, normalized_csv):
        result = super().ml(normalized_csv)
        result["champion"]["version"] = "ML-TEST"
        return result


def signed_token(secret: str, user_id: str, *, ttl: int = 60, environment: str = "staging",
                 role: str = "manager", audience: str = "forecast-towell-fastapi") -> str:
    now = int(time.time())
    role_permissions = {"manager": ["ADMIN", "EXECUTE", "READ"],
                        "editor": ["EXECUTE", "READ"], "reader": ["READ"]}
    claims = {"sub": user_id, "user_id": user_id, "session_id": "test-session", "iat": now,
              "exp": now + ttl, "aud": audience,
              "iss": f"forecast-towell-frontend:{environment}", "role": role,
              "permissions": role_permissions[role]}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{encoded}.{signature}"


class ProductionReadinessTests(unittest.TestCase):
    def test_settings_fail_closed_for_remote_providers_and_missing_auth(self):
        with self.assertRaisesRegex(ValueError, "future_provider_disabled"):
            replace(Settings(), openai_enabled=True).validate()
        with self.assertRaisesRegex(ValueError, "strict_auth_configuration_missing"):
            replace(Settings(), app_env="production", frontend_url="https://example.com").validate()
        with self.assertRaisesRegex(ValueError, "invalid_frontend_url"):
            replace(Settings(), frontend_url="*").validate()
        production = replace(Settings(), app_env="production", frontend_url="https://forecast.example",
                             api_host="0.0.0.0", assistant_token="s" * 48,
                             manager_ids=frozenset({"manager-1"}),
                             persistence_mode="hosted-volume").validate()
        self.assertTrue(production.strict_auth)
        with self.assertRaisesRegex(ValueError, "host_must_bind_all_interfaces"):
            replace(production, api_host="127.0.0.1").validate()
        with self.assertRaisesRegex(ValueError, "persistent_volume_configuration_missing"):
            replace(production, persistence_mode="local").validate()
        with self.assertRaisesRegex(ValueError, "local_auth_forbidden"):
            LocalAuthProvider(production)

    def test_signed_identity_roles_and_safe_responses(self):
        secret = "s" * 48
        config = replace(Settings(), app_env="staging", api_host="0.0.0.0",
                         frontend_url="https://forecast.example",
                         assistant_token=secret, manager_ids=frozenset({"manager-1"}),
                         reader_ids=frozenset({"reader-1"}), persistence_mode="hosted-volume").validate()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings=replace(config, state_dir=Path(directory)),
                             historical_state_dir=Path(directory) / "historical")
            client = TestClient(app)
            self.assertEqual(client.get("/api/live").status_code, 200)
            self.assertEqual(client.get("/api/ready").json()["status"], "ok")
            self.assertEqual(client.get("/api/performance/wape", headers={"x-actor-id": "manager-1"}).status_code, 401)
            reader = {"authorization": f"Bearer {signed_token(secret, 'reader-1', role='reader')}"}
            response = client.get("/api/v1/performance/wape", headers=reader)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNotNone(response.headers.get("x-request-id"))
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(client.post("/api/historical/first-vintage", headers={**reader,
                             "idempotency-key": "reader-run-01"}).status_code, 403)
            self.assertEqual(client.post("/api/historical/first-vintage", headers={
                "idempotency-key": "anonymous-run-01", "x-actor-id": "manager-1",
                "x-user-role": "admin"}).status_code, 401)
            self.assertEqual(client.post("/api/historical/first-vintage", headers={**reader,
                "idempotency-key": "spoofed-run-01", "x-user-role": "admin", "x-actor-id": "manager-1"},
                json={"role": "admin", "permissions": ["ADMIN"]}).status_code, 403)
            self.assertEqual(client.get("/api/performance/wape", headers={"authorization":
                             f"Bearer {signed_token(secret, 'outsider')}"}).status_code, 403)
            forged_role = {"authorization": f"Bearer {signed_token(secret, 'reader-1')}"}
            self.assertEqual(client.get("/api/performance/wape", headers=forged_role).status_code, 403)
            wrong_environment = {"authorization": f"Bearer {signed_token(secret, 'manager-1', environment='production')}"}
            self.assertEqual(client.get("/api/performance/wape", headers=wrong_environment).status_code, 401)
            wrong_audience = {"authorization": f"Bearer {signed_token(secret, 'manager-1', audience='other-api')}"}
            self.assertEqual(client.get("/api/performance/wape", headers=wrong_audience).status_code, 401)
            changed = signed_token(secret, "manager-1")
            payload, signature = changed.split(".", 1)
            altered = {"authorization": f"Bearer {payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"}
            self.assertEqual(client.get("/api/performance/wape", headers=altered).status_code, 401)
            principal = app.state.auth.authenticate(token=signed_token(secret, "reader-1", role="reader"),
                                                     actor_id="manager-1", client_host="testclient")
            self.assertEqual(principal.user_id, "reader-1")
            expired = {"authorization": f"Bearer {signed_token(secret, 'manager-1', ttl=-1)}"}
            self.assertEqual(client.get("/api/performance/wape", headers=expired).status_code, 401)
            malformed = {"authorization": "Bearer !!!.%%%"}
            self.assertEqual(client.get("/api/performance/wape", headers=malformed).status_code, 401)
            events = app.state.telemetry.snapshot()["counts"]
            self.assertGreater(events.get("auth_success", 0), 0)
            self.assertGreater(events.get("token_expired", 0), 0)
            self.assertGreater(events.get("authorization_denied", 0), 0)
            self.assertEqual(client.get("/api/health").json()["environment"], "staging")
            cors = client.options("/api/forecast/12m", headers={"origin": "https://forecast.example",
                                   "access-control-request-method": "GET"})
            self.assertEqual(cors.headers.get("access-control-allow-origin"), "https://forecast.example")
            blocked = client.options("/api/forecast/12m", headers={"origin": "https://evil.example",
                                      "access-control-request-method": "GET"})
            self.assertNotEqual(blocked.headers.get("access-control-allow-origin"), "https://evil.example")

    def test_railway_port_priority_bootstrap_and_version(self):
        with patch.dict(os.environ, {"API_PORT": "8765", "PORT": "9123"}, clear=True):
            self.assertEqual(Settings.from_env().api_port, 8765)
        with patch.dict(os.environ, {"PORT": "9123"}, clear=True):
            self.assertEqual(Settings.from_env().api_port, 9123)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().api_port, 8000)
        with patch.dict(os.environ, {"PORT": "invalid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "invalid_integer:PORT"):
                Settings.from_env()
        bootstrap = {"APP_ENV": "development", "API_HOST": "0.0.0.0", "PORT": "9123",
                     "RAILWAY_ENVIRONMENT_ID": "test-railway", "APP_VERSION": "0.11.1-test",
                     "RAILWAY_GIT_COMMIT_SHA": "a" * 40, "AI_ASSISTANT_API_ENABLED": "false",
                     "OPENAI_ENABLED": "false", "SUPABASE_ENABLED": "false", "VOICE_ENABLED": "false",
                     "DEEP_RESEARCH_ENABLED": "false"}
        with patch.dict(os.environ, bootstrap, clear=True):
            config = Settings.from_env()
        self.assertEqual(config.api_port, 9123)
        self.assertEqual(config.git_sha, "a" * 40)
        self.assertFalse(config.ai_assistant_api_enabled)
        self.assertFalse(any((config.openai_enabled, config.supabase_enabled,
                              config.voice_enabled, config.deep_research_enabled)))
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(settings=replace(config, state_dir=Path(directory)),
                                           historical_state_dir=Path(directory) / "historical"))
            for route in ("/api/live", "/api/health", "/api/ready", "/api/version"):
                self.assertEqual(client.get(route).status_code, 200, route)
            self.assertEqual(client.get("/api/version").json()["version"], "0.11.1-test")
            self.assertEqual(client.get("/api/version").json()["commit"], "a" * 40)
            self.assertEqual(client.get("/api/performance/wape", headers={"x-actor-id": "local-manager"}).status_code,
                             401)

    def test_assistant_degraded_does_not_break_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings=replace(Settings(), state_dir=Path(directory)),
                             historical_state_dir=Path(directory) / "historical",
                             assistant_provider=BrokenAssistant())
            client = TestClient(app)
            self.assertEqual(client.get("/api/ready").json()["status"], "degraded")
            headers = {"x-actor-id": "local-manager"}
            self.assertEqual(client.get("/api/forecast/12m", headers=headers).status_code, 200)
            failed = client.post("/api/assistant/message", headers=headers, json={"message": "Champion"})
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(failed.json()["error_code"], "ASSISTANT_001")
            self.assertNotIn("Traceback", failed.text)

    def test_data_failure_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            data = NormalizedDataProvider(data_dir=Path(directory), state_dir=Path(directory))
            client = TestClient(create_app(provider=data, historical_state_dir=Path(directory) / "historical"))
            self.assertEqual(client.get("/api/live").json()["status"], "ok")
            ready = client.get("/api/ready")
            self.assertEqual(ready.status_code, 503)
            self.assertEqual(ready.json()["status"], "not_ready")

    def test_sqlite_backup_restore_and_migration_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            store = LocalPersistenceProvider(folder / "state.sqlite3")
            store.put("source_evidence", "E3", {"level": "E3"})
            backup = folder / "backup.sqlite3"
            self.assertEqual(len(store.backup(backup)["sha256"]), 64)
            store.put("source_evidence", "E2", {"level": "E2"})
            with self.assertRaisesRegex(ValueError, "restore_requires_confirmation"):
                store.restore(backup)
            result = store.restore(backup, confirm=True)
            self.assertTrue(Path(result["safety_backup"]).is_file())
            self.assertIsNone(store.get("source_evidence", "E2"))
            bundle = export_bundle(store)
            self.assertEqual(validate_bundle(bundle)["records_source"], 1)
            target = LocalPersistenceProvider(folder / "target.sqlite3")
            self.assertEqual(import_bundle(target, bundle)["success"], 0)
            self.assertTrue(import_bundle(target, bundle, dry_run=False, confirm=True)["reconciled"])
            self.assertEqual(reconcile(store, target)["failed"], 0)
            bundle["entities"]["source_evidence"].append({"tampered": True})
            with self.assertRaisesRegex(ValueError, "migration_checksum_mismatch"):
                validate_bundle(bundle)

    def test_authorized_forecast_job_is_idempotent_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            pipeline = FastForecastPipeline()
            app = create_app(settings=replace(Settings(), state_dir=state),
                             historical_state_dir=state / "historical", forecast_pipeline=pipeline)
            client = TestClient(app)
            headers = {"x-actor-id": "local-manager", "idempotency-key": "forecast-july-2026"}
            response = client.post("/api/forecast/run", headers=headers, json={"period": "2026-07"})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()["run_id"]
            self.assertEqual(client.post("/api/forecast/run", headers=headers,
                                         json={"period": "2026-07"}).json()["run_id"], job_id)
            audit = app.state.persistence.list("run_logs")
            self.assertTrue(any(row.get("event") == "duplicate_prevented" and row.get("job_id") == job_id
                                for row in audit))
            self.assertTrue(any(row.get("event") == "command_accepted" and row.get("request_id")
                                for row in audit))
            for _ in range(50):
                job = client.get(f"/api/forecast/jobs/{job_id}", headers=headers).json()
                if job["status"] in {"COMPLETED", "FAILED"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "COMPLETED", job)
            run = app.state.persistence.get("monthly_runs", job["model_run_id"])
            self.assertEqual(run["state"], "Completed")
            self.assertEqual(len(run["results"]["ensemble"]["forecast_towell"]), 12)
            vintage_id = run["results"]["vintage"]["vintage_id"]
            self.assertIsNotNone(app.state.persistence.get("forecast_vintages", vintage_id))
            self.assertEqual(pipeline.events, ["statistical", "ml", "ensemble"])
            app.state.persistence.put("source_evidence", "TEST-PERSISTENCE-001", {"level": "technical-test"})
            app.state.persistence.put("performance_metrics", "TEST-PERSISTENCE-001", {"wape": 1.0})
            self.assertTrue(app.state.persistence.list("research_snapshots"))
            self.assertTrue(app.state.persistence.list("forecast_bands"))
            self.assertTrue(app.state.persistence.list("run_logs"))

            # A fresh application instance must reopen the same persistent volume.
            restarted = create_app(settings=replace(Settings(), state_dir=state),
                                   historical_state_dir=state / "historical", forecast_pipeline=FastForecastPipeline())
            after_restart = TestClient(restarted).get(f"/api/forecast/jobs/{job_id}", headers=headers)
            self.assertEqual(after_restart.status_code, 200, after_restart.text)
            self.assertEqual(after_restart.json()["status"], "COMPLETED")
            self.assertIsNotNone(restarted.state.persistence.get("monthly_runs", job["model_run_id"]))
            self.assertIsNotNone(restarted.state.persistence.get("forecast_vintages", vintage_id))
            self.assertEqual(restarted.state.persistence.get("source_evidence", "TEST-PERSISTENCE-001"),
                             {"level": "technical-test"})
            self.assertEqual(restarted.state.persistence.get("performance_metrics", "TEST-PERSISTENCE-001"),
                             {"wape": 1.0})
            for entity in ("research_snapshots", "forecast_bands", "run_logs"):
                self.assertTrue(restarted.state.persistence.list(entity), entity)

    def test_rate_retry_and_future_providers_fail_closed(self):
        limiter = SlidingWindowRateLimiter()
        self.assertTrue(limiter.allow("one", 1))
        self.assertFalse(limiter.allow("one", 1))
        calls = []
        def operation():
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError()
            return "ok"
        self.assertEqual(retry(operation, max_attempts=2, initial_backoff=0), "ok")
        with self.assertRaisesRegex(RuntimeError, "provider_disabled"):
            OpenAIAssistantProvider().respond("hi", {}, {})
        with self.assertRaisesRegex(RuntimeError, "provider_disabled"):
            OpenAIDeepResearchProvider().run("2026-08-12", "Walmart")
        with self.assertRaisesRegex(RuntimeError, "provider_disabled"):
            DisabledVoiceProvider().transcribe(b"")


if __name__ == "__main__":
    unittest.main()
