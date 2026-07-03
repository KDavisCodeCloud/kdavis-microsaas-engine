from fastapi import Request, HTTPException
from api.middleware.auth import verify_jwt


async def tenant_context_middleware(request: Request, call_next):
    # Health check bypasses auth
    if request.url.path == "/health":
        return await call_next(request)

    payload = verify_jwt(request)
    tenant_id = payload.get("sub")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant_id in token")

    request.state.tenant_id = tenant_id
    request.state.role = payload.get("role", "authenticated")
    return await call_next(request)
