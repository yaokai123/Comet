# DeepResearch Bench 适配器

该目录只负责可复现的数据选择和结果导出，不会自动执行 50 个联网研究任务，也不会调用裁判模型。

官方数据源是 `Ayanami0730/deep_research_bench` 仓库中的
`data/prompt_data/query.jsonl`。正式运行时应固定上游 commit，保存原文件，并调用：

```python
from eval.benchmarks.deepresearch_bench import export_reports, load_tasks, write_manifest

tasks, manifest = load_tasks("query.jsonl")  # 严格要求 50 条 language=zh
write_manifest(manifest, "results/deepresearch/manifest.json")

# results 中每项必须显式关联 benchmark_task_id，避免把内部 UUID 错当题号。
export_reports(
    tasks,
    results,
    "results/deepresearch/raw_data/comet.jsonl",
)
```

`results` 的最小输入结构：

```json
{
  "benchmark_task_id": 1,
  "report_id": "Comet report UUID",
  "topic": "与官方 prompt 完全相同",
  "report_md": "# 最终报告\n...",
  "sources": [{"index": 1, "type": "web", "title": "来源标题", "url": "https://example.com"}]
}
```

输出包括：

- `raw_data/comet.jsonl`：官方 RACE/FACT 接受的 `id/prompt/article` JSONL。
- `raw_data/comet.citation-audit.jsonl`：逐题引用审计，包含断链、缺少 URL、未使用来源以及 `fact_ready`。
- `manifest.json`：原始文件 SHA-256、筛选后任务 SHA-256、题号和固定顺序。

导出器不会修补或虚构 URL。`fact_ready=false` 的报告仍可进行 RACE 评测，但在运行 FACT 前应先检查引用问题。RACE/FACT 的官方 evaluator、模型版本和网页抓取依赖会变化，应在实际评测仓库中固定 commit 并单独记录。
