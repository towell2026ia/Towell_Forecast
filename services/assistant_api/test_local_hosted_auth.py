"""Synthetic Auth protocol tests, not evidence of a real hosted login/session."""
import base64
import json
import unittest
from unittest.mock import patch

import httpx

from services.assistant_api.auth import AuthFailure
from services.assistant_api.local_hosted_auth import AUTH_ISSUER, AUTH_URL, HostedAuthVerifier

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"
KEY = "sb_publishable_synthetic_not_a_real_key"


def token(*, header=None, **overrides):
    def b64(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    claims = {"sub": USER, "session_id": SESSION, "iss": AUTH_ISSUER,
              "aud": "authenticated", "role": "authenticated", "iat": 900, "exp": 2000}
    claims.update(overrides)
    return b64(header or {"alg": "ES256", "typ": "JWT"}) + "." + b64(claims) + ".synthetic-signature"


class HostedAuthVerifierTests(unittest.TestCase):
    def make(self, handler=None):
        self.calls = []
        self.now = 1000
        def handle(request):
            self.calls.append(request)
            return handler(request) if handler else httpx.Response(200, json={"id": USER, "email": "fixture@example.invalid"})
        return HostedAuthVerifier(AUTH_URL, KEY, transport=httpx.MockTransport(handle), clock=lambda: self.now)

    def assert_status(self, status, operation):
        with self.assertRaises(AuthFailure) as failure:
            operation()
        self.assertEqual(failure.exception.status_code, status)
        self.assertEqual(str(failure.exception), "AUTH_001")

    def test_only_exact_official_project_and_publishable_keys(self):
        for url in ["http://bskoyqhbgrycpwhydnnr.supabase.co", "https://other.supabase.co",
                    AUTH_URL + ".example.invalid", AUTH_URL + "/auth/v1", AUTH_URL + "?redirect=true",
                    "https://operator@bskoyqhbgrycpwhydnnr.supabase.co", "http://127.0.0.1:8000"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                HostedAuthVerifier(url, KEY)
        for key in ["", "sb_secret_synthetic", "service_role", token(role="service_role"), KEY + "\n"]:
            with self.subTest(key_type=key[:12]), self.assertRaises(ValueError):
                HostedAuthVerifier(AUTH_URL, key)
        self.assertIsInstance(HostedAuthVerifier(AUTH_URL + "/", KEY), HostedAuthVerifier)

    def test_timeout_must_be_positive_finite(self):
        for timeout in [True, 0, -1, float("inf"), float("nan"), 301]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                HostedAuthVerifier(AUTH_URL, KEY, timeout=timeout)

    def test_valid_identity_is_fetched_every_time_with_tls_and_no_redirects(self):
        verifier = self.make()
        actual_client = httpx.Client
        with patch("services.assistant_api.local_hosted_auth.httpx.Client", wraps=actual_client) as client:
            for _ in range(2):
                session = verifier.verify(token())
                self.assertEqual((session["sub"], session["session_id"], session["exp"]), (USER, SESSION, 2000))
                self.assertEqual(session["user"]["id"], USER)
            for call in client.call_args_list:
                self.assertTrue(call.kwargs["verify"])
                self.assertFalse(call.kwargs["follow_redirects"])
                self.assertFalse(call.kwargs["trust_env"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(str(r.url) == AUTH_URL + "/auth/v1/user" and r.method == "GET" and not r.content for r in self.calls))
        self.assertEqual(self.calls[0].headers["apikey"], KEY)
        self.assertEqual(self.calls[0].headers["authorization"], "Bearer " + token())

    def test_wrong_claims_are_rejected_before_any_network_or_sql(self):
        verifier = self.make()
        cases = [None, "bad", "a.b.c", token().rsplit(".", 1)[0] + ".***", token(iss="https://other.supabase.co/auth/v1"),
                 token(aud="other"), token(aud=["authenticated"]), token(role="service_role"),
                 token(exp=1000), token(exp=True), token(exp=float("nan")), token(exp=float("inf")),
                 token(exp="2000"), token(iat=1031), token(iat=2000), token(iat=None),
                 token(nbf=1001), token(nbf=True), token(nbf=None), token(sub="not-a-uuid"),
                 token(session_id=None), token(session_id="not-a-uuid"), token(is_anonymous=True),
                 token(header={"alg": "none"})]
        for value in cases:
            with self.subTest(case=str(value)[:20]):
                self.assert_status(401, lambda: verifier.verify(value))
        self.assertEqual(self.calls, [])

    def test_forged_signature_is_not_accepted_without_auth_server_acceptance(self):
        verifier = self.make(lambda _: httpx.Response(401, json={"message": "invalid signature"}))
        self.assert_status(401, lambda: verifier.verify(token()))
        self.assertEqual(len(self.calls), 1)

    def test_auth_identity_must_match_sub_and_not_be_anonymous(self):
        for user in [{"id": OTHER}, {"id": USER, "is_anonymous": True}]:
            verifier = self.make(lambda _, user=user: httpx.Response(200, json=user))
            self.assert_status(401, lambda: verifier.verify(token()))

    def test_no_offline_or_redirect_fallback(self):
        for status in [302, 429, 500, 503]:
            verifier = self.make(lambda _, status=status: httpx.Response(status, headers={"Location": "https://example.invalid/"}))
            self.assert_status(503, lambda: verifier.verify(token()))
            self.assertEqual(len(self.calls), 1)
        def failed(request):
            raise httpx.ConnectError("synthetic outage", request=request)
        verifier = self.make(failed)
        self.assert_status(503, lambda: verifier.verify(token()))

    def test_invalid_remote_json_is_unavailable_not_locally_substituted(self):
        for response in [httpx.Response(200, content=b"not json"), httpx.Response(200, json=[]),
                         httpx.Response(200, json={"id": "invalid"})]:
            verifier = self.make(lambda _, response=response: response)
            with self.assertRaises(AuthFailure):
                verifier.verify(token())

    def test_expiry_and_local_logout_are_rechecked_after_auth_request(self):
        def elapsed(_):
            self.now = 2000
            return httpx.Response(200, json={"id": USER})
        verifier = self.make(elapsed)
        self.assert_status(401, lambda: verifier.verify(token()))
        def revoked(_):
            verifier.revoke_verified({"session_id": SESSION, "exp": 2000})
            return httpx.Response(200, json={"id": USER})
        verifier = self.make(revoked)
        self.assert_status(401, lambda: verifier.verify(token()))

    def test_logout_denies_the_verified_session_locally_without_permission_cache(self):
        verifier = self.make()
        session = verifier.verify(token())
        verifier.revoke_verified(session)
        self.assert_status(401, lambda: verifier.verify(token()))
        self.assert_status(401, lambda: verifier.verify(token(exp=2100)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(verifier.verify(token(session_id=OTHER))["sub"], USER)
        # A new process has no local deny-list; hosted/global revocation was not proven.
        other_process = self.make()
        self.assertEqual(other_process.verify(token())["sub"], USER)

    def test_revoke_convenience_verifies_before_denying(self):
        verifier = self.make()
        verifier.revoke(token())
        self.assert_status(401, lambda: verifier.verify(token()))
        self.assertEqual(len(self.calls), 1)

    def test_no_credentials_or_tokens_are_logged_or_exposed_in_repr(self):
        verifier = self.make()
        with patch("builtins.print") as printed, patch("logging.log") as logged:
            verifier.verify(token())
            printed.assert_not_called()
            logged.assert_not_called()
        self.assertNotIn(KEY, repr(verifier))
        self.assertNotIn(token(), repr(verifier))


if __name__ == "__main__":
    unittest.main()
