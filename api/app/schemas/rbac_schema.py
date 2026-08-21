import uuid
from pydantic import BaseModel, Field

class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)

class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    permissions: list[str]

class MembershipUpsert(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID

class ResourceGrantUpsert(BaseModel):
    resource_type: str
    resource_id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    permissions: list[str]
