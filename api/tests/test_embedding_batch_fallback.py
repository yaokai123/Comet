import asyncio

import pytest

from app.core.llm import client as client_module
from app.core.llm.client import LLMClient


@pytest.mark.parametrize("error", [TimeoutError("timeout"), Exception("boom")])
def test_embedding_batch_fallback_splits_failed_batch(monkeypatch, error):
    calls = []

    async def fake_post(url, *, headers, json, timeout):
        calls.append(list(json["input"]))
        if len(json["input"]) > 1:
            raise error
        return {
            "data": [
                {"index": 0, "embedding": [float(len(json["input"][0]))]},
            ],
            "usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(client_module, "_post_with_retry", fake_post)
    client = LLMClient("http://127.0.0.1:8081/v1", "local", "bge-m3")

    result = asyncio.run(client.embed(["aa", "bbbb"], dimensions=1024))

    assert result == [[2.0], [4.0]]
    assert calls == [["aa", "bbbb"], ["aa"], ["bbbb"]]


def test_embedding_single_item_failure_still_raises(monkeypatch):
    async def fake_post(url, *, headers, json, timeout):
        raise TimeoutError("timeout")

    monkeypatch.setattr(client_module, "_post_with_retry", fake_post)
    client = LLMClient("http://127.0.0.1:8081/v1", "local", "bge-m3")

    with pytest.raises(TimeoutError):
        asyncio.run(client.embed(["aa"], dimensions=1024))
