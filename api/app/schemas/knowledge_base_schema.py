"""知识库请求/响应 Schema。"""
import uuid
from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="知识库名称")
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = Field(default=None, max_length=32, description="emoji 图标")
    color: str | None = Field(default=None, max_length=16, description="卡片主题色")
    project_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = Field(default=None, max_length=32)
    color: str | None = Field(default=None, max_length=16)
    project_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class MoveToKbRequest(BaseModel):
    kb_id: str = Field(..., description="目标知识库 id")


class ChatEnabledRequest(BaseModel):
    chat_enabled: bool = Field(..., description="是否参与对话检索")
