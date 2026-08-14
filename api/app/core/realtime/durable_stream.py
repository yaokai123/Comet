"""PostgreSQL durable SSE log with Redis route hints and direct instance forwarding."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.logging import get_logger
from app.db.postgres import SessionLocal
from app.db.redis import get_redis
from app.models.stream_event_model import StreamEvent, StreamRun

logger = get_logger(__name__)
_TERMINAL_EVENTS = {"done", "error"}
_ROUTE_PREFIX = "sse-route:v1:"
_local_subscribers: dict[str, set[asyncio.Queue[int]]] = {}
_subscriber_lock = asyncio.Lock()


@dataclass(slots=True, frozen=True)
class EventEnvelope:
    id: int
    run_id: str
    event: str
    data: dict


def instance_id() -> str:
    return settings.stream_instance_id.strip() or socket.gethostname()


def _route_key(run_id: str) -> str:
    return f"{_ROUTE_PREFIX}{run_id}"


async def create_run(*, stream_type: str, stream_key: str, user_id: uuid.UUID) -> StreamRun:
    async with SessionLocal() as session:
        run = StreamRun(
            stream_type=stream_type,
            stream_key=stream_key,
            user_id=user_id,
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def claim_run(
    *, stream_type: str, stream_key: str, user_id: uuid.UUID
) -> tuple[StreamRun, bool]:
    """Atomically claim a stream key; return the active run when another worker owns it."""
    try:
        return await create_run(
            stream_type=stream_type, stream_key=stream_key, user_id=user_id
        ), True
    except IntegrityError:
        active = await latest_run(
            stream_type=stream_type, stream_key=stream_key, user_id=user_id
        )
        if active is None:
            raise
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.stream_run_stale_seconds
        )
        if active.status == "running" and active.updated_at < cutoff:
            await finish_run(
                active.id,
                event="error",
                data={"message": "生成实例异常退出，请重新发送"},
                error="stale stream run recovered",
            )
            return await create_run(
                stream_type=stream_type, stream_key=stream_key, user_id=user_id
            ), True
        return active, False


async def latest_run(
    *, stream_type: str, stream_key: str, user_id: uuid.UUID
) -> StreamRun | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(StreamRun)
            .where(
                StreamRun.stream_type == stream_type,
                StreamRun.stream_key == stream_key,
                StreamRun.user_id == user_id,
            )
            .order_by(StreamRun.created_at.desc())
            .limit(1)
        )


async def append_event(run_id: uuid.UUID | str, event: str, data: dict) -> EventEnvelope:
    run_uuid = uuid.UUID(str(run_id))
    async with SessionLocal() as session:
        row = StreamEvent(run_id=run_uuid, event_type=event, data_json=data)
        session.add(row)
        await session.execute(
            update(StreamRun)
            .where(StreamRun.id == run_uuid, StreamRun.status == "running")
            .values(updated_at=datetime.now(timezone.utc))
        )
        await session.commit()
        await session.refresh(row)
        envelope = EventEnvelope(row.id, str(run_uuid), event, data)
    await _notify(envelope)
    return envelope


async def finish_run(
    run_id: uuid.UUID | str,
    *,
    event: str,
    data: dict,
    message_id: uuid.UUID | None = None,
    error: str | None = None,
) -> EventEnvelope:
    if event not in _TERMINAL_EVENTS:
        raise ValueError("finish_run requires done or error event")
    run_uuid = uuid.UUID(str(run_id))
    async with SessionLocal() as session:
        run = await session.get(StreamRun, run_uuid, with_for_update=True)
        if run is None:
            raise ValueError("stream run does not exist")
        if run.status != "running":
            existing = await session.scalar(
                select(StreamEvent)
                .where(StreamEvent.run_id == run_uuid, StreamEvent.event_type.in_(_TERMINAL_EVENTS))
                .order_by(StreamEvent.id.desc())
                .limit(1)
            )
            if existing is None:
                raise ValueError("stream run is terminal without terminal event")
            return EventEnvelope(existing.id, str(run_uuid), existing.event_type, existing.data_json)
        row = StreamEvent(run_id=run_uuid, event_type=event, data_json=data)
        session.add(row)
        run.status = "done" if event == "done" else "error"
        run.final_message_id = message_id
        run.error_msg = error[:2000] if error else None
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        envelope = EventEnvelope(row.id, str(run_uuid), event, data)
    await _notify(envelope)
    return envelope


async def _register_route(run_id: str) -> None:
    payload = json.dumps(
        {"instance_id": instance_id(), "internal_url": settings.stream_internal_url},
        ensure_ascii=False,
    )
    try:
        redis = get_redis()
        await redis.hset(_route_key(run_id), instance_id(), payload)
        await redis.expire(_route_key(run_id), settings.stream_route_ttl_seconds)
    except Exception as exc:
        logger.warning("SSE route registration failed; DB polling remains active: %s", exc)


async def _unregister_route(run_id: str) -> None:
    try:
        await get_redis().hdel(_route_key(run_id), instance_id())
    except Exception:
        pass


async def push_local(envelope: EventEnvelope) -> None:
    async with _subscriber_lock:
        queues = list(_local_subscribers.get(envelope.run_id, set()))
    for queue in queues:
        try:
            queue.put_nowait(envelope.id)
        except asyncio.QueueFull:
            # A full hint queue does not lose data; consumers query PostgreSQL by id.
            pass


async def _notify(envelope: EventEnvelope) -> None:
    await push_local(envelope)
    if not settings.stream_forward_secret:
        return
    try:
        routes = await get_redis().hvals(_route_key(envelope.run_id))
    except Exception as exc:
        logger.warning("SSE route lookup failed; DB polling remains active: %s", exc)
        return
    for raw in routes:
        try:
            route = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if route.get("instance_id") == instance_id() or not route.get("internal_url"):
            continue
        url = str(route["internal_url"]).rstrip("/") + f"/api/internal/streams/{envelope.run_id}/notify"
        try:
            async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
                await client.post(
                    url,
                    headers={"X-Stream-Forward-Secret": settings.stream_forward_secret},
                    json={
                        "id": envelope.id,
                        "run_id": envelope.run_id,
                        "event": envelope.event,
                        "data": envelope.data,
                    },
                )
        except Exception as exc:
            logger.warning("Direct SSE forwarding failed; DB polling will recover: %s", exc)


async def events_after(run_id: uuid.UUID | str, after_id: int) -> list[EventEnvelope]:
    run_uuid = uuid.UUID(str(run_id))
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(StreamEvent)
                    .where(StreamEvent.run_id == run_uuid, StreamEvent.id > after_id)
                    .order_by(StreamEvent.id)
                    .limit(500)
                )
            ).all()
        )
    return [EventEnvelope(row.id, str(run_uuid), row.event_type, row.data_json) for row in rows]


async def resume_snapshot(run_id: uuid.UUID | str, through_id: int) -> dict:
    """Rebuild the visible partial answer through a confirmed SSE cursor."""
    run_uuid = uuid.UUID(str(run_id))
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(StreamEvent)
                    .where(
                        StreamEvent.run_id == run_uuid,
                        StreamEvent.id <= through_id,
                    )
                    .order_by(StreamEvent.id)
                )
            ).all()
        )
    content = ""
    citations: list[dict] = []
    tool_calls: list[dict] = []
    for row in rows:
        data = row.data_json or {}
        if row.event_type == "token":
            content += str(data.get("text", ""))
        elif row.event_type == "citation":
            citations = list(data.get("citations") or [])
        elif row.event_type == "tool_start":
            tool_calls.append(
                {
                    "tool": data.get("tool"),
                    "query": data.get("query", ""),
                    "status": "running",
                }
            )
        elif row.event_type == "tool_result":
            for item in reversed(tool_calls):
                if item.get("tool") == data.get("tool") and item["status"] == "running":
                    item["status"] = data.get("status", "success")
                    break
    return {"content": content, "citations": citations, "tool_calls": tool_calls}


async def iter_events(
    run_id: uuid.UUID | str,
    *,
    after_id: int = 0,
) -> AsyncGenerator[EventEnvelope | None, None]:
    run_key = str(run_id)
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    async with _subscriber_lock:
        _local_subscribers.setdefault(run_key, set()).add(queue)
    await _register_route(run_key)
    cursor = max(0, after_id)
    heartbeat_at = asyncio.get_running_loop().time()
    try:
        while True:
            rows = await events_after(run_key, cursor)
            for row in rows:
                cursor = row.id
                yield row
                if row.event in _TERMINAL_EVENTS:
                    return
            now = asyncio.get_running_loop().time()
            if now - heartbeat_at >= max(10, settings.stream_route_ttl_seconds // 2):
                await _register_route(run_key)
                heartbeat_at = now
            try:
                await asyncio.wait_for(
                    queue.get(), timeout=settings.stream_poll_interval_seconds
                )
            except TimeoutError:
                # Heartbeat and fallback polling. None becomes an SSE comment.
                yield None
    finally:
        should_unregister = False
        async with _subscriber_lock:
            subscribers = _local_subscribers.get(run_key)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    _local_subscribers.pop(run_key, None)
                    should_unregister = True
        if should_unregister:
            await _unregister_route(run_key)


def sse(envelope: EventEnvelope) -> str:
    return (
        f"id: {envelope.id}\n"
        f"event: {envelope.event}\n"
        f"data: {json.dumps(envelope.data, ensure_ascii=False)}\n\n"
    )
