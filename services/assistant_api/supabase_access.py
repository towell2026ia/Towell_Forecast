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


SALES_SOURCE_FIELDS = ("source_hash", "generation", "first_period", "last_period", "sales_contract",
    "sales_source", "sales_sheet", "sales_file_sha256", "sales_training_cutoff", "authorized_source_hash", "inventory_source")


def require_sales_context(context, *, required=False):
    """The configured Towell path cannot degrade to an unpinned legacy input."""
    if not required and not (isinstance(context, dict) and context.get("sales_contract") == "towell-sales-v1"):
        return context
    if (not isinstance(context, dict) or context.get("sales_contract") != "towell-sales-v1"
            or context.get("sales_source") != "TOWELL" or context.get("sales_sheet") != "Towell"
            or context.get("inventory_source") != "ESTADISTICA"
            or type(context.get("generation")) is not int or context["generation"] < 1
            or any(not isinstance(context.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", context[key])
                   for key in ("source_hash", "sales_file_sha256", "authorized_source_hash"))
            or context["source_hash"] != context["authorized_source_hash"]
            or any(not isinstance(context.get(key), str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", context[key])
                   for key in ("first_period", "last_period", "sales_training_cutoff"))
            or not context["first_period"] <= context["sales_training_cutoff"] <= context["last_period"]
            or type(context.get("sales_available")) is not bool):
        raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)
    if not context["sales_available"]:
        raise PreviewError("SALES_SOURCE_UNAVAILABLE", 503)
    return context


def matches_source(value, context):
    if not isinstance(value, dict) or not isinstance(context, dict):
        return False
    if value.get("sales_contract") == "towell-sales-v1" or context.get("sales_contract") == "towell-sales-v1":
        try:
            require_sales_context(value, required=True)
            require_sales_context(context, required=True)
        except PreviewError:
            return False
        # A transport may omit a null explanatory field. This does not change
        # source identity; every accredited source/cutoff field must still match.
        return all(value.get(field) == context[field] for field in SALES_SOURCE_FIELDS)
    return (type(value.get("generation")) is int and type(context.get("generation")) is int
            and all(value.get(field) == context.get(field) for field in ("source_hash", "generation")))


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
            if response.status_code != 200:
                try:
                    code = response.json().get("code")
                except (ValueError, AttributeError):
                    code = None
                if code in {"SALES_SOURCE_UNAVAILABLE", "OFFICIAL_SOURCE_MISMATCH", "REPLACED_SOURCE_AUDIT_ONLY"}:
                    raise PreviewError(code, response.status_code)
                if response.status_code == 403:
                    raise PreviewError("SCOPE_FORBIDDEN", 403)
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
            context = {"source_hash": source_hash, "generation": generation,
                       "first_period": first, "last_period": last}
            if "sales_contract" in row:
                if (row.get("sales_contract") != "towell-sales-v1" or row.get("sales_source") != "TOWELL"
                        or row.get("sales_sheet") != "Towell" or row.get("inventory_source") != "ESTADISTICA"
                        or row.get("authorized_source_hash") != source_hash
                        or type(row.get("sales_available")) is not bool
                        or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sales_file_sha256", "")))):
                    raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)
                context.update({key: row[key] for key in (
                    "sales_contract", "sales_source", "sales_sheet", "inventory_source", "sales_available",
                    "sales_file_sha256", "authorized_source_hash")})
                if not row["sales_available"]:
                    context["sales_unavailable_reason"] = "Sin cálculo: fuente de ventas no disponible"
                if "sales_training_cutoff" in row:
                    cutoff = row["sales_training_cutoff"]
                    if (not isinstance(cutoff, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", cutoff)
                            or cutoff < first or cutoff > last):
                        raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)
                    context["sales_training_cutoff"] = cutoff
            self._operational_context = context
        return dict(self._operational_context)

    def refresh_operational_context(self):
        self._operational_context = None
        return self.operational_context()

    def observation_rows(self, params: dict[str, Any], *, mode: str = "OPERATIONAL",
                         available_through: str | None = None,
                         chain_id: str | None = None) -> list[dict[str, Any]]:
        context = self.operational_context()
        if context.get("sales_available") is False:
            raise PreviewError("SALES_SOURCE_UNAVAILABLE", 503)
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
