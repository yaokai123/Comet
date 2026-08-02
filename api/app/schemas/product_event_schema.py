from typing import Literal

from pydantic import BaseModel, Field


class ProductEventRequest(BaseModel):
    event_name: Literal[
        "capture_created", "capture_processed", "source_question_started",
        "source_question_submitted", "cited_answer_received", "citation_opened",
        "capture_processing_failed", "capture_retry_started", "capture_retry_recovered",
    ]
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class FirstValueFunnel(BaseModel):
    days: int
    captured: int
    processed: int
    questioned: int
    cited: int
    reviewed: int
    failed: int
    recovered: int
    outstanding_failures: int
    processing_rate: float
    question_rate: float
    citation_rate: float
    review_rate: float
