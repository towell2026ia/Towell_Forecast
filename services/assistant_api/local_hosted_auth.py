"""Hosted identity verification for local SQL, never an operational data proxy.

Every acceptance contacts the configured project's Auth endpoint. Local logout
denial lasts in this process until the presented token's expiry; this is not a
claim of global hosted session revocation or validation of local role grants.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
import time
from typing import Any, Callable, Mapping
import uuid

import httpx

from .auth import AuthFailure

AUTH_URL = "https://bskoyqhbgrycpwhydnnr.supabase.co"
AUTH_ISSUER = AUTH_URL + "/auth/v1"


def _invalid(reason="invalid_identity"):
    return AuthFailure("AUTH_001", 401, reason)


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_uuid")
    return value


def _epoch(value: Any) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid_epoch")
    return value


def _part(segment: str) -> dict[str, Any]:
    raw = base64.b64decode(segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("invalid_claims")
    return result


class HostedAuthVerifier:
    """Validate identity only. Authorization remains in local profiles and RLS."""

    def __init__(self, auth_url: str, publishable_key: str, *, timeout: float = 10,
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time):
        if auth_url not in {AUTH_URL, AUTH_URL + "/"}:
            raise ValueError("local_hosted_auth_project_rejected")
        if (not isinstance(publishable_key, str)
                or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{9,}", publishable_key)):
            raise ValueError("local_hosted_auth_publishable_key_required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("local_hosted_auth_timeout_rejected")
        self._key = publishable_key
        self._timeout, self._transport, self._clock = timeout, transport, clock
        self._revoked: dict[str, int | float] = {}
        self._lock = threading.Lock()

    def __repr__(self):
        return "HostedAuthVerifier(official Auth GET-only, credentials redacted)"

    def _claims(self, token: str | None) -> dict[str, Any]:
        try:
            if not isinstance(token, str) or not 0 < len(token) <= 16384 or token.count(".") != 2:
                raise ValueError("invalid_token")
            header, body, signature = token.split(".")
            if not signature or _part(header).get("alg") not in {"HS256", "RS256", "ES256"}:
                raise ValueError("invalid_algorithm")
            base64.b64decode(signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True)
            claims = _part(body)
            now = self._clock()
            if (claims.get("iss") != AUTH_ISSUER or claims.get("aud") != "authenticated"
                    or claims.get("role") != "authenticated" or claims.get("is_anonymous") is True
                    or _epoch(claims.get("exp")) <= now
                    or _epoch(claims.get("iat")) > now + 30
                    or claims["iat"] >= claims["exp"]
                    or "nbf" in claims and _epoch(claims["nbf"]) > now):
                raise ValueError("invalid_claims")
            _uuid(claims.get("sub"))
            _uuid(claims.get("session_id"))
            return claims
        except (ValueError, TypeError, OverflowError, UnicodeError, binascii.Error, json.JSONDecodeError):
            raise _invalid() from None

    def _check_revoked(self, session_id: str, now: float):
        with self._lock:
            self._revoked = {sid: expires for sid, expires in self._revoked.items() if expires > now}
            if session_id in self._revoked:
                raise _invalid("local_session_revoked")

    def verify(self, token: str | None) -> dict[str, Any]:
        claims = self._claims(token)
        self._check_revoked(claims["session_id"], self._clock())
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport,
                              verify=True, follow_redirects=False, trust_env=False) as client:
                response = client.get(AUTH_URL + "/auth/v1/user", headers={
                    "apikey": self._key, "Authorization": "Bearer " + token,
                    "Cache-Control": "no-store",
                })
            if response.status_code in {401, 403}:
                raise _invalid()
            if response.status_code != 200:
                raise AuthFailure("AUTH_001", 503, "hosted_auth_unavailable")
            user = response.json()
            if (not isinstance(user, dict) or _uuid(user.get("id")) != claims["sub"]
                    or user.get("is_anonymous") is True):
                raise _invalid()
        except (httpx.HTTPError, ValueError, TypeError, OverflowError, UnicodeError):
            raise AuthFailure("AUTH_001", 503, "hosted_auth_unavailable") from None
        # Reject a token expiring or logged out while the remote request ran.
        if claims["exp"] <= self._clock():
            raise _invalid("token_expired")
        self._check_revoked(claims["session_id"], self._clock())
        return {**claims, "user": user}

    def revoke_verified(self, session: Mapping[str, Any]) -> None:
        """Internal call with verify()'s result, never a browser-provided body."""
        try:
            sid, expires = _uuid(session.get("session_id")), _epoch(session.get("exp"))
        except (ValueError, TypeError, OverflowError):
            raise _invalid() from None
        with self._lock:
            self._revoked[sid] = max(expires, self._revoked.get(sid, expires))

    def revoke(self, token: str | None) -> None:
        self.revoke_verified(self.verify(token))
