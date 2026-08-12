import json
from types import SimpleNamespace

from app.core.llm.chat_model import build_chat_model
from app.core.llm.client import LLMClient
from app.core.security import encrypt_secret


def test_responses_text_supports_output_text_and_nested_content():
    assert LLMClient._responses_text({"output_text": "直接结果"}) == "直接结果"
    assert LLMClient._responses_text({
        "output": [{"content": [{"type": "output_text", "text": "嵌套结果"}]}]
    }) == "嵌套结果"


def test_build_chat_model_enables_responses_and_encrypted_headers():
    config = SimpleNamespace(
        model_name="gpt-5.5",
        api_key_encrypted=encrypt_secret("primary-secret"),
        base_url="https://example.invalid",
        wire_api="responses",
        reasoning_effort="xhigh",
        extra_headers_encrypted=encrypt_secret(json.dumps({"x-actor": "actor-secret"})),
        store_responses=False,
    )
    model = build_chat_model(config, streaming=False)
    assert model.use_responses_api is True
    assert model.reasoning == {"effort": "xhigh"}
    assert model.default_headers == {"x-actor": "actor-secret"}
    assert model.store is False
