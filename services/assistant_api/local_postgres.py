"""Development-only Supabase protocol adapter backed by the real local SQL/RLS.

No production credentials, hosted Auth, proxying, general SQL endpoint or fake
commercial responses. Windows DPAPI protects the operator's local credentials.
"""
from __future__ import annotations

import base64
import csv
import ctypes
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import parse_qs
import uuid

import httpx

NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
TABLES = frozenset({"profiles", "chains", "categories", "products", "user_chain_access",
    "import_profile_versions", "import_profiles", "import_batches", "source_evidence",
    "forecast_runs", "forecast_horizons", "forecast_calculations", "forecast_calculation_horizons",
    "forecast_decisions", "forecast_selection_events", "forecast_current_selections",
    "forecast_capture_sessions", "forecast_live_evaluations", "forecast_learning_events",
    "portal_monthly_observations_current", "portal_history_periods", "audit_log"})


def unprotect(path: Path) -> str:
    """Decrypt only in process memory; never send a secret through shell output."""
    if sys.platform != "win32":
        raise ValueError("local_windows_credential_required")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = path.read_bytes()
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, destination = Blob(len(raw), buffer), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None,
            None, None, 0x1, ctypes.byref(destination)):
        raise ValueError("local_credential_decryption_failed")
    try:
        return ctypes.string_at(destination.data, destination.size).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(destination.data)


def _json(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)  # SQL value_text / quantity_text preserve the exact literal.
    raise TypeError(type(value).__name__)


def _response(status, payload=None, headers=None):
    return httpx.Response(status, content=b"" if payload is None else
        json.dumps(payload, default=_json, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "X-Local-Development": "postgresql-rls",
                 **(headers or {})})


