import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx

from services.assistant_api.auth import Principal, ROLE_PERMISSIONS
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.rs_assistant import RSAssistantRequest, RSAssistantWorkflow, mount_rs_assistant
from services.assistant_api.supabase_access import PreviewError

CHAIN = "00000000-0000-4000-8000-000000000001"
BODY = {"chain_id": CHAIN, "purchase_type": "RS", "format": "BD", "model": "FENDI", "period": "2026-10"}
SCOPE = {**BODY, "version_id": "c139f367-df75-5ef3-8f9f-1627038e4496"}


class RSAssistantTests(unittest.TestCase):
    def test_questions_start_public_research_only_with_execution_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalPersistenceProvider(Path(tmp) / "state.sqlite")
            provider = Mock()
            provider.status.return_value = {"status": "NOT_REQUESTED"}
            provider.start.return_value = {"status": "PENDING"}
            provider.cached.return_value = {"status": "NOT_REQUESTED"}
            compute = Mock()
            workflow = RSAssistantWorkflow(storage, provider, compute=compute)
            scoped = {"scope": SCOPE, "records": [], "quality": {}, "metrics": {}}
            question = RSAssistantRequest(**BODY, message="riesgo de desabasto; private text")
            answer = workflow.answer(question, scoped)
            self.assertIn("inventario disponible es agregado", answer["message"])
            provider.cached.assert_called_once_with(SCOPE)
            explanation = workflow.answer(RSAssistantRequest(**BODY, message="Explica el Bias"), scoped,
                                          may_start_research=True)
            self.assertIn("real menos pronóstico", explanation["message"])
            self.assertIn("positivo indica subestimación", explanation["message"])
            compute.assert_not_called()
            provider.start.assert_not_called()
            provider.status.assert_not_called()
            workflow.answer(RSAssistantRequest(**BODY, action="research_status"), scoped)
            provider.status.assert_called_once_with(SCOPE)
            provider.start.assert_not_called()
            answer = workflow.answer(question, scoped, may_start_research=True)
            self.assertEqual(answer["research"], {"status": "PENDING"})
            provider.start.assert_called_once_with(SCOPE)
            provider.start.reset_mock()
            workflow.answer(RSAssistantRequest(**BODY, action="research"), scoped, may_start_research=True)
            provider.start.assert_called_once_with(SCOPE)

    def test_forecast_separates_families_reuses_results_and_reports_short_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalPersistenceProvider(Path(tmp) / "state.sqlite")
            records = [{"chain_id": CHAIN, "product_id": "test-product", "objective": "Venta",
                        "period": f"{2025 + i // 12}-{1 + i % 12:02}", "value": "10"}
                       for i in range(22)]
            scoped = {"scope": SCOPE, "records": records, "quality": {}, "metrics": {}, "eligible_variants": 2}
            compute = Mock(return_value={"chains": [{"model_audit": {"engine_version": "synthetic",
                "retrospective_statistical_metrics": {"wape": 10, "bias": -5}}, "forecast_towell": [
                    {"target_period": "2026-11", "statistical": 12, "ml": None, "ensemble_value": 11},
                ]}]})
            provider = Mock()
            provider.start.return_value = {"status": "PENDING"}
            workflow = RSAssistantWorkflow(storage, provider, compute=compute)
            body = RSAssistantRequest(**BODY, action="forecast")
            with self.assertRaisesRegex(PreviewError, "RS_FORECAST_HISTORY_INSUFFICIENT"):
                workflow.answer(body, {**scoped, "records": records[:1]}, may_start_research=True)
            provider.start.assert_not_called()
            response = workflow.answer(body, scoped, may_start_research=True)
            provider.start.assert_called_once_with(SCOPE)
            self.assertEqual(response["research"], {"status": "PENDING"})
            first = response["forecast"]
            provider.start.side_effect = sqlite3.OperationalError
            cached = workflow.answer(body, scoped, may_start_research=True)
            self.assertEqual(cached["research"]["reason"], "RESEARCH_STORAGE_UNAVAILABLE")
            second = cached["forecast"]
            self.assertEqual(first, second)
            self.assertEqual(first["periods"][0]["statistical"], {"pieces": "12", "covered": 1, "eligible": 2})
            self.assertIsNone(first["periods"][0]["ml"]["pieces"])
            self.assertIsNone(first["live"])
            compute.assert_called_once()

    def test_route_derives_research_permission_from_role_and_chain(self):
        app = FastAPI()
        identity, data, limiter, workflow = Mock(), Mock(), Mock(), Mock()
        mount_rs_assistant(app, SimpleNamespace(ai_assistant_api_enabled=True), identity, data, limiter, workflow)
        route = next(route.endpoint for route in app.routes if route.path == "/api/forecast/rs-assistant")
        body = RSAssistantRequest(**BODY, message="¿Qué tendencias comerciales hay?")
        request = SimpleNamespace(state=SimpleNamespace())
        with patch("services.assistant_api.rs_assistant.load_rs_scope") as load:
            for role, execute_chain_ids, expected in (
                ("reader", {CHAIN}, False), ("editor", set(), False), ("editor", {CHAIN}, True),
            ):
                with self.subTest(role=role, execute_chain_ids=execute_chain_ids):
                    identity.authenticate.return_value = Principal(
                        "user", role, ROLE_PERMISSIONS[role], "session", frozenset({CHAIN}), frozenset(execute_chain_ids)
                    )
                    route(body, request, "Bearer fixture")
                    workflow.answer.assert_called_with(body, load.return_value, may_start_research=expected)

    def test_route_rejects_anonymous_wrong_chain_and_inactive_b_before_work(self):
        app = FastAPI()

        @app.exception_handler(PreviewError)
        async def preview_error(_request, error):
            return JSONResponse(status_code=error.status, content={"code": error.code})

        identity, data, limiter, workflow = Mock(), Mock(), Mock(), Mock()
        identity.authenticate.return_value = Principal("user", "reader", ROLE_PERMISSIONS["reader"], "session",
                                                      chain_ids=frozenset({CHAIN}))
        mount_rs_assistant(app, SimpleNamespace(ai_assistant_api_enabled=True), identity, data, limiter, workflow)
        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                path, headers = "/api/forecast/rs-assistant", {"Authorization": "Bearer fixture"}
                self.assertEqual((await client.post(path, json=BODY)).status_code, 401)
                self.assertEqual((await client.post(path, headers=headers, json={**BODY, "action": "research"})).status_code, 403)
                response = await client.post(path, headers=headers, json=BODY)
                self.assertEqual(response.json(), {"code": "RS_CANDIDATE_NOT_ACTIVE"})
                self.assertEqual(response.status_code, 409)
                self.assertEqual((await client.post(path, headers=headers, json={**BODY, "purchase_type": "CE"})).status_code, 422)

        with patch("services.assistant_api.rs_assistant.load_rs_scope",
                   side_effect=PreviewError("RS_CANDIDATE_NOT_ACTIVE", 409)) as load:
            asyncio.run(requests())
            load.assert_called_once()
            workflow.answer.assert_not_called()
