"""
Regression coverage for a real, systemic production bug found 2026-07-17,
fixed in two passes:

Pass 1 (incomplete): tenant_context_middleware had no exemption for CORS
preflight (OPTIONS) requests, so it rejected every preflight with 401
before CORSMiddleware ever got a chance to answer it. Fixed by exempting
OPTIONS in the middleware. Deployed and verified live — but Kelvin still
saw "Failed to fetch" clicking reject afterward.

Pass 2 (the actual full fix): CORSMiddleware was registered in main.py
BEFORE tenant_context_middleware, which — because Starlette prepends each
added middleware, so whichever is added LAST ends up OUTERMOST at runtime
— made tenant_context_middleware the outer layer and CORSMiddleware the
INNER one (only reached via call_next). Fixing the OPTIONS case alone
wasn't enough: any REAL request tenant_context_middleware rejected (a
genuinely invalid/missing JWT — the actual case when the browser's
session token is stale or absent) returned its 401 directly, without ever
calling call_next, so that response bypassed CORSMiddleware too and
carried no Access-Control-Allow-Origin header. A browser can't read a
cross-origin response with no CORS header regardless of status code — it
reports it to JavaScript as a generic "Failed to fetch", identical to the
preflight symptom, just one layer deeper. Fixed by reordering
registration in main.py so CORSMiddleware is added last (outermost),
wrapping every response including early auth-rejection ones.
"""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

# conftest.py sets ALLOWED_ORIGINS to this value by default (setdefault,
# so it won't pick up a real ALLOWED_ORIGINS already exported in a dev
# shell) — using it here, rather than the real production domain, is what
# keeps these tests deterministic regardless of the environment they run
# in. The bug this file covers is about middleware ORDERING, not about
# any specific origin string, so which allowed origin is used doesn't
# matter for what's being verified.
_TEST_ORIGIN = "http://localhost:3000"


def _preflight_headers(origin: str = _TEST_ORIGIN):
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }


def test_options_preflight_on_protected_route_is_not_rejected():
    resp = client.options("/pipeline/opp-1/review", headers=_preflight_headers())
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == _TEST_ORIGIN


def test_options_preflight_carries_no_auth_and_still_succeeds():
    # The whole point of a preflight is that it never carries the real
    # Authorization header — confirming it succeeds with none present is
    # what actually exercises the bug (a request WITH auth would have
    # passed even before the fix, on a route with no admin check).
    resp = client.options("/factory/build/opp-1", headers=_preflight_headers())
    assert resp.status_code == 200


def test_actual_post_without_auth_is_still_401_unaffected_by_the_fix():
    # The OPTIONS exemption must not leak into real requests — a real POST
    # with no Authorization header still needs to be rejected.
    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "rejected"}, headers={"Origin": _TEST_ORIGIN},
    )
    assert resp.status_code == 401


def test_real_401_response_still_carries_cors_headers():
    # This is the actual bug Kelvin hit: a genuine auth failure (missing/
    # invalid JWT) on a real cross-origin POST must still carry CORS
    # headers, or the browser blocks it and reports "Failed to fetch"
    # instead of surfacing the real 401 to the frontend's error handling.
    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "rejected"}, headers={"Origin": _TEST_ORIGIN},
    )
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == _TEST_ORIGIN


def test_real_403_response_still_carries_cors_headers():
    # Same check for the non-admin-role rejection path (403), not just 401.
    import time
    import jwt as pyjwt

    token = pyjwt.encode(
        {"sub": "someone", "app_metadata": {"role": "marketing"}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    resp = client.post(
        "/pipeline/opp-1/review",
        json={"decision": "rejected"},
        headers={"Origin": _TEST_ORIGIN, "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.headers.get("access-control-allow-origin") == _TEST_ORIGIN
