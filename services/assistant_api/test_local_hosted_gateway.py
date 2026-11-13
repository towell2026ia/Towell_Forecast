"""Gateway security wiring with fake SQL interfaces, no database or credentials.

These tests check verified UUID/SQL role boundaries; they do not prove hosted
login, PostgreSQL RLS policy execution, or production permissions.
"""
from contextlib import nullcontext
import json
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from services.assistant_api.auth import AuthFailure
from services.assistant_api.local_postgres import LocalPostgresGateway

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SESSION = {"sub": USER, "session_id": "33333333-3333-4333-8333-333333333333", "exp": 2000,
           "user": {"id": USER, "email": "fixture@example.invalid", "user_metadata": {"role": "ADMIN"}}}


class Fragment(str):
    def format(self, *args, **kwargs):
        return Fragment(super().format(*args, **kwargs))

    def join(self, items):
        return Fragment(super().join(items))

    def __add__(self, other):
        return Fragment(str(self) + str(other))


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, profile, rows=None):
        self.profile = profile
        self.rows = rows or []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transaction(self):
        return nullcontext()

    def execute(self, query, params=None):
        query = str(query)
        self.calls.append((query, params))
        if "from public.profiles where id=%s" in query:
            return Cursor(self.profile)
        if query.startswith("select count(*)"):
            return Cursor([{"n": len(self.rows)}])
        return Cursor(self.rows if query.startswith("select * ") else [])


def sql_module():
    """Minimal adapter, not a simulated SQL server or RLS implementation."""
    module = ModuleType("psycopg")
    module.Error = type("FakePsycopgError", (Exception,), {})
    module.sql = SimpleNamespace(SQL=Fragment, Identifier=lambda *parts: Fragment(".".join('"' + p + '"' for p in parts)),
                                 Placeholder=lambda: Fragment("%s"))
    return module


