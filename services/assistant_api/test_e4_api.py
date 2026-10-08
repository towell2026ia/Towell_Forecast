"""Flag, JWT/scope, idempotency, and E4 route contract tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.assistant_api.auth import AuthFailure, Principal, ROLE_PERMISSIONS
from services.assistant_api.e4_api import mount_e4_routes
from services.assistant_api.operational_preview import snapshot_hash
from services.assistant_api.supabase_access import PreviewError
from services.assistant_api.test_calculation_history import ACTOR, CHAIN, PRODUCT, preview


class FakeIdentity:
    def authenticate(self, *, token, actor_id, client_host):
        if token == "admin":
            return Principal(ACTOR, "manager", ROLE_PERMISSIONS["manager"], "session", frozenset({CHAIN}), frozenset({CHAIN}))
        if token == "reader":
            return Principal(ACTOR, "reader", ROLE_PERMISSIONS["reader"], "session", frozenset({CHAIN}), frozenset())
        raise AuthFailure("AUTH_001", 401)


class FakeSource:
    def __init__(self, records, saved):
        self._records, self.saved, self.client = records, saved, self

    def bind(self, token):
        return self

    def records(self, **kwargs):
        return self._records

    def product_catalog(self, chain):
        return [{"id": PRODUCT, "chain_id": CHAIN}]

    def rows(self, table, params):
        if table == "forecast_calculations":
            return [self.saved["calculation"]] if self.saved.get("calculation") else []
        if table == "forecast_calculation_horizons":
            return self.saved["horizons"]
        if table == "forecast_selection_events":
            return []
        if table == "forecast_live_evaluations":
            return self.saved.get("live", [])
        if table == "forecast_capture_sessions":
            return [{"id": self.saved["session"], "chain_id": CHAIN, "product_id": PRODUCT, "status": "DRAFT"}] if self.saved.get("session") else []
        if table == "monthly_observations":
            return []
        if table == "current_forecast_selection":
            return []
        if table == "forecast_learning_events":
            return []
        return []


class FakeWriter:
    def __init__(self, saved):
        self.saved, self.calls = saved, []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        if name == "e4_create_calculation":
            data = payload["p"]
            self.saved["calculation"] = {"id": "00000000-0000-4000-8000-000000000100",
                "chain_id": CHAIN, "product_id": PRODUCT, "issue_period": data["issue_period"],
                "status": "READY_FOR_DECISION", "suggested_reference": data["suggested_reference"]}
            self.saved["horizons"] = data["horizons"]
            return self.saved["calculation"]["id"]
        if name == "e4_save_capture":
            self.saved["session"] = "00000000-0000-4000-8000-000000000200"
            return self.saved["session"]
        if name == "e4_close_live":
            return {"status": "CLOSED", "evaluations": 4}
        return "00000000-0000-4000-8000-000000000300"


class E4ApiTests(unittest.TestCase):
    def setUp(self):
        records = [{"chain_id": CHAIN, "product_id": PRODUCT, "period": "2026-07",
            "objective": "Venta", "value": 100, "source_id": "00000000-0000-4000-8000-000000000400",
            "version_no": 1, "availability_source": "UNKNOWN", "available_at": None}]
        self.preview = preview()
        self.preview["dataset_hash"] = snapshot_hash(records)
        self.saved = {"horizons": []}
        self.source = FakeSource(records, self.saved)
        self.writer = FakeWriter(self.saved)
        self.flags = SimpleNamespace(forecast_calculation_history_enabled=True, forecast_selection_enabled=True,
            capture_center_enabled=True, live_learning_enabled=True, git_sha="test-sha")
        app = FastAPI()
        @app.exception_handler(PreviewError)
        async def preview_error(request, error):
            return JSONResponse(status_code=error.status, content={"error_code": error.code})
        runner = SimpleNamespace(persistence=SimpleNamespace(get=lambda table, key: self.preview if key == "preview-1" else None))
        mount_e4_routes(app, self.flags, FakeIdentity(), self.source, runner, self.writer)
        self.client = TestClient(app)

    def test_flags_auth_scope_and_idempotency(self):
        payload = {"chain_id": CHAIN, "product_id": PRODUCT, "preview_id": "preview-1"}
        self.flags.forecast_calculation_history_enabled = False
        self.assertEqual(self.client.post("/api/forecast/calculations", json=payload).status_code, 503)
        self.flags.forecast_calculation_history_enabled = True
        self.assertEqual(self.client.post("/api/forecast/calculations", json=payload).status_code, 401)
        self.assertEqual(self.client.post("/api/forecast/calculations", json=payload,
            headers={"Authorization": "Bearer reader", "Idempotency-Key": "first"}).status_code, 403)
        self.assertEqual(self.client.post("/api/forecast/calculations", json=payload,
            headers={"Authorization": "Bearer admin"}).status_code, 422)
        payload["chain_id"] = "00000000-0000-4000-8000-000000000999"
        self.assertEqual(self.client.post("/api/forecast/calculations", json=payload,
            headers={"Authorization": "Bearer admin", "Idempotency-Key": "first"}).status_code, 403)
        self.assertFalse(self.writer.calls)

    def test_create_snapshot_select_and_capture(self):
        headers = {"Authorization": "Bearer admin", "Idempotency-Key": "first"}
        result = self.client.post("/api/forecast/calculations", json={"chain_id": CHAIN,
            "product_id": PRODUCT, "preview_id": "preview-1"}, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)
        name, payload = self.writer.calls[-1]
        self.assertEqual(name, "e4_create_calculation")
        self.assertEqual(len(payload["p"]["horizons"]), 12)
        self.assertEqual(payload["p"]["input_observation_ids"], [self.source._records[0]["source_id"]])
        self.assertEqual(self.client.get(f"/api/forecast/calculations/{result.json()['calculation_id']}",
            headers=headers).json()["status"], "READY_FOR_DECISION")
        selection = self.client.post(f"/api/forecast/calculations/{result.json()['calculation_id']}/select",
            json={"selected_candidate": "ENSEMBLE"}, headers=headers)
        self.assertEqual(selection.status_code, 200, selection.text)
        self.assertEqual(self.writer.calls[-1][0], "e4_select_forecast")
        capture = self.client.post("/api/forecast/observations/capture", json={"chain_id": CHAIN,
            "product_id": PRODUCT, "period": "2026-08-01", "order_value": 120, "sale_value": 100,
            "delivery_value": 90}, headers=headers)
        self.assertEqual(capture.status_code, 200, capture.text)
        confirmed = self.client.post(f"/api/forecast/observations/capture/{capture.json()['session_id']}/confirm",
            headers=headers)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        closed = self.client.post("/api/forecast/live-evaluation", json={"chain_id": CHAIN,
            "product_id": PRODUCT, "target_period": "2026-08-01"}, headers=headers)
        self.assertEqual(closed.json()["evaluations"], 4)


if __name__ == "__main__":
    unittest.main()