class LocalPostgresGateway:
    def __init__(self, settings):
        settings.validate()
        if settings.local_postgres_config is None or settings.app_env not in {"development", "test"}:
            raise ValueError("local_postgres_development_only")
        self.settings = settings
        self.config = json.loads(settings.local_postgres_config.read_text(encoding="utf-8-sig"))
        c = self.config
        if (c.get("host") != "127.0.0.1" or type(c.get("port")) is not int
                or not 1024 <= c["port"] <= 65535
                or not re.fullmatch(r"gateb_official_[a-z0-9_]+", c.get("database", ""))
                or not re.fullmatch(r"[0-9a-f]{32}", c.get("lab_id", ""))
                or not str(c.get("system_identifier", "")).isdigit()):
            raise ValueError("local_postgres_target_rejected")
        self.password = unprotect(Path(c["password_dpapi_file"]))
        self.users = {u["email"].casefold(): u for u in c["users"]}
        self.sessions, self.refresh = {}, {}
        self.signing = secrets.token_bytes(32)  # Ephemeral local Auth fixture, not a provider API key.
        self.transport = LocalSQLTransport(self)
        with self.connect():
            pass

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        c = psycopg.connect(host="127.0.0.1", hostaddr="127.0.0.1", port=self.config["port"],
            dbname=self.config["database"], user="postgres", password=self.password,
            autocommit=True, row_factory=dict_row, connect_timeout=5,
            options="-c statement_timeout=30000 -c lock_timeout=10000")
        try:
            proof = c.execute("""select host(inet_server_addr()) address,inet_server_port() port,
                current_database() db,current_setting('listen_addresses') listen,
                current_setting('data_directory') directory,current_setting('gateb.lab_id',true) marker,
                (select system_identifier::text from pg_control_system()) system_id""").fetchone()
            if (proof["address"] != "127.0.0.1" or proof["listen"] != "127.0.0.1"
                    or proof["port"] != self.config["port"] or proof["db"] != self.config["database"]
                    or proof["marker"] != self.config["lab_id"]
                    or proof["system_id"] != str(self.config["system_identifier"])
                    or Path(proof["directory"]).resolve() != Path(self.config["data_directory"]).resolve()):
                raise ValueError("local_postgres_marker_mismatch")
            return c
        except Exception:
            c.close()
            raise

    def _user(self, token):
        session = self.sessions.get(token)
        return session if session and session["exp"] > time.time() else None

    def _issue(self, user):
        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
        now = int(time.time())
        claims = {"sub": user["id"], "aud": "authenticated", "role": "authenticated",
                  "iss": self.settings.supabase_url.rstrip("/") + "/auth/v1",
                  "iat": now, "exp": now + 3600, "session_id": str(uuid.uuid4())}
        encoded = b64(b'{"alg":"HS256","typ":"JWT"}') + "." + b64(json.dumps(claims).encode())
        token = encoded + "." + b64(hmac.new(self.signing, encoded.encode(), hashlib.sha256).digest())
        refresh = secrets.token_urlsafe(40)
        self.sessions[token] = {**claims, "user": user}
        self.refresh[refresh] = token
        identity = {"id": user["id"], "email": user["email"], "aud": "authenticated",
                    "role": "authenticated", "app_metadata": {"provider": "local-postgres"},
                    "user_metadata": {}, "created_at": datetime.now(timezone.utc).isoformat()}
        return {"access_token": token, "refresh_token": refresh, "token_type": "bearer",
                "expires_in": 3600, "expires_at": claims["exp"], "user": identity}

    def handle(self, request):
        path = request.url.path
        params = parse_qs(request.url.query.decode(), keep_blank_values=True)
        params = {k: v[-1] for k, v in params.items()}
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        session = self._user(token)
        try:
            body = json.loads(request.content or b"{}")
        except (ValueError, UnicodeError):
            return _response(400, {"code": "LOCAL_INVALID_JSON"})
        if not isinstance(body, dict):
            return _response(400, {"code": "LOCAL_INVALID_JSON"})
        if path == "/auth/v1/token" and request.method == "POST":
            if params.get("grant_type") == "refresh_token":
                old = self._user(self.refresh.get(body.get("refresh_token"), ""))
                return _response(200, self._issue(old["user"])) if old else _response(401, {"msg": "Invalid local session"})
            user = self.users.get(str(body.get("email", "")).casefold())
            if not user or not hmac.compare_digest(str(body.get("password", "")),
                    unprotect(Path(user.get("password_dpapi_file", self.config["password_dpapi_file"])))):
                return _response(400, {"error": "invalid_grant", "error_description": "Invalid local credentials"})
            return _response(200, self._issue(user))
        if not session:
            return _response(401, {"code": "AUTH_REQUIRED", "message": "Local authentication required"})
        if path == "/auth/v1/user" and request.method == "GET":
            return _response(200, {"id": session["sub"], "email": session["user"]["email"],
                "aud": "authenticated", "role": "authenticated", "app_metadata": {}, "user_metadata": {}})
        if path == "/auth/v1/logout" and request.method == "POST":
            self.sessions.pop(token, None)
            return _response(204)
        if not path.startswith("/rest/v1/"):
            return _response(404, {"code": "LOCAL_ENDPOINT_UNAVAILABLE"})
        try:
            return self.query(request, session, params, body)
        except Exception as exc:
            import psycopg
            if isinstance(exc, psycopg.Error):
                status = 403 if exc.sqlstate == "42501" else 409 if exc.sqlstate == "40001" else 400
                return _response(status, {"code": exc.sqlstate, "message": exc.diag.message_primary})
            if isinstance(exc, ValueError):
                return _response(400, {"code": "LOCAL_QUERY_UNSUPPORTED", "message": str(exc)})
            raise

    def query(self, request, session, params, body):
        from psycopg import sql
        resource = request.url.path.removeprefix("/rest/v1/")
        args, values = [], []
        with self.connect() as c, c.transaction():
            c.execute("set transaction read only")
            c.execute("set local role authenticated")
            c.execute("select set_config('request.jwt.claim.sub',%s,true)", (session["sub"],))
            if resource.startswith("rpc/"):
                name = resource[4:]
                if not name.startswith("operational_read_") or not NAME.fullmatch(name):
                    return _response(403, {"code": "LOCAL_WRITE_DISABLED"})
                supplied = body if request.method == "POST" else {k: v for k, v in params.items() if k.startswith("p_")}
                signatures = c.execute("""select p.oid,p.proargnames,
                    array(select format_type(x,null) from unnest(p.proargtypes::oid[]) x) types
                    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                    where n.nspname='public' and p.proname=%s""", (name,)).fetchall()
                matches = [p for p in signatures if set(supplied).issubset(set(p["proargnames"] or []))]
                if len(matches) != 1:
                    raise ValueError("RPC signature not uniquely supported")
                signature = dict(zip(matches[0]["proargnames"], matches[0]["types"]))
                for key, value in supplied.items():
                    kind = signature[key]
                    if kind.endswith("[]") and isinstance(value, str):
                        value = json.loads(value) if value.startswith("[") else value.strip("{}").split(",")
                    args.append(sql.SQL("{} => %s::{}").format(sql.Identifier(key), sql.SQL(kind)))
                    values.append(value)
                origin = sql.SQL("public.{}({})").format(sql.Identifier(name), sql.SQL(",").join(args))
            else:
                if resource not in TABLES or request.method not in {"GET", "HEAD"}:
                    return _response(403, {"code": "LOCAL_WRITE_DISABLED"})
                origin = sql.Identifier("public", resource)
            fields = params.get("select", "*").split(",")
            if fields != ["*"] and not all(NAME.fullmatch(x) for x in fields):
                raise ValueError("Unsupported projection")
            projection = sql.SQL("*") if fields == ["*"] else sql.SQL(",").join(map(sql.Identifier, fields))
            conditions = []
            def logical(expression):
                """Cursor predicates, parsed into identifiers and bound values only."""
                def split(text):
                    items, level, start = [], 0, 0
                    for index, char in enumerate(text):
                        if char == "(":
                            level += 1
                        elif char == ")":
                            level -= 1
                        elif char == "," and level == 0:
                            items.append(text[start:index])
                            start = index + 1
                    if level != 0:
                        raise ValueError("Unbalanced cursor")
                    return [*items, text[start:]]
                for operator in ("and", "or"):
                    if expression.startswith(operator + "(") and expression.endswith(")"):
                        return sql.SQL("(") + sql.SQL(" " + operator + " ").join(
                            logical(item) for item in split(expression[len(operator)+1:-1])) + sql.SQL(")")
                column, operator, value = expression.split(".", 2)
                if not NAME.fullmatch(column) or operator not in {"eq", "gt", "gte", "lt", "lte"}:
                    raise ValueError("Unsupported cursor predicate")
                values.append(value)
                symbol = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
                return sql.SQL("{} {} %s").format(sql.Identifier(column), sql.SQL(symbol))
            for key, expression in params.items():
                if key in {"select", "order", "limit", "offset"} or key.startswith("p_"):
                    continue
                if key == "or" and expression.startswith("(") and expression.endswith(")"):
                    conditions.append(logical("or" + expression))
                    continue
                if not NAME.fullmatch(key):
                    raise ValueError("Unsupported filter")
                operator, _, value = expression.partition(".")
                if operator in {"eq", "neq", "gte", "lte", "gt", "lt"}:
                    symbol = {"eq": "=", "neq": "<>", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[operator]
                    conditions.append(sql.SQL("{} {} %s").format(sql.Identifier(key), sql.SQL(symbol)))
                    values.append(value)
                elif operator == "in" and value.startswith("(") and value.endswith(")"):
                    members = next(csv.reader([value[1:-1]]))
                    conditions.append(sql.SQL("{} in ({})").format(sql.Identifier(key),
                        sql.SQL(",").join(sql.Placeholder() for _ in members)))
                    values.extend(members)
                elif expression in {"is.null", "not.is.null"}:
                    conditions.append(sql.SQL("{} is {}null").format(sql.Identifier(key),
                        sql.SQL("not " if expression.startswith("not.") else "")))
                else:
                    raise ValueError("Unsupported filter operator")
            base = sql.SQL(" from {} as q").format(origin)
            if conditions:
                base += sql.SQL(" where ") + sql.SQL(" and ").join(conditions)
            total = c.execute(sql.SQL("select count(*) n") + base, values).fetchone()["n"]
            statement = sql.SQL("select {} ").format(projection) + base
            if params.get("order"):
                ordering = []
                for item in params["order"].split(","):
                    col, *directions = item.split(".")
                    if not NAME.fullmatch(col) or any(d not in {"asc", "desc", "nullsfirst", "nullslast"} for d in directions):
                        raise ValueError("Unsupported order")
                    ordering.append(sql.Identifier(col) + sql.SQL(" desc" if "desc" in directions else " asc")
                        + sql.SQL(" nulls first" if "nullsfirst" in directions else " nulls last"))
                statement += sql.SQL(" order by ") + sql.SQL(",").join(ordering)
            offset, limit = int(params.get("offset", 0)), min(int(params.get("limit", 1000)), 1000)
            if "range" in request.headers:
                start, end = map(int, request.headers["range"].split("-"))
                offset, limit = start, min(end - start + 1, 1000)
            if offset < 0 or limit < 0:
                raise ValueError("Invalid range")
            rows = [] if request.method == "HEAD" else c.execute(statement + sql.SQL(" limit %s offset %s"), [*values, limit, offset]).fetchall()
            headers = {"Content-Range": f"{offset}-{offset + len(rows) - 1}/{total}" if rows else f"*/{total}", "Range-Unit": "items"}
            if "application/vnd.pgrst.object+json" in request.headers.get("accept", ""):
                if len(rows) != 1:
                    return _response(406, {"code": "PGRST116", "details": f"The result contains {len(rows)} rows"}, headers)
                return _response(200, rows[0], headers)
            return _response(200, None if request.method == "HEAD" else rows, headers)

    def mount(self, app):
        from fastapi import Request
        from fastapi.responses import Response

        async def dispatch(request: Request):
            from starlette.concurrency import run_in_threadpool
            body = await request.body()
            query = request.url.query
            local = httpx.Request(request.method, self.settings.supabase_url + request.url.path
                + ("?" + query if query else ""), headers=dict(request.headers), content=body)
            answer = await run_in_threadpool(self.handle, local)
            return Response(content=answer.content, status_code=answer.status_code, headers=dict(answer.headers))

        # FastAPI resolves postponed annotations from module globals.
        dispatch.__annotations__["request"] = Request
        for path in ("/auth/v1/{operation:path}", "/rest/v1/{operation:path}"):
            app.add_api_route(path, dispatch, methods=["GET", "HEAD", "POST"], include_in_schema=False)


class LocalSQLTransport(httpx.BaseTransport):
    """Backend reads use exactly the SQL handler exposed to the local browser."""
    def __init__(self, gateway):
        self.gateway = gateway

    def handle_request(self, request):
        request.read()
        return self.gateway.handle(request)
