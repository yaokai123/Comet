"""消息推送渠道请求 schema。"""
from pydantic import BaseModel, Field


class NotifyChannelCreate(BaseModel):
    channel_type: str = Field(..., pattern="^(serverchan|wecom|dingtalk|webhook)$")
    name: str = Field("", max_length=64)
    target: str = Field(..., min_length=1, max_length=2048)  # SendKey / webhook URL
    enabled: bool = True


class NotifyChannelUpdate(BaseModel):
    name: str | None = Field(None, max_length=64)
    target: str | None = Field(None, max_length=2048)  # 不传=不改 key
    enabled: bool | None = None
