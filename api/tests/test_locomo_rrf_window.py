import asyncio

from eval import clients
from eval.benchmarks.locomo.local_es_runner import _windowed_texts


def _turn(memory_id, session_id, speaker, text, session_date_time):
    return {
        "memory_id": memory_id,
        "sample_id": "conv-1",
        "session_id": session_id,
        "session_date_time": session_date_time,
        "dia_id": memory_id,
        "speaker": speaker,
        "text": text,
        "image_caption": None,
    }


def test_windowed_texts_add_time_and_stay_inside_session():
    turns = [
        _turn("D1:1", "session_1", "A", "first", "1 pm on 1 May, 2023"),
        _turn("D1:2", "session_1", "B", "second", "1 pm on 1 May, 2023"),
        _turn("D2:1", "session_2", "A", "third", "2 pm on 2 May, 2023"),
    ]

    rendered = _windowed_texts(turns, radius=1)

    assert "Session time: 1 pm on 1 May, 2023" in rendered[0]
    assert "Current turn (retrieval target): A: first" in rendered[0]
    assert "Next turn 1: B: second" in rendered[0]
    assert "third" not in rendered[1]
    assert "second" not in rendered[2]


def test_weighted_rrf_preserves_all_rankers_and_is_deterministic():
    fused = clients.weighted_rrf(
        [
            (["a", "b", "c"], 1.0),
            (["b", "c", "a"], 0.7),
            (["c", "b", "a"], 1.5),
        ],
        rank_constant=60,
    )

    assert [item_id for item_id, _ in fused] == ["b", "c", "a"]
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_weighted_rrf_counts_duplicate_only_once():
    duplicate = clients.weighted_rrf([(["a", "a", "b"], 1.0)], rank_constant=60)
    expected = clients.weighted_rrf([(["a", "b"], 1.0)], rank_constant=60)
    assert duplicate == expected


def test_rerank_view_uses_timestamp_and_center_turn_only():
    content = "\n".join(
        [
            "Session time: 1 pm on 1 May, 2023",
            "Previous turn 1: A: before",
            "Current turn (retrieval target): B: target",
            "Image: target image",
            "Next turn 1: A: after",
        ]
    )
    assert clients.rerank_view(content) == "\n".join(
        [
            "Session time: 1 pm on 1 May, 2023",
            "Current turn (retrieval target): B: target",
            "Image: target image",
        ]
    )


def test_rerank_sources_rrf_returns_trace(monkeypatch):
    class FakeEs:
        async def search(self, *, index, body):
            assert index
            source_ids = body["query"]["bool"]["filter"][1]["terms"]["source_id"]
            return {
                "hits": {
                    "hits": [
                        {"_source": {"source_id": source_id, "content": f"text {source_id}"}}
                        for source_id in source_ids
                    ]
                }
            }

    class FakeReranker:
        async def rerank(self, query, documents, top_n=None):
            assert query == "query"
            assert documents == ["text a", "text b", "text c"]
            assert top_n is None
            return [(2, 0.9), (0, 0.8), (1, 0.7)]

    monkeypatch.setattr(clients, "get_es", lambda: FakeEs())
    final, trace = asyncio.run(
        clients.rerank_sources_rrf(
            FakeReranker(),
            "user",
            "query",
            ["a", "b", "c"],
            {"vector": ["a", "b", "c"], "bm25": ["b", "a", "c"]},
            2,
        )
    )

    assert final == ["c", "a"]
    assert trace["reranker"][0] == {"source_id": "c", "score": 0.9}
    assert trace["final"] == final
