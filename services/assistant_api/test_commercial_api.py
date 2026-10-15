"""Isolated request-scoped read tests. No production fixtures or writes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.assistant_api.auth import AuthFailure, Principal, ROLE_PERMISSIONS
from services.assistant_api.commercial_api import commercial_catalog, mount_commercial_routes
from services.assistant_api.supabase_access import PreviewError

RULES = json.loads(Path("config/commercial_catalog_rules.json").read_text(encoding="utf-8"))


class Source:
    def __init__(self):
        self.calls = []
        self.data = {"chains": [{"id": "c", "name": "Authorized"}, {"id": "foreign", "name": "Foreign"}],
            "categories": [{"id": "cat", "chain_id": "c", "name": "Toalla"}],
            "products": [{"id": "p", "chain_id": "c", "category_id": "cat", "product_code": "opaque", "variant_code": None, "description": "TOALLA MB FENDI OXFORD"},
                         {"id": "private", "chain_id": "foreign", "category_id": None, "product_code": "private", "description": "PRIVATE DESIGN"}],
            "import_profile_versions": [], "product_commercial_classifications_current": []}

    def bind(self, token):
        self.calls.append(("bind", token))
        return self

    def rows(self, table, params):
        self.calls.append((table, params))
        return copy.deepcopy(self.data.get(table, []))


class Identity:
    def authenticate(self, *, token, **kwargs):
        if token not in ROLE_PERMISSIONS:
            raise AuthFailure("AUTH_001", 401)
        return Principal("actor", token, ROLE_PERMISSIONS[token], "session", frozenset({"c"}), frozenset())


class CommercialReadTests(unittest.TestCase):
    def setUp(self):
        self.source = Source()
        self.principal = SimpleNamespace(chain_ids={"c"})

    def test_complete_catalog_is_transient_pending_and_scoped(self):
        before = copy.deepcopy(self.source.data)
        result = commercial_catalog(self.source, self.principal, RULES)
        self.assertFalse(result["persisted"])
        self.assertEqual(len(result["products"]), 1)
        row = result["products"][0]
        self.assertEqual((row["model"], row["color"], row["size"], row["product_type"]), ("FENDI", "OXFORD", "MEDIO BAÑO", "TOALLA"))
        self.assertEqual(row["status"], "PENDING_CLASSIFICATION")
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(self.source.data, before)

    def test_future_products_unknown_attributes_and_multiword_designs_are_not_omitted(self):
        for description in ("TOALLA FACIAL NEW DESIGN AZUL", "ambiguous", "BATA DE BANO XG"):
            self.source.data["products"][0]["description"] = description
            result = commercial_catalog(self.source, self.principal, RULES)
            self.assertEqual(len(result["products"]), 1)
            self.assertEqual(result["products"][0]["status"], "PENDING_CLASSIFICATION")

    def test_approved_names_come_from_dictionaries_not_proposals(self):
        self.source.data["product_commercial_classifications_current"] = [{"product_id": "p", "chain_id": "c", "classification_status": "APPROVED", "stale_snapshot": False, "model_id": "m", "color_id": "color", "size_id": None, "product_type_id": "type"}]
        for field, label in (("models", "REVIEWED MODEL"), ("colors", "REVIEWED COLOR"), ("product_types", "REVIEWED TYPE")):
            identifier = {"models": "m", "colors": "color", "product_types": "type"}[field]
            self.source.data["commercial_" + field] = [{"id": identifier, "name": label}]
        row = commercial_catalog(self.source, self.principal, RULES)["products"][0]
        self.assertEqual(row["status"], "APPROVED")
        self.assertEqual(row["model"], "REVIEWED MODEL")
        self.assertIsNone(row["size"])
        self.source.data["product_commercial_classifications_current"][0]["stale_snapshot"] = True
        self.assertEqual(commercial_catalog(self.source, self.principal, RULES)["products"][0]["status"], "PENDING_CLASSIFICATION")

    def test_bad_source_and_dictionary_fail_closed(self):
        self.source.data["products"].append(copy.deepcopy(self.source.data["products"][0]))
        with self.assertRaises(ValueError):
            commercial_catalog(self.source, self.principal, RULES)

    def test_read_route_all_roles_no_execute_grant_required_and_no_post(self):
        app = FastAPI()
        mount_commercial_routes(app, Identity(), self.source, SimpleNamespace(allow=lambda *args: True))

        @app.exception_handler(PreviewError)
        async def error(request, exc):
            return JSONResponse({"error_code": exc.code}, status_code=exc.status)

        with TestClient(app) as client:
            self.assertEqual(client.get("/api/forecast/commercial-catalog").status_code, 401)
            for role in ("reader", "editor", "manager"):
                response = client.get("/api/forecast/commercial-catalog", headers={"Authorization": "Bearer " + role})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["products"]), 1)
                self.assertIn(("bind", role), self.source.calls)
            self.assertEqual(client.get("/api/forecast/commercial-catalog", headers={"Authorization": "Bearer invalid"}).status_code, 401)
            self.assertEqual(client.post("/api/forecast/commercial-catalog").status_code, 405)

    def test_rate_limited_before_catalog_read(self):
        app = FastAPI()
        mount_commercial_routes(app, Identity(), self.source, SimpleNamespace(allow=lambda *args: False))

        @app.exception_handler(PreviewError)
        async def error(request, exc):
            return JSONResponse({"error_code": exc.code}, status_code=exc.status)

        with TestClient(app) as client:
            self.assertEqual(client.get("/api/forecast/commercial-catalog", headers={"Authorization": "Bearer reader"}).status_code, 429)
        self.assertFalse(self.source.calls)