class LocalHostedGatewayTests(unittest.TestCase):
    def make(self, *, profile=None, rows=None):
        gateway = object.__new__(LocalPostgresGateway)
        gateway.auth = Mock()
        gateway.auth.verify.return_value = SESSION
        self.db = FakeConnection(profile if profile is not None else [{"id": USER, "status": "ACTIVE", "global_role": "VIEWER"}], rows)
        gateway.connect = Mock(return_value=self.db)
        return gateway

    def request(self, method="GET", path="/rest/v1/products", **kwargs):
        return httpx.Request(method, "http://127.0.0.1:8000" + path,
            headers={"Authorization": "Bearer synthetic-hosted-token", "X-User-Role": "ADMIN", "X-Actor-Id": OTHER}, **kwargs)

    def handle(self, gateway, request):
        with patch.dict("sys.modules", {"psycopg": sql_module()}):
            return gateway.handle(request)

    def test_password_and_refresh_endpoints_cannot_read_local_passwords_or_issue_fixtures(self):
        gateway = self.make()
        with patch("services.assistant_api.local_postgres.unprotect", side_effect=AssertionError("no user password decryption")):
            for path in ["/auth/v1/token?grant_type=password", "/auth/v1/token?grant_type=refresh_token", "/auth/v1/signup"]:
                response = gateway.handle(self.request("POST", path, json={"email": "local-admin@localhost", "password": "synthetic", "refresh_token": "fixture"}))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "HOSTED_AUTH_REQUIRED")
        gateway.auth.verify.assert_not_called()
        gateway.connect.assert_not_called()

    def test_rejected_fixture_or_invalid_token_never_reaches_sql_or_password_files(self):
        gateway = self.make()
        gateway.auth.verify.side_effect = AuthFailure("AUTH_001", 401, "invalid_identity")
        with patch("services.assistant_api.local_postgres.unprotect", side_effect=AssertionError("no credentials")):
            response = gateway.handle(self.request())
        self.assertEqual(response.status_code, 401)
        gateway.connect.assert_not_called()

    def test_auth_unavailability_retains_503_without_local_fallback(self):
        gateway = self.make()
        gateway.auth.verify.side_effect = AuthFailure("AUTH_001", 503, "hosted_auth_unavailable")
        response = gateway.handle(self.request())
        self.assertEqual(response.status_code, 503)
        gateway.connect.assert_not_called()

    def test_verified_auth_user_lookup_is_not_local_permission_or_account_creation(self):
        gateway = self.make()
        response = gateway.handle(self.request(path="/auth/v1/user"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], USER)
        gateway.connect.assert_not_called()

    def test_missing_inactive_or_invalid_local_profile_blocks_commercial_queries(self):
        for profile in [[], [{"id": USER, "status": "INACTIVE", "global_role": "ADMIN"}],
                        [{"id": OTHER, "status": "ACTIVE", "global_role": "ADMIN"}],
                        [{"id": USER, "status": "ACTIVE", "global_role": "forged"}]]:
            with self.subTest(profile=profile):
                gateway = self.make(profile=profile)
                response = self.handle(gateway, self.request())
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "LOCAL_PROFILE_REQUIRED")
                self.assertFalse(any('from "public"."products"' in sql for sql, _ in self.db.calls))
                self.assertFalse(any(sql.lower().startswith(("insert", "update", "delete")) for sql, _ in self.db.calls))

    def test_sql_uses_verified_uuid_read_only_authenticated_not_browser_role(self):
        gateway = self.make(rows=[{"id": "synthetic-product"}])
        response = self.handle(gateway, self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.calls[:4], [
            ("set transaction read only", None),
            ("set local role authenticated", None),
            ("select set_config('request.jwt.claim.sub',%s,true)", (USER,)),
            ("select id,status,global_role from public.profiles where id=%s", (USER,)),
        ])
        self.assertNotIn(OTHER, repr(self.db.calls))
        self.assertFalse(any("role ADMIN" in sql or "role postgres" in sql for sql, _ in self.db.calls))

    def test_profile_role_comes_from_db_and_is_reloaded_each_request(self):
        gateway = self.make(rows=[{"id": USER, "status": "ACTIVE", "global_role": "VIEWER"}])
        first = self.handle(gateway, self.request(path="/rest/v1/profiles"))
        self.assertEqual(first.json()[0]["global_role"], "VIEWER")
        self.db.profile = [{"id": USER, "status": "INACTIVE", "global_role": "VIEWER"}]
        second = self.handle(gateway, self.request(path="/rest/v1/profiles"))
        self.assertEqual(second.status_code, 403)
        self.assertEqual(gateway.auth.verify.call_count, 2)
        self.assertEqual(gateway.connect.call_count, 2)

    def test_raw_history_and_mutating_rest_or_rpc_stay_closed_for_any_profile(self):
        for role in ["VIEWER", "EDITOR", "ADMIN"]:
            gateway = self.make(profile=[{"id": USER, "status": "ACTIVE", "global_role": role}])
            for method, path in [("GET", "/rest/v1/monthly_observations"),
                                 ("POST", "/rest/v1/profiles"), ("POST", "/rest/v1/products"),
                                 ("POST", "/rest/v1/rpc/e4_create_calculation"),
                                 ("POST", "/rest/v1/rpc/official_source_activate")]:
                with self.subTest(role=role, method=method, path=path):
                    response = self.handle(gateway, self.request(method, path, json={"global_role": "ADMIN"}))
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["code"], "LOCAL_WRITE_DISABLED")

    def test_logout_denies_the_verified_session_not_a_browser_supplied_identity(self):
        gateway = self.make()
        response = gateway.handle(self.request("POST", "/auth/v1/logout", json={"sub": OTHER, "session_id": OTHER}))
        self.assertEqual(response.status_code, 204)
        gateway.auth.revoke_verified.assert_called_once_with(SESSION)
        gateway.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
