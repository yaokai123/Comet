import asyncio
import io
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image as PillowImage
from pydantic import ValidationError

from app.core.exceptions import BizError
from app.core.knowledge.federated_retrieval import format_federated_result
from app.core.knowledge.quality_filter import QualityEvidence
from app.core.rag.chat_images import (
    validate_chat_image_keys,
    validate_chat_image_upload,
)
from app.schemas.chat_schema import ChatStreamRequest
from app.services.chat_service import (
    ChatService,
    _number_citations,
    _requires_history_vision,
)


def _png_bytes(size=(32, 32)) -> bytes:
    output = io.BytesIO()
    PillowImage.new("RGB", size, (20, 120, 220)).save(output, format="PNG")
    return output.getvalue()


def test_upload_validation_uses_real_image_content():
    content = _png_bytes()
    validated = validate_chat_image_upload(
        filename="safe.png", content_type="image/png", content=content
    )
    assert validated.extension == ".png"
    assert validated.width == validated.height == 32
    assert b"exif" not in validated.content.lower()
    with pytest.raises(BizError):
        validate_chat_image_upload(
            filename="fake.jpg", content_type="image/jpeg", content=content
        )


def test_chat_request_rejects_more_than_six_images():
    with pytest.raises(ValidationError):
        ChatStreamRequest(message="看图", image_keys=[str(index) for index in range(7)])


def test_chat_request_gets_stable_client_request_id():
    request_id = uuid.uuid4()
    first = ChatStreamRequest(message="幂等", client_request_id=request_id)
    retry = ChatStreamRequest.model_validate(first.model_dump())
    assert first.client_request_id == request_id
    assert retry.client_request_id == request_id


def test_chat_request_generates_request_id_for_legacy_client():
    assert isinstance(ChatStreamRequest(message="兼容旧客户端").client_request_id, uuid.UUID)


@pytest.mark.parametrize("query", ["这张图说明了什么", "上面的表格里有什么", "再仔细看一下细节"])
def test_history_vision_requires_explicit_visual_reference(query):
    assert _requires_history_vision(query, True)


def test_unrelated_followup_does_not_resend_history_images():
    assert not _requires_history_vision("帮我把结论整理成邮件", True)
    assert not _requires_history_vision("这张图是什么", False)


def test_image_key_validation_enforces_owner_and_existence(monkeypatch):
    user_id = uuid.uuid4()
    key = f"{user_id}/chat/{uuid.uuid4()}.png"

    class Storage:
        async def exists(self, candidate):
            return candidate == key

    monkeypatch.setattr("app.core.rag.chat_images.get_storage", lambda: Storage())
    assert asyncio.run(validate_chat_image_keys(user_id, [key])) == [key]
    with pytest.raises(BizError):
        asyncio.run(validate_chat_image_keys(uuid.uuid4(), [key]))
    with pytest.raises(BizError):
        asyncio.run(validate_chat_image_keys(user_id, [f"{user_id}/chat/../secret.png"]))


def test_history_images_are_grouped_into_original_human_message(monkeypatch):
    user_id = uuid.uuid4()
    key = f"{user_id}/chat/{uuid.uuid4()}.png"
    image_message = SimpleNamespace(
        id=uuid.uuid4(),
        role="user",
        content="第一轮图片",
        meta_data={"image_keys": [key]},
    )
    assistant = SimpleNamespace(
        id=uuid.uuid4(), role="assistant", content="已看到", meta_data=None
    )
    current = SimpleNamespace(
        id=uuid.uuid4(), role="user", content="继续追问", meta_data=None
    )

    class Messages:
        async def recent_history(self, _conv_id, _limit):
            return [image_message, assistant, current]

    class Storage:
        async def exists(self, candidate):
            return candidate == key

        async def get(self, _key):
            return _png_bytes()

    service = ChatService(SimpleNamespace())
    service.msg_repo = Messages()
    storage = Storage()
    monkeypatch.setattr("app.core.rag.chat_images.get_storage", lambda: storage)
    monkeypatch.setattr("app.services.chat_service.get_storage", lambda: storage)
    messages, has_images = asyncio.run(service._history_messages(uuid.uuid4(), user_id))
    assert has_images is True
    assert isinstance(messages[0].content, list)
    assert messages[0].content[0]["text"] == "第一轮图片"
    assert messages[0].content[1]["type"] == "image_url"
    assert messages[1].content == "已看到"


def test_image_evidence_carries_unified_reference_number():
    evidence = QualityEvidence(
        evidence_id="image-evidence",
        source_type="image",
        source_name="企业知识库",
        content="设备铭牌显示额定功率 20kW，序列号为 A-01。",
        authority=0.88,
        final_score=0.9,
        metadata={"citation_index": 2},
    )
    rendered = format_federated_result(
        {"evidence": [evidence], "conflicts": [], "sources": []}
    )
    assert "图片引用编号=[2]" in rendered
    citations = _number_citations([{"source_id": "doc"}, {"source_id": "image"}])
    assert [item["citation_index"] for item in citations] == [1, 2]
