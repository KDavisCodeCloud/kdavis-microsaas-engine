from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from api.middleware.auth import verify_jwt


async def tenant_context_middleware(request: Request, call_next):
    # Public paths — no auth required
    PUBLIC_PATHS = {
        "/health", "/docs", "/openapi.json", "/redoc", "/webhooks/stripe", "/ceo/health",
        # n8n-triggered automation, not tenant-JWT-scoped — authenticated via
        # MARKETING_API_KEY inside api/routers/marketing.py instead.
        "/marketing/research", "/marketing/campaign",
        "/marketing/apollo-list", "/marketing/dm-sequences", "/marketing/seo-content",
        "/marketing/send-sequences",
    }
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # HTTPException raised inside @app.middleware("http") is NOT caught by
    # FastAPI's normal exception handlers (a Starlette BaseHTTPMiddleware
    # gotcha) — it crashes as a raw 500 instead of the intended status code.
    # Must catch and convert to a JSONResponse manually here.
    try:
        payload = verify_jwt(request)
        tenant_id = payload.get("sub")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="No tenant_id in token")
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    request.state.tenant_id = tenant_id
    # NOTE: top-level "role" in a Supabase JWT is PostgREST's reserved DB-role
    # claim (always "authenticated" for any logged-in user) — NOT the app's
    # custom admin/marketing/rnd role. That lives in app_metadata (service-role
    # writable only; user_metadata is client-editable via auth.updateUser() and
    # must never be trusted for authorization). Matches the RLS fix in
    # supabase/migrations/20260716000009_fix_admin_rls_claim.sql.
    request.state.role = payload.get("app_metadata", {}).get("role", "authenticated")
    return await call_next(request)
