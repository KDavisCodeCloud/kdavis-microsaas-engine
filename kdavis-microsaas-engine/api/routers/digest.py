from fastapi import APIRouter, Request, HTTPException
from core.retention.digest_generator import generate_digest

router = APIRouter(prefix="/digest", tags=["digest"])


@router.post("/preview/{tenant_id}")
async def preview_digest(tenant_id: str, request: Request):
    result = generate_digest(tenant_id)
    if result is None:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "no_usage", "message": "No usage events in the last 7 days — digest skipped"}
        )
    return result
