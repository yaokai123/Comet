"""存储客户端访问 + ES 检索变体（文档粒度，返回排序后的 source_id）。

检索变体逻辑参照 app/core/rag/search.py，便于做「纯向量/纯BM25/混合/+rerank」四配置对比；
embed_client / rerank_client 由调用方注入（来自 eval_config，不读 app 用户配置）。
"""
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNKS_INDEX
from app.db.elastic import close as _es_close
from app.db.elastic import get_es
from app.db.neo4j import close as _neo_close


def _base_filter(uid: str) -> list[dict]:
    return [{"term": {"user_id": uid}}, {"term": {"chunk_type": CHUNK_TYPE_CHILD}}]


def _dedup_sources(hits: list[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        sid = h["_source"].get("source_id")
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def weighted_rrf(
    rankings: list[tuple[list[str], float]], *, rank_constant: int = 60
) -> list[tuple[str, float]]:
    """Fuse ranked ids with weighted reciprocal-rank fusion.

    Duplicate ids inside one ranking contribute only at their first position.
    Ties are resolved by first appearance so repeated runs remain deterministic.
    """
    if rank_constant < 0:
        raise ValueError("rank_constant must be non-negative")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    sequence = 0
    for ranked, weight in rankings:
        seen: set[str] = set()
        rank = 0
        for item_id in ranked:
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            rank += 1
            if item_id not in first_seen:
                first_seen[item_id] = sequence
                sequence += 1
            scores[item_id] = scores.get(item_id, 0.0) + weight / (rank_constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def rerank_view(content: str) -> str:
    """Keep timestamp and the scored center turn from an enriched retrieval window."""
    lines = content.splitlines()
    session = next((line for line in lines if line.startswith("Session time: ")), None)
    current_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("Current turn (retrieval target): ")
        ),
        None,
    )
    if current_index is None:
        return content
    selected = [line for line in (session, lines[current_index]) if line]
    for line in lines[current_index + 1:]:
        if line.startswith(("Previous turn ", "Current turn ", "Next turn ")):
            break
        selected.append(line)
    return "\n".join(selected)


async def retrieve_hybrid_contexts(
    embed_client, uid: str, query: str, top_k: int = 5,
    wv: float = 0.6, wb: float = 0.4, rerank_client=None,
) -> list[dict]:
    """复刻生产 hybrid_search，并返回 RAGAS 所需的最终父块上下文。"""
    recall = 20
    es = get_es()
    qv = await embed_client.embed_one(query)
    filters = _base_filter(uid)
    knn = await es.search(index=CHUNKS_INDEX, body={
        "size": recall, "query": {"bool": {"filter": filters}},
        "knn": {"field": "vector", "query_vector": qv, "k": recall,
                "num_candidates": recall * 5,
                "filter": {"bool": {"filter": filters}}},
    })
    bm = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "query": {"bool": {"must": [{"match": {"content": query}}],
                           "filter": filters}},
    })
    vs = {h["_id"]: h["_score"] for h in knn["hits"]["hits"]}
    bs = {h["_id"]: h["_score"] for h in bm["hits"]["hits"]}
    hits = {h["_id"]: h for h in knn["hits"]["hits"] + bm["hits"]["hits"]}
    vn, bn = _normalize(vs), _normalize(bs)
    fused = {cid: wv * vn.get(cid, 0.0) + wb * bn.get(cid, 0.0) for cid in hits}
    candidate_ids = sorted(fused, key=fused.get, reverse=True)[:max(top_k, recall)]
    if rerank_client and candidate_ids:
        pairs = await rerank_client.rerank(
            query, [hits[cid]["_source"].get("content", "") for cid in candidate_ids],
            top_n=top_k,
        )
        candidate_ids = [candidate_ids[index] for index, _ in pairs
                         if 0 <= index < len(candidate_ids)]
    results: list[dict] = []
    for cid in candidate_ids[:top_k]:
        source = hits[cid]["_source"]
        content = source.get("content", "")
        parent_id = source.get("parent_id")
        if parent_id:
            parent = await es.search(index=CHUNKS_INDEX, body={
                "size": 1,
                "query": {"bool": {"filter": [
                    {"term": {"user_id": uid}}, {"term": {"chunk_id": parent_id}},
                ]}},
            })
            parent_hits = parent["hits"]["hits"]
            if parent_hits:
                content = parent_hits[0]["_source"].get("content", content)
        results.append({
            "chunk_id": cid,
            "source_id": source.get("source_id"),
            "content": content,
            "score": round(fused.get(cid, 0.0), 8),
        })
    return results


async def retrieve_vector(embed_client, uid: str, query: str, recall: int = 20) -> list[str]:
    es = get_es()
    qv = await embed_client.embed_one(query)
    resp = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "query": {"bool": {"filter": _base_filter(uid)}},
        "knn": {"field": "vector", "query_vector": qv, "k": recall,
                "num_candidates": recall * 5, "filter": {"bool": {"filter": _base_filter(uid)}}},
    })
    return _dedup_sources(resp["hits"]["hits"])


async def retrieve_bm25(uid: str, query: str, recall: int = 20) -> list[str]:
    es = get_es()
    resp = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "query": {"bool": {"must": [{"match": {"content": query}}], "filter": _base_filter(uid)}},
    })
    return _dedup_sources(resp["hits"]["hits"])


