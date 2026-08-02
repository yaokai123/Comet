"""First-value funnel telemetry. Event payloads are deliberately small and user-scoped."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.product_event_model import ProductEvent
from app.models.user_model import User
from app.schemas.product_event_schema import FirstValueFunnel, ProductEventRequest

router = APIRouter(prefix="/product-events", tags=["product-events"])


@router.post("")
async def create_product_event(
    body: ProductEventRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    session.add(ProductEvent(user_id=user.id, event_name=body.event_name, properties=body.properties))
    await session.commit()
    return success(message="已记录")


@router.get("/first-value")
async def first_value_funnel(
    days: int = Query(default=30, ge=1, le=90),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Return the current user's privacy-scoped first-value funnel for the workspace."""
    base = [
        ProductEvent.user_id == user.id,
        ProductEvent.created_at >= func.now() - func.make_interval(days=days),
    ]
    async def event_count(name: str, distinct_property: str | None = None) -> int:
        target = func.count(ProductEvent.id)
        if distinct_property:
            target = func.count(func.distinct(ProductEvent.properties[distinct_property].astext))
        return int(await session.scalar(select(target).where(*base, ProductEvent.event_name == name)) or 0)
    captured = await event_count("capture_created")
    # Normalize to a monotonic funnel. A user may open several citations from one answer,
    # or complete an item whose creation fell outside the selected time window.
    processed = min(await event_count("capture_processed"), captured)
    questioned = min(await event_count("source_question_submitted"), processed)
    cited = min(await event_count("cited_answer_received"), questioned)
    reviewed = min(await event_count("citation_opened"), cited)
    failed = await event_count("capture_processing_failed", "document_id")
    recovered = min(await event_count("capture_retry_recovered", "document_id"), failed)
    data = FirstValueFunnel(
        days=days,
        captured=captured,
        processed=processed,
        questioned=questioned,
        cited=cited,
        reviewed=reviewed,
        failed=failed,
        recovered=recovered,
        outstanding_failures=max(failed - recovered, 0),
        processing_rate=round(processed / captured, 4) if captured else 0,
        question_rate=round(questioned / processed, 4) if processed else 0,
        citation_rate=round(cited / questioned, 4) if questioned else 0,
        review_rate=round(reviewed / cited, 4) if cited else 0,
    )
    return success(data)
