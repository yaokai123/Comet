"""CMRC 2018 到 Comet RAGAS case/corpus 的确定性转换。"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Iterable

DATASET_ID = "hfl/cmrc2018"
DATASET_SPLIT = "validation"
DATASET_REVISION = "137f2c45a24275fb68f6961c4d357f46288886aa"


def source_id(context: str) -> str:
    return "cmrc-" + hashlib.sha256(context.encode("utf-8")).hexdigest()[:20]


def _bucket(row: dict) -> str:
    answer = next((x for x in row["answers"]["text"] if x.strip()), "")
    answer_band = "short" if len(answer) <= 8 else "medium" if len(answer) <= 24 else "long"
    context_band = "short" if len(row["context"]) <= 450 else "long"
    return f"{answer_band}-{context_band}"


def _valid_rows(rows: Iterable[dict]) -> list[dict]:
    output = []
    for row in rows:
        context = (row.get("context") or "").strip()
        question = (row.get("question") or "").strip()
        answers = [x.strip() for x in (row.get("answers") or {}).get("text", []) if x.strip()]
        if context and question and answers and any(answer in context for answer in answers):
            output.append({**row, "context": context, "question": question,
                           "answers": {"text": answers}})
    return output


def _stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    if n >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_bucket(row), []).append(row)
    # Hamilton 最大余数法：允许极小样本下部分 strata 分配为 0，仍严格返回 n 条。
    exact = {key: n * len(group) / len(rows) for key, group in groups.items()}
    allocation = {key: int(value) for key, value in exact.items()}
    remaining = n - sum(allocation.values())
    ranked = sorted(
        groups,
        key=lambda key: (exact[key] - allocation[key], len(groups[key]), key),
        reverse=True,
    )
    for key in ranked[:remaining]:
        allocation[key] += 1
    sampled: list[dict] = []
    for key in sorted(groups):
        group = list(groups[key])
        rng.shuffle(group)
        sampled.extend(group[:allocation[key]])
    rng.shuffle(sampled)
    return sampled


def prepare_cmrc(
    validation_rows: Iterable[dict], train_rows: Iterable[dict], *,
    sample_size: int, corpus_limit: int, seed: int,
) -> tuple[list[dict], list[dict]]:
    """构造验证问题和封闭检索语料；所有相关文档必定包含在 corpus 中。"""
    if sample_size < 1 or corpus_limit < sample_size:
        raise ValueError("CMRC corpus_limit 必须不小于 sample_size，且二者均大于 0")
    validation = _valid_rows(validation_rows)
    if sample_size > len(validation):
        raise ValueError(f"请求 {sample_size} 题，但有效 validation 仅 {len(validation)} 题")
    selected = _stratified_sample(validation, sample_size, seed)
    cases: list[dict] = []
    relevant_contexts: dict[str, str] = {}
    for row in selected:
        sid = source_id(row["context"])
        relevant_contexts[sid] = row["context"]
        cases.append({
            "id": row["id"],
            "question": row["question"],
            "reference": row["answers"]["text"][0],
            "reference_variants": row["answers"]["text"],
            "relevant_doc_ids": [sid],
            "type": _bucket(row),
        })

    corpus = [{"source_id": sid, "text": text} for sid, text in relevant_contexts.items()]
    seen = set(relevant_contexts)
    rng = random.Random(seed + 1)
    distractors: list[dict] = []
    for row in list(validation) + _valid_rows(train_rows):
        sid = source_id(row["context"])
        if sid not in seen:
            seen.add(sid)
            distractors.append({"source_id": sid, "text": row["context"]})
    rng.shuffle(distractors)
    corpus.extend(distractors[:max(0, corpus_limit - len(corpus))])
    rng.shuffle(corpus)
    return cases, corpus


def load_cmrc(sample_size: int, corpus_limit: int, seed: int) -> tuple[list[dict], list[dict], dict]:
    from datasets import load_dataset

    # 一次加载 DatasetDict，避免同一仓库为 validation/train 重复做 Hub 解析与缓存锁检查。
    dataset = load_dataset(DATASET_ID, revision=DATASET_REVISION)
    validation = dataset[DATASET_SPLIT]
    train = dataset["train"]
    cases, corpus = prepare_cmrc(
        validation, train, sample_size=sample_size, corpus_limit=corpus_limit, seed=seed
    )
    canonical = json.dumps(
        {"cases": cases, "corpus_ids": [x["source_id"] for x in corpus]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    meta = {
        "dataset_id": DATASET_ID,
        "split": DATASET_SPLIT,
        "revision": DATASET_REVISION,
        "license": "CC BY-SA 4.0",
        "validation_fingerprint": getattr(validation, "_fingerprint", None),
        "train_fingerprint": getattr(train, "_fingerprint", None),
        "prepared_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    return cases, corpus, meta
