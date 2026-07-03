from fastapi import APIRouter, Request
from core.supabase_client import get_supabase

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/manifest")
async def mcp_manifest(request: Request):
    """MCP server manifest — describes available resources and actions."""
    return {
        "name": "microsaas-engine",
        "version": "0.1.0",
        "resources": [
            {
                "uri": "microsaas://events",
                "name": "Usage Events",
                "description": "Tenant usage event stream",
                "mimeType": "application/json",
            },
            {
                "uri": "microsaas://milestones",
                "name": "Milestones",
                "description": "Tenant milestone progress",
                "mimeType": "application/json",
            },
            {
                "uri": "microsaas://pipeline",
                "name": "Opportunity Pipeline",
                "description": "Research agent validated opportunities",
                "mimeType": "application/json",
            },
        ],
        "tools": [
            {
                "name": "log_event",
                "description": "Log a usage event for a tenant",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "event_type": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["event_type"],
                },
            }
        ],
    }


@router.get("/resources/events")
async def mcp_events(request: Request):
    tenant_id = request.state.tenant_id
    db = get_supabase()
    events = (
        db.table("usage_events")
        .select("event_type,metadata,created_at")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
        .data
    )
    return {"contents": [{"uri": "microsaas://events", "mimeType": "application/json", "text": str(events)}]}
