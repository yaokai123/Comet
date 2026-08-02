# 评测结果留档规范

真实运行结果默认不提交 Git，因为逐样本文件可能包含私有语料或模型输出。运行 RAGAS 后会生成：

```text
results/ragas/<UTC-run-id>/
├── manifest.json   # 数据集哈希、Git SHA、RAGAS/模型版本、参数
├── samples.jsonl   # 每题上下文、答案、逐项分数和错误
├── summary.json    # 汇总指标
└── report.md       # 可读报告
```

`summary.json` 同时保存逐指标均值与 95% bootstrap 置信区间。对外声明指标前，应保留完整运行目录，
并确认 `metric_error_count` 为 0。若要公开结果，请先脱敏，
再显式解除对应目录的忽略规则；不要只提交汇总分数而省略 manifest 与逐样本明细。
