"""GET-only, request-scoped Supabase access. No service key or RLS bypass."""
from __future__ import annotations

import base64
import json
import math
import time
import uuid
from typing import Any

import httpx


class PreviewError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def uuid_value(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise PreviewError("SCOPE_FORBIDDEN", 403) from None


class SupabaseReadClient:
    def __init__(self, url: str, key: str, token: str | None = None, *, timeout: int = 10,
                 transport: httpx.BaseTransport | None = None):
        self.url, self._key, self._token = url.rstrip("/"), key, token
        self.timeout, self.transport = timeout, transport

    def __repr__(self) -> str:
        return "SupabaseReadClient(GET-only, credentials redacted)"

    def bind(self, token: str) -> "SupabaseReadClient":
        return SupabaseReadClient(self.url, self._key, token, timeout=self.timeout, transport=self.transport)

    def get(self, path: str, params: dict[str, Any] | None = None):
        if not self._token:
            raise PreviewError("AUTH_REQUIRED", 401)
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client:
                response = client.get(self.url + path, params=params,
                    headers={"apikey": self._key, "Authorization": f"Bearer {self._token}"})
            if response.status_code == 401:
                raise PreviewError("AUTH_REQUIRED", 401)
            if response.status_code == 403:
                raise PreviewError("SCOPE_FORBIDDEN", 403)
            if response.status_code != 200:
                raise PreviewError("DATA_READ_FAILED", 503)
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise PreviewError("DATA_READ_FAILED", 503) from None

    def rows(self, table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for offset in range(0, 200000, 500):
            page = self.get(f"/rest/v1/{table}", {**params, "limit": 500, "offset": offset})
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise PreviewError("DATA_READ_FAILED", 503)
            result.extend(page)
            if len(page) < 500:
                return result
        raise PreviewError("DATA_READ_FAILED", 503)

    def identity(self) -> dict[str, Any]:
        # Decoded claims are NOT trusted until the Auth server verifies the JWT.
        try:
            if not self._token or self._token.count(".") != 2:
                raise ValueError()
            segment = self._token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
            if not isinstance(claims, dict) or claims.get("iss") != self.url + "/auth/v1" or claims.get("aud") != "authenticated" \
                    or type(claims.get("exp")) not in (int, float) or not math.isfinite(claims["exp"]) or claims["exp"] <= time.time() \
                    or not claims.get("session_id"):
                raise ValueError()
            uuid_value(claims["session_id"])
            user = self.get("/auth/v1/user")
            if uuid_value(user.get("id")) != uuid_value(claims.get("sub")):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, PreviewError):
            raise PreviewError("AUTH_REQUIRED", 401) from None
        profile = self.rows("profiles", {"select": "id,status,global_role", "id": f"eq.{user['id']}", "order": "id.asc"})
        if len(profile) != 1 or profile[0].get("id") != user["id"] or profile[0].get("status") != "ACTIVE" \
                or profile[0].get("global_role") not in {"ADMIN", "EDITOR", "VIEWER"}:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        chains = self.rows("chains", {"select": "id,name,status", "status": "eq.ACTIVE", "order": "id.asc"})
        grants = self.rows("user_chain_access", {"select": "chain_id,can_view,can_run_forecast", "user_id": f"eq.{user['id']}", "order": "chain_id.asc"})
        visible = frozenset(row["id"] for row in chains)
        role = profile[0]["global_role"]
        execute = visible if role == "ADMIN" else frozenset(row["chain_id"] for row in grants
            if role == "EDITOR" and row.get("can_view") and row.get("can_run_forecast") and row["chain_id"] in visible)
        return {"user_id": user["id"], "role": role, "session_id": claims["session_id"],
                "chain_ids": visible, "execute_chain_ids": execute, "chains": chains}
