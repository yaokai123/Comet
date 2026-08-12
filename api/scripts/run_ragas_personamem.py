"""使用本地 BGE-M3、PersonaMem GPT 和 Comet 默认聊天模型运行 RAGAS。"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

# 必须在 app.core.llm.client 首次导入前设置；该模块会在导入时固化批量参数。
os.environ["EMBED_BATCH_SIZE"] = "10"
os.environ["EMBED_CONCURRENCY"] = "1"

from dotenv import dotenv_values, load_dotenv
from sqlalchemy import select

# Docker Compose 使用仓库根目录 .env。必须在导入 app.settings 前加载同一份
# FERNET_KEY，否则主机 api/.env 会导致数据库密文无法解密。
load_dotenv(API_ROOT.parent / ".env", override=True)

from app.core.security import decrypt_secret
from app.db.postgres import SessionLocal
from app.models.model_config_model import ModelConfig


def _required(values: dict, name: str) -> str:
    value = str(values.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"源环境缺少 {name}")
    return value


async def _load_default_judge() -> dict:
    async with SessionLocal() as session:
        result = await session.execute(select(ModelConfig).where(
            ModelConfig.type == "chat", ModelConfig.is_default.is_(True),
        ))
        configs = result.scalars().all()
        if len(configs) != 1:
            raise RuntimeError(f"需要且只能有一个默认聊天模型，当前找到 {len(configs)} 个")
        config = configs[0]
        if (config.wire_api or "chat_completions") != "chat_completions":
            raise RuntimeError("默认聊天模型必须支持 chat/completions，才能作为 RAGAS 裁判")
        headers = {}
        if config.extra_headers_encrypted:
            headers = json.loads(decrypt_secret(config.extra_headers_encrypted))
        return {
            "base_url": config.base_url,
            "model": config.model_name,
            "key": decrypt_secret(config.api_key_encrypted),
            "headers": headers,
        }


def _set(name: str, value: str) -> None:
    os.environ[name] = value


def configure(source: Path, judge: dict, embedding_base_url: str | None = None) -> None:
    values = dict(dotenv_values(source))
    embed_base = (
        embedding_base_url or _required(values, "XIAOBA_MEMORY_EMBEDDING_BASE_URL")
    ).rstrip("/")
    embed_model = _required(values, "XIAOBA_MEMORY_EMBEDDING_MODEL")
    embed_key = _required(values, "XIAOBA_MEMORY_EMBEDDING_API_KEY")
    _set("EVAL_EMBED_BASE_URL", embed_base)
    _set("EVAL_EMBED_MODEL", embed_model)
    _set("EVAL_EMBED_KEY", embed_key)
    _set("RAGAS_EMBED_BASE_URL", embed_base)
    _set("RAGAS_EMBED_MODEL", embed_model)
    _set("RAGAS_EMBED_KEY", embed_key)

    mapping = {
        "EVAL_CHAT_BASE_URL": "XIAOBA_LLM_API_BASE",
        "EVAL_CHAT_MODEL": "XIAOBA_LLM_MODEL",
        "EVAL_CHAT_KEY": "XIAOBA_LLM_API_KEY",
        "EVAL_CHAT_WIRE_API": "XIAOBA_LLM_WIRE_API",
        "EVAL_CHAT_REASONING_EFFORT": "XIAOBA_LLM_REASONING_EFFORT",
        "EVAL_CHAT_HTTP_HEADERS_JSON": "XIAOBA_LLM_HTTP_HEADERS_JSON",
        "EVAL_CHAT_DISABLE_RESPONSE_STORAGE": "XIAOBA_LLM_DISABLE_RESPONSE_STORAGE",
    }
    for target, source_name in mapping.items():
        value = str(values.get(source_name) or "").strip()
        if value:
            _set(target, value)
    for required in ("EVAL_CHAT_BASE_URL", "EVAL_CHAT_MODEL", "EVAL_CHAT_KEY"):
        if not os.getenv(required):
            raise RuntimeError(f"源环境无法生成 {required}")

    _set("RAGAS_JUDGE_BASE_URL", judge["base_url"])
    _set("RAGAS_JUDGE_MODEL", judge["model"])
    _set("RAGAS_JUDGE_KEY", judge["key"])
    _set("RAGAS_JUDGE_WIRE_API", "chat_completions")
    if judge["headers"]:
        _set("RAGAS_JUDGE_HTTP_HEADERS_JSON", json.dumps(judge["headers"]))
    _set("HF_HOME", str(API_ROOT / "eval" / "cache" / "huggingface"))
    _set("HF_HUB_DISABLE_TELEMETRY", "1")
    _set("RAGAS_DO_NOT_TRACK", "true")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--profile", choices=["smoke", "standard", "rigorous"],
                        default="standard")
    parser.add_argument("--sample", type=int)
    parser.add_argument("--corpus-limit", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--keep-corpus", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--embedding-base-url")
    args = parser.parse_args()
    configure(
        args.source,
        asyncio.run(_load_default_judge()),
        embedding_base_url=args.embedding_base_url,
    )
    command = ["eval.run_eval", "--benchmark", "ragas", "--ragas-profile", args.profile,
               "--top-k", str(args.top_k)]
    if args.sample is not None:
        command += ["--sample", str(args.sample)]
    if args.corpus_limit is not None:
        command += ["--ragas-corpus-limit", str(args.corpus_limit)]
    if args.keep_corpus:
        command.append("--keep-corpus")
    if args.skip_check:
        command.append("--skip-check")
    sys.argv = command
    from eval.run_eval import main as run_eval
    run_eval()


if __name__ == "__main__":
    main()
