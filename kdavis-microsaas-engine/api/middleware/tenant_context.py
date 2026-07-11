from fastapi import Request, HTTPException
from api.middleware.auth import verify_jwt


async def tenant_context_middleware(request: Request, call_next):
    # Public paths — no auth required
    PUBLIC_PATHS = {
        "/health", "/docs", "/openapi.json", "/redoc", "/webhooks/stripe", "/ceo/health",
        # n8n-triggered automation, not tenant-JWT-scoped — authenticated via
        # MARKETING_API_KEY inside api/routers/marketing.py instead.
        "/marketing/research", "/marketing/campaign",
    }
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    payload = verify_jwt(request)
    tenant_id = payload.get("sub")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant_id in token")

    request.state.tenant_id = tenant_id
    request.state.role = payload.get("role", "authenticated")
    return await call_next(request)
