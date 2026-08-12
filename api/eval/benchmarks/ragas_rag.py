"""RAGAS 0.4.x 端到端 RAG 评测：检索、回答、模型裁判与审计产物。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import subprocess
import sys
import types
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import fmean
from typing import Any

from app.core.rag.chunker import chunk_parent_child
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNK_TYPE_PARENT, CHUNKS_INDEX, ensure_index
from app.core.rag.es_store import build_chunk_doc, bulk_index
from app.db.elastic import get_es
from eval import clients, eval_config, metrics as deterministic_metrics
from eval.benchmarks.ragas_cmrc import load_cmrc
from eval.eval_config import EVAL_USER_ID

DATASET_PATH = Path(__file__).parents[1] / "fixtures" / "gold" / "ragas.json"
RESULTS_ROOT = Path(__file__).parents[1] / "results" / "ragas"
METRIC_NAMES = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
    "factual_correctness",
)
CMRC_USER_ID = uuid.UUID("eee20000-0000-0000-0000-00000000c018")
PROFILES = {
    "smoke": {"sample": 12, "corpus": 10},
    "standard": {"sample": 200, "corpus": 1000},
    "rigorous": {"sample": 500, "corpus": 3000},
}


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("RAGAS 数据集必须是非空 JSON 数组")
    required = {"id", "question", "reference", "relevant_doc_ids", "type"}
    seen: set[str] = set()
    for index, item in enumerate(data):
        missing = required - item.keys()
        if missing:
            raise ValueError(f"第 {index + 1} 条缺少字段: {sorted(missing)}")
        if item["id"] in seen:
            raise ValueError(f"样本 id 重复: {item['id']}")
        seen.add(item["id"])
        if not all(isinstance(item[key], str) and item[key].strip()
                   for key in ("id", "question", "reference", "type")):
            raise ValueError(f"样本 {item['id']} 包含空文本字段")
        if not isinstance(item["relevant_doc_ids"], list) or not item["relevant_doc_ids"]:
            raise ValueError(f"样本 {item['id']} 必须包含 relevant_doc_ids")
    return data


def _score_value(result: Any) -> float:
    """兼容 RAGAS MetricResult、标量和单值 dict。"""
    value = getattr(result, "value", result)
    if isinstance(value, dict):
        if len(value) != 1:
            raise TypeError(f"无法确定 RAGAS 结果中的分数: {value}")
        value = next(iter(value.values()))
    return round(float(value), 6)


def _mean_scores(samples: list[dict]) -> dict[str, float | None]:
    summary: dict[str, float | None] = {}
    for name in METRIC_NAMES:
        values = [item["scores"][name] for item in samples
                  if item["scores"].get(name) is not None]
        summary[name] = round(fmean(values), 6) if values else None
    return summary


def _bootstrap_ci(samples: list[dict], section: str, names: tuple[str, ...] | list[str],
                  seed: int, rounds: int = 2000) -> dict[str, dict | None]:
    """对逐题分数做非参数 bootstrap，输出均值的 95% 置信区间。"""
    rng = random.Random(seed)
    output: dict[str, dict | None] = {}
    for name in names:
        values = [row[section].get(name) for row in samples if row[section].get(name) is not None]
        if not values:
            output[name] = None
            continue
        estimates = sorted(
            fmean(rng.choice(values) for _ in range(len(values))) for _ in range(rounds)
        )
        output[name] = {
            "low": round(estimates[int(rounds * 0.025)], 6),
            "high": round(estimates[min(rounds - 1, int(rounds * 0.975))], 6),
        }
    return output


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _dataset_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _clear_cmrc() -> None:
    es = get_es()
    try:
        await es.delete_by_query(
            index=CHUNKS_INDEX,
            body={"query": {"term": {"user_id": str(CMRC_USER_ID)}}},
            refresh=True, conflicts="proceed",
        )
    except Exception as exc:
        print(f"[RAGAS/CMRC] 清理 ES 失败（忽略）: {exc}")


async def _ingest_cmrc(embed_client, corpus: list[dict], batch_size: int = 40) -> int:
    """批量写入 CMRC 封闭语料，使用独立 user namespace，避免污染其他评测。"""
    await ensure_index()
    await _clear_cmrc()
    uid = str(CMRC_USER_ID)
    for start in range(0, len(corpus), batch_size):
        batch = corpus[start:start + batch_size]
        docs: list[dict] = []
        children: list[tuple[str, dict, str]] = []
        for item in batch:
            for parent in chunk_parent_child(item["text"]):
                parent_doc = build_chunk_doc(
                    user_id=uid, source_type="document", source_id=item["source_id"],
                    doc_name=item["source_id"], chunk_type=CHUNK_TYPE_PARENT,
                    content=parent.content, vector=None,
                )
                docs.append(parent_doc)
                for child in parent.children:
                    children.append((child, parent_doc, item["source_id"]))
        vectors = await embed_client.embed([row[0] for row in children])
        for (text, parent_doc, source_id), vector in zip(children, vectors):
            docs.append(build_chunk_doc(
                user_id=uid, source_type="document", source_id=source_id,
                doc_name=source_id, chunk_type=CHUNK_TYPE_CHILD, content=text,
                vector=vector, parent_id=parent_doc["_id"],
            ))
        await bulk_index(docs)
        done = min(start + batch_size, len(corpus))
        print(f"    [RAGAS/CMRC] corpus {done}/{len(corpus)}")
    return len(corpus)


def _install_ragas_043_compat() -> None:
    """绕过 RAGAS 0.4.3 对已移除 VertexAI 旧模块的无条件导入。

    langchain-community 0.4.x 删除了该模块，而 RAGAS 仍把 ChatVertexAI 仅用于
    ``isinstance`` 能力判断。Comet 只使用 OpenAI-compatible client，因此提供一个
    不可实例化的占位类型不会改变实际评分路径。上游修复后此函数自然不再注入。
    """
    module_name = "langchain_community.chat_models.vertexai"
    try:
        __import__(module_name)
        return
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
    shim = types.ModuleType(module_name)
    shim.ChatVertexAI = type("ChatVertexAI", (), {"__module__": module_name})
    sys.modules[module_name] = shim


def _make_ragas_components():
    # 延迟导入：数据校验与单元测试不要求网络，也不会初始化第三方客户端。
    _install_ragas_043_compat()
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        FactualCorrectness,
        Faithfulness,
    )

    judge = eval_config.ragas_model_config("JUDGE")
    embed = eval_config.ragas_model_config("EMBED")
    if judge["wire_api"] != "chat_completions":
        raise RuntimeError(
            "RAGAS 0.4.x 的 Instructor 裁判只支持 chat/completions；"
            "请为 RAGAS_JUDGE_* 配置独立的 Chat Completions 模型"
        )
    judge_llm = llm_factory(
        judge["model"], provider="openai",
        client=AsyncOpenAI(
            api_key=judge["api_key"], base_url=judge["base_url"],
            default_headers=judge["default_headers"] or None,
        ),
        temperature=0.0,
        max_tokens=4096,
    )
    judge_embeddings = OpenAIEmbeddings(
        model=embed["model"],
        client=AsyncOpenAI(
            api_key=embed["api_key"], base_url=embed["base_url"],
            default_headers=embed["default_headers"] or None,
        ),
    )
    return {
        "context_precision": ContextPrecision(llm=judge_llm),
        "context_recall": ContextRecall(llm=judge_llm),
        "faithfulness": Faithfulness(llm=judge_llm),
        "answer_relevancy": AnswerRelevancy(llm=judge_llm, embeddings=judge_embeddings),
        "factual_correctness": FactualCorrectness(llm=judge_llm, mode="f1"),
    }, judge, embed


async def _answer(chat_client, question: str, contexts: list[str]) -> str:
    evidence = "\n\n".join(f"[证据 {i}] {text}" for i, text in enumerate(contexts, 1))
    return (await chat_client.chat([
        {"role": "system", "content": (
            "你是严谨的知识库问答助手。只能依据给定证据回答；证据不足时明确说不知道。"
            "回答应简洁、完整，不得补充证据之外的事实。"
        )},
        {"role": "user", "content": f"问题：{question}\n\n{evidence}"},
    ], temperature=0.0, max_tokens=512)).strip()


async def _evaluate_sample(metric_set: dict, question: str, response: str,
                           reference: str, contexts: list[str]) -> tuple[dict, dict]:
    calls = {
        "context_precision": {"user_input": question, "reference": reference,
                              "retrieved_contexts": contexts},
        "context_recall": {"user_input": question, "reference": reference,
                           "retrieved_contexts": contexts},
        "faithfulness": {"user_input": question, "response": response,
                         "retrieved_contexts": contexts},
        "answer_relevancy": {"user_input": question, "response": response},
        "factual_correctness": {"response": response, "reference": reference},
    }
    scores: dict[str, float | None] = {}
    errors: dict[str, str] = {}

    async def run_metric(name: str, kwargs: dict) -> tuple[str, float | None, str | None]:
        try:
            return name, _score_value(await metric_set[name].ascore(**kwargs)), None
        except Exception as exc:  # 单项失败仍需保存其他指标及可排查错误
            return name, None, f"{type(exc).__name__}: {exc}"

    results = await asyncio.gather(*(
        run_metric(name, kwargs) for name, kwargs in calls.items()
    ))
    for name, score, error in results:
        scores[name] = score
        if error:
            errors[name] = error
    return scores, errors


def _write_artifacts(samples: list[dict], manifest: dict, summary: dict) -> Path:
    run_dir = RESULTS_ROOT / manifest["run_id"]
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "samples.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in samples),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = [
        "# Comet RAGAS 评测报告", "",
        f"- 运行 ID：`{manifest['run_id']}`",
        f"- 数据集：`{manifest['dataset']['dataset_id']}` / `{manifest['dataset']['split']}`",
        f"- Profile：`{manifest['profile']}`",
        f"- 样本数：{manifest['sample_count']}",
        f"- Corpus：{manifest['corpus_count']} 篇",
        f"- 数据集 SHA-256：`{manifest['dataset_sha256']}`",
        f"- 代码提交：`{manifest['git_sha'] or 'unknown'}`", "",
        "| 指标 | 均值 | 95% CI |", "|---|---:|---:|",
    ]
    for name, score in summary["ragas"].items():
        ci = summary["confidence_intervals_95"]["ragas"].get(name)
        ci_text = "N/A" if ci is None else f"[{ci['low']:.4f}, {ci['high']:.4f}]"
        rows.append(f"| {name} | {'N/A' if score is None else f'{score:.4f}'} | {ci_text} |")
    rows += ["", "| 确定性检索指标 | 均值 | 95% CI |", "|---|---:|---:|"]
    for name, score in summary["retrieval"].items():
        ci = summary["confidence_intervals_95"]["retrieval"].get(name)
        ci_text = "N/A" if ci is None else f"[{ci['low']:.4f}, {ci['high']:.4f}]"
        rows.append(f"| {name} | {score:.4f} | {ci_text} |")
    rows += ["", f"失败的指标调用数：{summary['metric_error_count']}"
             f"（{summary['metric_error_rate']:.2%}）。",
             "逐样本证据、答案、分数及错误见 `samples.jsonl`。", ""]
    (run_dir / "report.md").write_text("\n".join(rows), encoding="utf-8")
    return run_dir


async def run_benchmark(
    embed_client, chat_client, rerank_client=None, *, profile: str = "standard",
    sample: int | None = None, corpus_limit: int | None = None, top_k: int = 5,
    seed: int = 42, skip_ingest: bool = False, keep_corpus: bool = False,
) -> Path:
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    if top_k < 1:
        raise ValueError("--top-k 必须大于 0")
    if profile not in PROFILES:
        raise ValueError(f"未知 RAGAS profile: {profile}")
    profile_config = PROFILES[profile]
    requested_sample = sample if sample is not None else profile_config["sample"]
    requested_corpus = corpus_limit if corpus_limit is not None else profile_config["corpus"]
    if profile == "smoke":
        dataset = load_dataset()[:requested_sample]
        uid = str(EVAL_USER_ID)
        dataset_meta = {
            "dataset_id": "comet/fixtures/ragas",
            "split": "local",
            "license": "repository license",
            "prepared_sha256": _dataset_sha(DATASET_PATH),
        }
        corpus_count = 10
    else:
        print(f"[RAGAS/CMRC] 加载数据：sample={requested_sample}, corpus={requested_corpus}…")
        dataset, corpus, dataset_meta = load_cmrc(
            sample_size=requested_sample, corpus_limit=requested_corpus, seed=seed
        )
        uid = str(CMRC_USER_ID)
        corpus_count = len(corpus)
        if not skip_ingest:
            await _ingest_cmrc(embed_client, corpus)
    metric_set, judge, judge_embed = _make_ragas_components()
    outputs: list[dict] = []
    for index, item in enumerate(dataset, 1):
        print(f"    [RAGAS] {index}/{len(dataset)} {item['question'][:32]}…")
        selected = await clients.retrieve_hybrid_contexts(
            embed_client, uid, item["question"], top_k=top_k,
            rerank_client=rerank_client,
        )
        contexts = [row["content"] for row in selected]
        response = await _answer(chat_client, item["question"], contexts)
        scores, errors = await _evaluate_sample(
            metric_set, item["question"], response, item["reference"], contexts
        )
        # RAGAS 按真实 chunk 上下文评分；确定性文档指标则按首次出现顺序去重 source。
        ranked = list(dict.fromkeys(row["source_id"] for row in selected if row["source_id"]))
        outputs.append({
            **item,
            "retrieved": selected,
            "response": response,
            "scores": scores,
            "errors": errors,
            "retrieval_scores": {
                f"recall@{top_k}": deterministic_metrics.recall_at_k(
                    ranked, item["relevant_doc_ids"], top_k
                ),
                f"precision@{top_k}": deterministic_metrics.precision_at_k(
                    ranked, item["relevant_doc_ids"], top_k
                ),
                f"ndcg@{top_k}": deterministic_metrics.ndcg_at_k(
                    ranked, item["relevant_doc_ids"], top_k
                ),
                "mrr": deterministic_metrics.mrr(ranked, item["relevant_doc_ids"]),
            },
        })
    retrieval_names = list(outputs[0]["retrieval_scores"])
    error_count = sum(len(x["errors"]) for x in outputs)
    summary = {
        "ragas": _mean_scores(outputs),
        "retrieval": {name: round(fmean(x["retrieval_scores"][name] for x in outputs), 6)
                      for name in retrieval_names},
        "metric_error_count": error_count,
        "metric_error_rate": round(error_count / (len(outputs) * len(METRIC_NAMES)), 6),
    }
    summary["confidence_intervals_95"] = {
        "ragas": _bootstrap_ci(outputs, "scores", list(METRIC_NAMES), seed),
        "retrieval": _bootstrap_ci(outputs, "retrieval_scores", retrieval_names, seed + 1),
    }
    now = datetime.now(timezone.utc)
    manifest = {
        "schema_version": 2,
        "run_id": now.strftime("%Y%m%dT%H%M%S%fZ"),
        "created_at": now.isoformat(),
        "sample_count": len(outputs),
        "corpus_count": corpus_count,
        "top_k": top_k,
        "seed": seed,
        "profile": profile,
        "dataset": dataset_meta,
        "dataset_sha256": dataset_meta["prepared_sha256"],
        "git_sha": _git_sha(),
        "ragas_version": version("ragas"),
        "generator_model": chat_client.model_name,
        "judge_model": judge["model"],
        "judge_embedding_model": judge_embed["model"],
        "rerank_model": rerank_client.model_name if rerank_client else None,
        "telemetry_disabled": os.environ.get("RAGAS_DO_NOT_TRACK") == "true",
    }
    run_dir = _write_artifacts(outputs, manifest, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"RAGAS 报告目录：{run_dir}")
    if profile != "smoke" and not keep_corpus:
        print("[RAGAS/CMRC] 清理 corpus…")
        await _clear_cmrc()
    return run_dir
