from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from services.assistant_api.api import create_app
from services.assistant_api.auth import SignedAuthProvider
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


def signed_token(secret: str, user_id: str, *, ttl: int = 60) -> str:
    now = int(time.time())
    claims = {"user_id": user_id, "session_id": "test-session", "iat": now,
              "exp": now + ttl, "aud": "forecast-towell-fastapi", "role": "manager"}
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
                             assistant_token="s" * 48, manager_ids=frozenset({"manager-1"})).validate()
        self.assertTrue(production.strict_auth)

    def test_signed_identity_roles_and_safe_responses(self):
        secret = "s" * 48
        config = replace(Settings(), app_env="staging", frontend_url="https://forecast.example",
                         assistant_token=secret, manager_ids=frozenset({"manager-1"}),
                         reader_ids=frozenset({"reader-1"})).validate()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings=replace(config, state_dir=Path(directory)),
                             historical_state_dir=Path(directory) / "historical")
            client = TestClient(app)
            self.assertEqual(client.get("/api/live").status_code, 200)
            self.assertEqual(client.get("/api/ready").json()["status"], "ok")
            self.assertEqual(client.get("/api/performance/wape", headers={"x-actor-id": "manager-1"}).status_code, 401)
            reader = {"authorization": f"Bearer {signed_token(secret, 'reader-1')}"}
            response = client.get("/api/v1/performance/wape", headers=reader)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNotNone(response.headers.get("x-request-id"))
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(client.post("/api/historical/first-vintage", headers={**reader,
                             "idempotency-key": "reader-run-01"}).status_code, 403)
            self.assertEqual(client.get("/api/performance/wape", headers={"authorization":
                             f"Bearer {signed_token(secret, 'outsider')}"}).status_code, 403)
            expired = {"authorization": f"Bearer {signed_token(secret, 'manager-1', ttl=-1)}"}
            self.assertEqual(client.get("/api/performance/wape", headers=expired).status_code, 401)
            malformed = {"authorization": "Bearer !!!.%%%"}
            self.assertEqual(client.get("/api/performance/wape", headers=malformed).status_code, 401)
            self.assertEqual(client.get("/api/health").json()["environment"], "staging")
            cors = client.options("/api/forecast/12m", headers={"origin": "https://forecast.example",
                                   "access-control-request-method": "GET"})
            self.assertEqual(cors.headers.get("access-control-allow-origin"), "https://forecast.example")
            blocked = client.options("/api/forecast/12m", headers={"origin": "https://evil.example",
                                      "access-control-request-method": "GET"})
            self.assertNotEqual(blocked.headers.get("access-control-allow-origin"), "https://evil.example")

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
            for _ in range(50):
                job = client.get(f"/api/forecast/jobs/{job_id}", headers=headers).json()
                if job["status"] in {"COMPLETED", "FAILED"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "COMPLETED", job)
            run = app.state.persistence.get("monthly_runs", job["model_run_id"])
            self.assertEqual(run["state"], "Completed")
            self.assertEqual(len(run["results"]["ensemble"]["forecast_towell"]), 12)
            self.assertIsNotNone(app.state.persistence.get("forecast_vintages", run["results"]["vintage"]["vintage_id"]))
            self.assertEqual(pipeline.events, ["statistical", "ml", "ensemble"])

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
