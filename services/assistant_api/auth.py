"""Authentication and authorization are separate, server-enforced concerns."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .settings import Settings

READ = "READ"
EXECUTE = "EXECUTE"
ADMIN = "ADMIN"
ROLE_PERMISSIONS = {
    "manager": frozenset({READ, EXECUTE, ADMIN}),
    "editor": frozenset({READ, EXECUTE}),
    "reader": frozenset({READ}),
}


class AuthFailure(Exception):
    def __init__(self, code: str, status_code: int):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    permissions: frozenset[str]
    session_id: str


class AuthProvider(ABC):
    @abstractmethod
    def authenticate(self, *, token: str | None, actor_id: str | None,
                     client_host: str | None) -> Principal: ...

    def health(self) -> dict[str, str]:
        return {"status": "healthy", "provider": type(self).__name__}


def authorize(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise AuthFailure("AUTH_002", 403)


def _role_for(user_id: str, settings: Settings) -> str | None:
    if user_id in settings.manager_ids:
        return "manager"
    if user_id in settings.editor_ids:
        return "editor"
    if user_id in settings.reader_ids:
        return "reader"
    return None


class LocalAuthProvider(AuthProvider):
    """Loopback-only development adapter; never enabled for staging/production."""

    def __init__(self, settings: Settings):
        if settings.strict_auth:
            raise ValueError("local_auth_forbidden_in_strict_environment")
        self.settings = settings

    def authenticate(self, *, token: str | None, actor_id: str | None,
                     client_host: str | None) -> Principal:
        if client_host not in {"127.0.0.1", "::1", "testclient"}:
            raise AuthFailure("AUTH_001", 401)
        if self.settings.assistant_token and (not token or
                not hmac.compare_digest(token, self.settings.assistant_token)):
            raise AuthFailure("AUTH_001", 401)
        user_id = actor_id or ""
        role = _role_for(user_id, self.settings)
        if not role and not any((self.settings.manager_ids, self.settings.editor_ids,
                                 self.settings.reader_ids)) and user_id == "local-manager":
            role = "manager"
        if not role:
            raise AuthFailure("AUTH_002", 403)
        return Principal(user_id, role, ROLE_PERMISSIONS[role], f"local:{user_id}")


def _decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class SignedAuthProvider(AuthProvider):
    """Verify short-lived server-minted claims; never trust browser role fields."""

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.assistant_token or len(settings.assistant_token) < 32:
            raise ValueError("strict_auth_configuration_missing")

    def authenticate(self, *, token: str | None, actor_id: str | None,
                     client_host: str | None) -> Principal:
        del actor_id, client_host
        if not token or token.count(".") != 1:
            raise AuthFailure("AUTH_001", 401)
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.settings.assistant_token.encode("utf-8"),
                                encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(_decode(signature), expected):
                raise AuthFailure("AUTH_001", 401)
            claims: dict[str, Any] = json.loads(_decode(encoded))
            user_id = claims.get("user_id")
            session_id = claims.get("session_id")
            expires = claims.get("exp")
            issued = claims.get("iat")
            if not isinstance(user_id, str) or not user_id or not isinstance(session_id, str) or \
                    not session_id or not isinstance(expires, int) or not isinstance(issued, int) or \
                    expires < time.time() or issued > time.time() + 30 or expires - issued > 300 or \
                    claims.get("aud") != "forecast-towell-fastapi":
                raise AuthFailure("AUTH_001", 401)
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
            raise AuthFailure("AUTH_001", 401) from exc
        role = _role_for(user_id, self.settings)
        if not role:
            raise AuthFailure("AUTH_002", 403)
        return Principal(user_id, role, ROLE_PERMISSIONS[role], session_id)


class SupabaseAuthProvider(AuthProvider):
    def authenticate(self, *, token: str | None, actor_id: str | None,
                     client_host: str | None) -> Principal:
        raise NotImplementedError("supabase_auth_not_connected")

    def health(self) -> dict[str, str]:
        return {"status": "disabled", "provider": "SupabaseAuthProvider"}
