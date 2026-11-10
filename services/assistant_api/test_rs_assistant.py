import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from .auth import Principal, ROLE_PERMISSIONS
from .persistence import LocalPersistenceProvider
from .rs_assistant import RSAssistantRequest, RSAssistantWorkflow, mount_rs_assistant
from .supabase_access import PreviewError

CHAIN = "00000000-0000-4000-8000-000000000001"
BODY = {"chain_id": CHAIN, "purchase_type": "RS", "format": "BD", "model": "FENDI", "period": "2026-10"}
SCOPE = {**BODY, "version_id": "c139f367-df75-5ef3-8f9f-1627038e4496"}


class RSAssistantTests(unittest.TestCase):
    def test_questions_never_compute_or_search_and_research_uses_only_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalPersistenceProvider(Path(tmp) / "state.sqlite")
            provider = Mock()
            provider.status.return_value = {"status": "NOT_REQUESTED"}
            provider.start.return_value = {"status": "PENDING"}
            compute = Mock()
            workflow = RSAssistantWorkflow(storage, provider, compute=compute)
            scoped = {"scope": SCOPE, "records": [], "quality": {}, "metrics": {}}
            message = "riesgo de desabasto; private text"
            answer = workflow.answer(RSAssistantRequest(**BODY, message=message), scoped)
            self.assertIn("inventario disponible es agregado", answer["message"])
            compute.assert_not_called()
            provider.start.assert_not_called()
            provider.status.assert_not_called()
            workflow.answer(RSAssistantRequest(**BODY, action="research_status"), scoped)
            provider.status.assert_called_once_with(SCOPE)
            provider.start.assert_not_called()
            workflow.answer(RSAssistantRequest(**BODY, message=message, action="research"), scoped)
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
            workflow = RSAssistantWorkflow(storage, compute=compute)
            body = RSAssistantRequest(**BODY, action="forecast")
            with self.assertRaisesRegex(PreviewError, "RS_FORECAST_HISTORY_INSUFFICIENT"):
                workflow.answer(body, {**scoped, "records": records[:1]})
            first = workflow.answer(body, scoped)["forecast"]
            second = workflow.answer(body, scoped)["forecast"]
            self.assertEqual(first, second)
            self.assertEqual(first["periods"][0]["statistical"], {"pieces": "12", "covered": 1, "eligible": 2})
            self.assertIsNone(first["periods"][0]["ml"]["pieces"])
            self.assertIsNone(first["live"])
            compute.assert_called_once()

    def test_route_rejects_anonymous_wrong_chain_and_inactive_b_before_work(self):
        app = FastAPI()

        @app.exception_handler(PreviewError)
        async def preview_error(_request, error):
            return JSONResponse(status_code=error.status, content={"code": error.code})

        identity, data, limiter, workflow = Mock(), Mock(), Mock(), Mock()
        identity.authenticate.return_value = Principal("user", "reader", ROLE_PERMISSIONS["reader"], "session",
                                                      chain_ids=frozenset({CHAIN}))
        mount_rs_assistant(app, SimpleNamespace(ai_assistant_api_enabled=True), identity, data, limiter, workflow)
        with TestClient(app) as client, patch("services.assistant_api.rs_assistant.load_rs_scope",
                                             side_effect=PreviewError("RS_CANDIDATE_NOT_ACTIVE", 409)) as load:
            path, headers = "/api/forecast/rs-assistant", {"Authorization": "Bearer fixture"}
            self.assertEqual(client.post(path, json=BODY).status_code, 401)
            self.assertEqual(client.post(path, headers=headers, json={**BODY, "action": "research"}).status_code, 403)
            response = client.post(path, headers=headers, json=BODY)
            self.assertEqual(response.json(), {"code": "RS_CANDIDATE_NOT_ACTIVE"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(client.post(path, headers=headers, json={**BODY, "purchase_type": "CE"}).status_code, 422)
            load.assert_called_once()
            workflow.answer.assert_not_called()
