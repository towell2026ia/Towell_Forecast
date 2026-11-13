"""GET-only, request-scoped Supabase access. No service key or RLS bypass."""
from __future__ import annotations

import base64
import json
import math
import re
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


def matches_source(value, context):
    return isinstance(value, dict) and all(value.get(field) == context[field] for field in ("source_hash", "generation"))


class SupabaseReadClient:
    def __init__(self, url: str, key: str, token: str | None = None, *, timeout: int = 10,
                 transport: httpx.BaseTransport | None = None, auth_url: str | None = None):
        self.url, self._key, self._token = url.rstrip("/"), key, token
        self.auth_url = (auth_url or url).rstrip("/")
        self.timeout, self.transport = timeout, transport
        self._operational_context: dict[str, str | int] | None = None

    def __repr__(self) -> str:
        return "SupabaseReadClient(GET-only, credentials redacted)"

    def bind(self, token: str) -> "SupabaseReadClient":
        return SupabaseReadClient(self.url, self._key, token, timeout=self.timeout, transport=self.transport, auth_url=self.auth_url)

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
        if table in {"monthly_observations", "monthly_observations_current", "portal_monthly_observations_current"}:
            raise PreviewError("DATA_READ_FAILED", 503)
        return self._pages(f"/rest/v1/{table}", params)

    def operational_context(self) -> dict[str, str | int]:
        if self._operational_context is None:
            rows = self.get("/rest/v1/rpc/operational_read_context")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise PreviewError("DATA_READ_FAILED", 503)
            row = rows[0]
            try:
                source_hash = row["source_hash"]
                generation = row["generation"]
                first, last = row["first_period"], row["last_period"]
                if (not isinstance(source_hash, str) or not re.fullmatch("[0-9a-f]{64}", source_hash)
                        or type(generation) is not int or generation < 1
                        or any(not isinstance(period, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period)
                               for period in (first, last)) or first > last):
                    raise ValueError()
            except (TypeError, ValueError, KeyError, AttributeError):
                raise PreviewError("DATA_READ_FAILED", 503) from None
            self._operational_context = {"source_hash": source_hash, "generation": generation,
                                         "first_period": first, "last_period": last}
        return dict(self._operational_context)

    def observation_rows(self, params: dict[str, Any], *, mode: str = "OPERATIONAL",
                         available_through: str | None = None,
                         chain_id: str | None = None) -> list[dict[str, Any]]:
        context = self.operational_context()
        arguments = {**params, "p_source_hash": context["source_hash"],
                     "p_generation": context["generation"], "p_mode": mode}
        if chain_id is not None:
            arguments["p_chain_id"] = uuid_value(chain_id)
        if available_through is not None:
            arguments["p_available_through"] = available_through
        return self._pages("/rest/v1/rpc/operational_read_observations", arguments)

    def inventory_rows(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        context = self.operational_context()
        return self._pages("/rest/v1/rpc/operational_read_inventory",
                           {**params, "p_source_hash": context["source_hash"], "p_generation": context["generation"]})

    def catalog_rows(self, *, chain_id=None, as_of=None):
        context = self.operational_context()
        params = {"p_source_hash": context["source_hash"], "p_generation": context["generation"],
                  "order": "chain_id.asc,product_id.asc"}
        if chain_id:
            params["p_chain_id"] = uuid_value(chain_id)
        if as_of:
            params["p_as_of"] = as_of + "-01"
        return self._pages("/rest/v1/rpc/operational_read_catalog", params)

    def _pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for offset in range(0, 200000, 500):
            page = self.get(path, {**params, "limit": 500, "offset": offset})
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
            if not isinstance(claims, dict) or claims.get("iss") != self.auth_url + "/auth/v1" or claims.get("aud") != "authenticated" \
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
