import asyncio

import pytest

from app.core.llm import client as client_module
from app.core.llm.client import LLMClient


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8082/rerank", True),
        ("http://localhost:8082/rerank", True),
        ("http://bge-reranker:80/rerank", True),
        ("http://host.docker.internal:8082/rerank", True),
        ("https://yohohoho.online/v1/chat/completions", False),
    ],
)
def test_local_url_detection(url, expected):
    assert client_module._is_local_url(url) is expected


def test_tei_rerank_request_and_response(monkeypatch):
    captured = {}

    async def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return [
            {"index": 1, "score": 0.2},
            {"index": 0, "score": 0.9},
            {"index": 2, "score": 0.1},
        ]

    monkeypatch.setattr(client_module, "_post_with_retry", fake_post)
    reranker = LLMClient(
        base_url="http://bge-reranker:80",
        api_key="local",
        model_name="BAAI/bge-reranker-v2-m3",
        wire_api="tei",
    )
    result = asyncio.run(reranker.rerank("query", ["a", "b", "c"], top_n=2))
    assert result == [(0, 0.9), (1, 0.2)]
    assert captured["url"] == "http://bge-reranker:80/rerank"
    assert captured["json"] == {
        "query": "query",
        "texts": ["a", "b", "c"],
        "raw_scores": False,
    }


def test_tei_rerank_empty_documents_skips_request():
    reranker = LLMClient("http://local", "local", "model", wire_api="tei")
    assert asyncio.run(reranker.rerank("query", [], top_n=5)) == []
