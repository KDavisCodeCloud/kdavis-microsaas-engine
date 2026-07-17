"""
Real bug found 2026-07-17: a genuine browser login was rejected with
"The specified alg value is not allowed" — verify_jwt only ever supported
HS256 (a shared secret), but this Supabase project has migrated to the
newer asymmetric JWT signing keys model and signs real session tokens
with ES256 against a public JWKS endpoint (confirmed live against this
project's own /auth/v1/.well-known/jwks.json). Covers both paths: HS256
(legacy projects, and this repo's own synthetic test tokens elsewhere)
and ES256 via a mocked JWKS client using a real generated EC key pair —
no network access required, dispatch is on the token's own declared alg.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request

import api.middleware.auth as auth_module
from api.middleware.auth import verify_jwt


def _request_with_bearer(token: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


def test_hs256_token_still_verifies_legacy_path(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    token = jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "test-secret",
        algorithm="HS256",
    )
    payload = verify_jwt(_request_with_bearer(token))
    assert payload["sub"] == "user-1"


def test_hs256_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "the-real-secret")
    token = jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "a-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_request_with_bearer(token))
    assert exc.value.status_code == 401


def test_es256_token_verifies_via_jwks(monkeypatch):
    # Simulates a real Supabase session token from a project using
    # asymmetric JWT signing keys — no network call, get_signing_key_from_jwt
    # is monkeypatched to hand back the matching public key directly.
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    token = jwt.encode(
        {"sub": "user-2", "aud": "authenticated", "exp": int(time.time()) + 3600, "app_metadata": {"role": "admin"}},
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key-id"},
    )

    class _FakeSigningKey:
        key = public_key

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, tok):
            return _FakeSigningKey()

    monkeypatch.setattr(auth_module, "_get_jwk_client", lambda: _FakeJWKClient())

    payload = verify_jwt(_request_with_bearer(token))
    assert payload["sub"] == "user-2"
    assert payload["app_metadata"]["role"] == "admin"


def test_es256_token_with_wrong_key_is_rejected(monkeypatch):
    signing_key_pair = ec.generate_private_key(ec.SECP256R1())
    wrong_public_key = ec.generate_private_key(ec.SECP256R1()).public_key()

    token = jwt.encode(
        {"sub": "user-3", "aud": "authenticated", "exp": int(time.time()) + 3600},
        signing_key_pair,
        algorithm="ES256",
    )

    class _FakeSigningKey:
        key = wrong_public_key

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, tok):
            return _FakeSigningKey()

    monkeypatch.setattr(auth_module, "_get_jwk_client", lambda: _FakeJWKClient())

    with pytest.raises(HTTPException) as exc:
        verify_jwt(_request_with_bearer(token))
    assert exc.value.status_code == 401


def test_expired_token_returns_token_expired_detail(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    token = jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) - 60},
        "test-secret",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_request_with_bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "token_expired"


def test_missing_bearer_token_is_401():
    scope = {"type": "http", "headers": []}
    with pytest.raises(HTTPException) as exc:
        verify_jwt(Request(scope))
    assert exc.value.status_code == 401
