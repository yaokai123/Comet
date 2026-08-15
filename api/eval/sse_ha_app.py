"""Isolated Docker probe app for the production durable SSE implementation."""

from __future__ import annotations

import os
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from app.controllers.internal_stream_controller import router as internal_stream_router
from app.controllers.chat_controller import router as chat_router
from app.core.dependencies import get_current_project_id, get_current_user
from app.core.exceptions import register_exception_handlers
from app.core.realtime import durable_stream
from app.db import postgres, redis
from app.db.postgres import SessionLocal
from app.models.user_model import User
from app.services.chat_service import ChatService

PROBE_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000042")
INSTANCE_ID = os.getenv("STREAM_INSTANCE_ID", "unknown")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with SessionLocal() as session:
        await session.execute(
            insert(User)
            .values(
                id=PROBE_USER_ID,
                username="sse-ha-probe",
                password_hash="not-used",
                is_active=True,
                token_version=0,
            )
            .on_conflict_do_nothing(index_elements=[User.username])
        )
        await session.commit()
    yield
    await postgres.close()
    await redis.close()


app = FastAPI(lifespan=lifespan)
app.include_router(internal_stream_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
register_exception_handlers(app)


async def _probe_user():
    async with SessionLocal() as session:
        return await session.get(User, PROBE_USER_ID)


async def _no_project():
    return None


app.dependency_overrides[get_current_user] = _probe_user
app.dependency_overrides[get_current_project_id] = _no_project


async def _deterministic_generate_events(
    _self, _user_id, _conv, body, _attachments, _citations
):
    """Exercise the real ChatService persistence path without external model cost."""
    answer = f"ECHO:{body.message}"
    for offset in range(0, len(answer), 4):
        await asyncio.sleep(0.08)
        yield {"type": "token", "text": answer[offset : offset + 4]}


ChatService._generate_events = _deterministic_generate_events


class ProbeEvent(BaseModel):
    event: str = "token"
    data: dict


@app.get("/probe/health")
async def health():
    return {"ok": True, "instance_id": INSTANCE_ID}


@app.post("/probe/runs/{stream_key}")
async def create_probe_run(stream_key: str):
    run, claimed = await durable_stream.claim_run(
        stream_type="probe", stream_key=stream_key, user_id=PROBE_USER_ID
    )
    if claimed:
        await durable_stream.append_event(
            run.id,
            "meta",
            {"stream_key": stream_key, "producer_instance": INSTANCE_ID},
        )
    return {"run_id": str(run.id), "claimed": claimed, "instance_id": INSTANCE_ID}


@app.post("/probe/run/{run_id}/events")
async def append_probe_event(run_id: uuid.UUID, body: ProbeEvent):
    if body.event in {"done", "error"}:
        envelope = await durable_stream.finish_run(
            run_id,
            event=body.event,
            data=body.data,
            error=str(body.data.get("message", "")) if body.event == "error" else None,
        )
    else:
        envelope = await durable_stream.append_event(run_id, body.event, body.data)
    return {"id": envelope.id, "instance_id": INSTANCE_ID}


@app.get("/probe/runs/{stream_key}/events")
async def probe_events(
    stream_key: str,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    run = await durable_stream.latest_run(
        stream_type="probe", stream_key=stream_key, user_id=PROBE_USER_ID
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError:
        cursor = 0

    async def relay():
        yield f": instance={INSTANCE_ID}\n\n"
        async for envelope in durable_stream.iter_events(run.id, after_id=cursor):
            yield ": ping\n\n" if envelope is None else durable_stream.sse(envelope)

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"X-Instance-ID": INSTANCE_ID, "X-Accel-Buffering": "no"},
    )
