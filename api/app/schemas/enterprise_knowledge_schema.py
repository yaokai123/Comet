"""API schemas for enterprise knowledge governance."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


_SECRET_KEYS = {"api_key", "apikey", "password", "secret", "token", "access_token"}


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    connector_type: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9_-]+$")
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = Field(default=None, max_length=512)

    @field_validator("config")
    @classmethod
    def reject_inline_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        offending = {str(key).casefold() for key in value} & _SECRET_KEYS
        if offending:
            names = ", ".join(sorted(offending))
            raise ValueError(f"connector secrets must use secret_ref, not config: {names}")
        return value
