"""
Regression coverage for a real, systemic production bug found 2026-07-17:
tenant_context_middleware had no exemption for CORS preflight (OPTIONS)
requests, so it rejected every preflight with 401 before CORSMiddleware
(registered earlier in main.py, so it sits INSIDE this middleware in the
actual execution order) ever got a chance to answer it with proper
Access-Control-Allow-* headers. The browser then reports the real request
as a generic network failure ("Failed to fetch"), not a clean 401 — this
silently broke every authenticated POST/PATCH/DELETE endpoint called from
a browser (reject/approve on the Opportunities tab, the build trigger,
brief generation), not just one button, until this was actually clicked
in a live browser for the first time.
"""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _preflight_headers():
    return {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }


def test_options_preflight_on_protected_route_is_not_rejected():
    resp = client.options("/pipeline/opp-1/review", headers=_preflight_headers())
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


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
    resp = client.post("/pipeline/opp-1/review", json={"decision": "rejected"})
    assert resp.status_code == 401
