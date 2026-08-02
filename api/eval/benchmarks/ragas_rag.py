"""RAGAS 0.4.x 端到端 RAG 评测：检索、回答、模型裁判与审计产物。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import fmean
from typing import Any

from eval import clients, eval_config, metrics as deterministic_metrics
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


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _dataset_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    from openai import AsyncOpenAI, OpenAI
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
    judge_llm = llm_factory(
        judge["model"], provider="openai",
        client=AsyncOpenAI(api_key=judge["api_key"], base_url=judge["base_url"]),
        temperature=0.0,
    )
    judge_embeddings = OpenAIEmbeddings(
        model=embed["model"],
        client=OpenAI(api_key=embed["api_key"], base_url=embed["base_url"]),
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
    for name, kwargs in calls.items():
        try:
            scores[name] = _score_value(await metric_set[name].ascore(**kwargs))
        except Exception as exc:  # 单项失败仍需保存其他指标及可排查错误
            scores[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"
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
        f"- 样本数：{manifest['sample_count']}",
        f"- 数据集 SHA-256：`{manifest['dataset_sha256']}`",
        f"- 代码提交：`{manifest['git_sha'] or 'unknown'}`", "",
        "| 指标 | 均值 |", "|---|---:|",
    ]
    for name, score in summary["ragas"].items():
        rows.append(f"| {name} | {'N/A' if score is None else f'{score:.4f}'} |")
    rows += ["", "| 确定性检索指标 | 均值 |", "|---|---:|"]
    for name, score in summary["retrieval"].items():
        rows.append(f"| {name} | {score:.4f} |")
    rows += ["", f"失败的指标调用数：{summary['metric_error_count']}。",
             "逐样本证据、答案、分数及错误见 `samples.jsonl`。", ""]
    (run_dir / "report.md").write_text("\n".join(rows), encoding="utf-8")
    return run_dir


async def run_benchmark(embed_client, chat_client, rerank_client=None,
                        sample: int | None = None, top_k: int = 5) -> Path:
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    if top_k < 1:
        raise ValueError("--top-k 必须大于 0")
    dataset = load_dataset()
    if sample is not None:
        if sample < 1:
            raise ValueError("--sample 必须大于 0")
        dataset = dataset[:sample]
    metric_set, judge, judge_embed = _make_ragas_components()
    outputs: list[dict] = []
    uid = str(EVAL_USER_ID)
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
    summary = {
        "ragas": _mean_scores(outputs),
        "retrieval": {name: round(fmean(x["retrieval_scores"][name] for x in outputs), 6)
                      for name in retrieval_names},
        "metric_error_count": sum(len(x["errors"]) for x in outputs),
    }
    now = datetime.now(timezone.utc)
    manifest = {
        "schema_version": 1,
        "run_id": now.strftime("%Y%m%dT%H%M%S%fZ"),
        "created_at": now.isoformat(),
        "sample_count": len(outputs),
        "top_k": top_k,
        "dataset": str(DATASET_PATH.relative_to(Path(__file__).parents[2])),
        "dataset_sha256": _dataset_sha(DATASET_PATH),
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
    return run_dir
