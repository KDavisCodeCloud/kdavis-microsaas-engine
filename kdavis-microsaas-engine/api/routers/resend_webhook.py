"""
api/routers/resend_webhook.py

POST /webhooks/resend -- Resend delivery events (bounce, complaint).

Public path (Resend has no session), authenticated by the Svix signature
over the raw body with RESEND_WEBHOOK_SECRET. If the secret is not
configured the route refuses with 503 rather than accepting unsigned
events: an unauthenticated endpoint that suppresses addresses would let
anyone stop any sequence.
"""

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request

from core.delivery_events import SignatureError, apply_delivery_failure, verify_svix_signature
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/resend")
async def resend_webhook(request: Request):
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="RESEND_WEBHOOK_SECRET not configured")
    body = await request.body()
    try:
        verify_svix_signature(secret, request.headers.get("svix-id", ""),
                              request.headers.get("svix-timestamp", ""),
                              request.headers.get("svix-signature", ""), body)
    except SignatureError as exc:
        log.warning("[ResendWebhook] rejected: %s", exc)
        raise HTTPException(status_code=401, detail=f"invalid signature: {exc}")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="body is not JSON")
    result = apply_delivery_failure(get_supabase(), event)
    log.info("[ResendWebhook] %s", result)
    return result
