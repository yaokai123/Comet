# 企业知识库四基准评测

评测主线只保留四套与企业知识库直接相关的公开数据：

| Benchmark | 默认样本 | 主要能力 | 默认来源 |
|---|---:|---|---|
| FinanceBench | 150 | 财报 PDF、证据页、数值问答、引用 | `PatronusAI/financebench` |
| TAT-QA | 500 | 表格+正文联合检索和数值推理 | `next-tat/TAT-QA` dev |
| CRUD-RAG | 200 | 中文问答、摘要、幻觉识别 | `IAAR-Shanghai/CRUD_RAG` |
| ViDoRe V3 | 200 | 图片、图表、页级检索、BBox | `vidore/vidore_v3_finance_en` |

旧的不适配评测资产已移除，避免项目指标与企业知识库目标混杂。

## 数据准备

在 `api/` 目录运行：

```bash
uv run python -m eval.benchmarks.fetch --with-financebench-pdfs
```

命令下载前三套标注文件，并按 FinanceBench 的 `doc_link` 下载每份唯一源 PDF 到 `eval/data/financebench/pdfs/`；已有非空 PDF 会跳过。若只需调试 loader，可去掉 `--with-financebench-pdfs`。该目录被 Git 忽略。ViDoRe 通过 Hugging Face `datasets` 按需获取；默认 finance-en 数据约 1.29 GB，首次运行前请确认磁盘和网络条件。

第三方数据不提交仓库。使用或发布结果前必须复核对应数据卡及原始文档许可证，尤其是 FinanceBench 的 CC BY-NC 4.0 和 ViDoRe 原始 PDF 的上游许可。

## 生成统一评测包

```bash
uv run python -m eval.run_eval --benchmark financebench
uv run python -m eval.run_eval --benchmark tatqa --sample 500
uv run python -m eval.run_eval --benchmark crud-rag --sample 200
uv run python -m eval.run_eval --benchmark vidore --sample 200
```

输出到 `eval/results/prepared/<benchmark>/`：

- `cases.jsonl`：问题、答案、Gold source、BBox 和场景标签。
- `corpus.jsonl`：统一后的文本/表格/页面语料。
- `manifest.json`：样本数、随机种子和数据来源。

FinanceBench 的 `corpus.jsonl` 保存人工 Gold 页文本用于计分和审计，`asset_path` 指向完整源 PDF；实际 RAG 必须导入去重后的完整 PDF，不能只把 Gold evidence page 当检索语料。

所有抽样固定 `seed=42`。可用 `--seed` 修改，但不同 seed 的结果不得直接作版本回归比较。

## 预测协议与评分

系统完成语料导入和查询后，每题输出一行：

```json
{"query_id":"q-1","answer":"答案","retrieved_source_ids":["page-3"],"cited_source_ids":["page-3"],"predicted_bboxes":{"page-3":[[0,0,100,80]]}}
```

评分：

```bash
uv run python -m eval.run_eval \
  --benchmark financebench \
  --predictions eval/results/predictions/financebench.jsonl \
  --score-output eval/results/financebench-score.json
```

统一指标包括 Recall@k、MRR@k、nDCG@k、Citation Precision/Recall、Answer Exact Match、Answer Token F1 和 BBox IoU。没有 BBox Gold 的数据集不会被记为零，而是在该指标中输出 `null`。

## ViDoRe 数据集切换

默认固定 Finance EN 官方端到端评测 revision。切换工业文档时应显式清空默认 revision：

```bash
uv run python -m eval.run_eval \
  --benchmark vidore \
  --vidore-dataset vidore/vidore_v3_industrial \
  --vidore-revision "" \
  --sample 200
```

## 代码结构

```text
eval/benchmarks/
├── schema.py              统一 case/corpus 契约
├── scoring.py             统一确定性计分器
├── suite.py               四基准 CLI
├── fetch.py               官方标注下载
├── financebench/loader.py
├── tatqa/loader.py
├── crud_rag/loader.py
└── vidore/loader.py
```

## FinanceBench 完整 PDF 基线

在 PostgreSQL、Elasticsearch 和已配置 Chat 模型可用时，可运行隔离的完整 PDF
BM25 基线。该执行器使用生产父子分块、页码对齐和显式来源引用，不会写入现有知识库：

```bash
uv run python -m eval.benchmarks.financebench.runner --rebuild
```

结果写入 `eval/results/predictions/financebench-bm25.jsonl` 和
`eval/results/financebench-bm25-score.json`。若模型返回空内容，可只续跑空答案：

```bash
uv run python -m eval.benchmarks.financebench.runner \
  --resume-from eval/results/predictions/financebench-bm25.jsonl \
  --max-tokens 2048
```

该命令是 BM25 基线，不应标记为向量或混合检索结果。运行向量版前应单独记录
Embedding 模型、完整索引耗时和检索策略。
