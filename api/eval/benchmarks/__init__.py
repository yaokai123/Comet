"""Enterprise evaluation suite: FinanceBench, TAT-QA, CRUD-RAG and ViDoRe."""
from pathlib import Path

# 公共缓存目录:HuggingFace datasets 下载 + 各 benchmark 中间产物
CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


BENCHMARKS = ("financebench", "tatqa", "crud-rag", "vidore")
