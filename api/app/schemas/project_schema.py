from pydantic import BaseModel, Field


class ProjectUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default="#155EEF", max_length=16)