async def retrieve_hybrid(embed_client, uid: str, query: str, recall: int = 20,
                          wv: float = 0.6, wb: float = 0.4) -> list[str]:
    rankings = await retrieve_hybrid_rankings(embed_client, uid, query, recall, wv, wb)
    return rankings["hybrid"]


async def retrieve_hybrid_rankings(
    embed_client,
    uid: str,
    query: str,
    recall: int = 20,
    wv: float = 0.6,
    wb: float = 0.4,
) -> dict[str, list[str]]:
    """Return vector, BM25, and score-fused source rankings from one retrieval pass."""
    es = get_es()
    qv = await embed_client.embed_one(query)
    knn = await es.search(index=CHUNKS_INDEX, body={
        "size": recall, "query": {"bool": {"filter": _base_filter(uid)}},
        "knn": {"field": "vector", "query_vector": qv, "k": recall, "num_candidates": recall * 5,
                "filter": {"bool": {"filter": _base_filter(uid)}}}})
    bm = await es.search(index=CHUNKS_INDEX, body={
        "size": recall, "query": {"bool": {"must": [{"match": {"content": query}}],
                                           "filter": _base_filter(uid)}}})
    chunk_src: dict[str, str] = {}
    vs: dict[str, float] = {}
    bs: dict[str, float] = {}
    for h in knn["hits"]["hits"]:
        vs[h["_id"]] = h["_score"]
        chunk_src[h["_id"]] = h["_source"].get("source_id")
    for h in bm["hits"]["hits"]:
        bs[h["_id"]] = h["_score"]
        chunk_src[h["_id"]] = h["_source"].get("source_id")
    vn, bn = _normalize(vs), _normalize(bs)
    fused = {cid: wv * vn.get(cid, 0.0) + wb * bn.get(cid, 0.0) for cid in chunk_src}
    vector = _dedup_sources(knn["hits"]["hits"])
    bm25 = _dedup_sources(bm["hits"]["hits"])
    seen: set[str] = set()
    hybrid: list[str] = []
    for cid in sorted(fused, key=fused.get, reverse=True):
        sid = chunk_src.get(cid)
        if sid and sid not in seen:
            seen.add(sid)
            hybrid.append(sid)
    return {"vector": vector, "bm25": bm25, "hybrid": hybrid}


async def rerank_sources_rrf(
    rerank_client,
    uid: str,
    query: str,
    source_ids: list[str],
    first_stage: dict[str, list[str]],
    top_k: int,
    *,
    rank_constant: int = 10,
    vector_weight: float = 1.0,
    bm25_weight: float = 0.7,
    rerank_weight: float = 6.0,
) -> tuple[list[str], dict]:
    """Rerank candidates and fuse vector/BM25/reranker ranks with weighted RRF."""
    if not source_ids:
        return [], {
            "vector": [], "bm25": [], "candidates": [],
            "reranker": [], "rrf": [], "final": [],
        }
    es = get_es()
    resp = await es.search(index=CHUNKS_INDEX, body={
        "size": len(source_ids),
        "query": {"bool": {"filter": [
            {"term": {"user_id": uid}},
            {"terms": {"source_id": source_ids}},
        ]}},
    })
    content_by_source = {
        hit["_source"].get("source_id"): hit["_source"].get("content", "")
        for hit in resp["hits"]["hits"]
    }
    contents = [rerank_view(content_by_source.get(source_id, "")) for source_id in source_ids]
    pairs = await rerank_client.rerank(query, contents, top_n=None)
    reranker_rows = [
        {"source_id": source_ids[index], "score": round(score, 8)}
        for index, score in pairs
        if 0 <= index < len(source_ids)
    ]
    candidate_set = set(source_ids)
    vector = [item for item in first_stage.get("vector", []) if item in candidate_set]
    bm25 = [item for item in first_stage.get("bm25", []) if item in candidate_set]
    reranked = [row["source_id"] for row in reranker_rows]
    fused = weighted_rrf(
        [
            (vector, vector_weight),
            (bm25, bm25_weight),
            (reranked, rerank_weight),
        ],
        rank_constant=rank_constant,
    )
    final = [source_id for source_id, _ in fused[:top_k]]
    trace = {
        "vector": vector,
        "bm25": bm25,
        "candidates": source_ids,
        "reranker": reranker_rows,
        "rrf": [
            {"source_id": source_id, "score": round(score, 10)}
            for source_id, score in fused
        ],
        "final": final,
    }
    return final, trace


async def rerank_sources(rerank_client, uid: str, query: str,
                         source_ids: list[str], top_k: int) -> list[str]:
    """对候选 source 取代表 chunk 内容做 cross-encoder rerank，返回重排后的 source_id。"""
    if not source_ids:
        return []
    es = get_es()
    contents: list[str] = []
    for sid in source_ids:
        resp = await es.search(index=CHUNKS_INDEX, body={
            "size": 1,
            "query": {"bool": {"filter": [
                {"term": {"user_id": uid}}, {"term": {"source_id": sid}},
            ]}},
        })
        hits = resp["hits"]["hits"]
        contents.append(hits[0]["_source"].get("content", "") if hits else "")
    pairs = await rerank_client.rerank(query, contents, top_n=top_k)
    return [source_ids[idx] for idx, _ in pairs]


async def close_clients() -> None:
    for closer in (_es_close, _neo_close):
        try:
            await closer()
        except Exception:
            pass
