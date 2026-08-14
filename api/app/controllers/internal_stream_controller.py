"""Authenticated direct forwarding endpoint for cross-instance SSE wakeups."""

from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core.realtime.durable_stream import EventEnvelope, push_local

router = APIRouter(prefix="/internal/streams", tags=["internal_streams"])


class ForwardedEvent(BaseModel):
    id: int
    run_id: str
    event: str
    data: dict


@router.post("/{run_id}/notify", include_in_schema=False)
async def notify_stream_instance(
    run_id: uuid.UUID,
    body: ForwardedEvent,
    secret: str = Header(default="", alias="X-Stream-Forward-Secret"),
):
    expected = settings.stream_forward_secret
    if not expected or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="forbidden")
    if body.run_id != str(run_id):
        raise HTTPException(status_code=422, detail="run id mismatch")
    await push_local(EventEnvelope(body.id, body.run_id, body.event, body.data))
    return {"ok": True}
