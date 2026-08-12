# DuIE 2.0 接入

该适配器只读取本地 DuIE 2.0 JSON/JSONL，不会自动下载数据。转换结果兼容
`eval.tasks.extraction` 的 `dialogue`、`gold_entities`、`gold_triples` 格式，并保留
原始实体类型、对象角色和源行号用于审计。

```powershell
cd api
.\.venv\Scripts\python.exe -m eval.benchmarks.duie2.loader `
  D:\datasets\duie2\duie_train.json `
  eval\fixtures\gold\duie2-extraction-500.json `
  --split train --limit 500 --seed 42
```

DuIE 的 `object.@value` 使用原谓词；n-ary 标注的其他对象角色转换为
`谓词::角色`，避免丢失标注。采样按每条记录排序后的首个谓词做比例分层，固定
seed 可得到完全一致的样本和顺序。

正式报告应使用 `score_exact_micro` 计算严格集合相等口径的实体/三元组 micro
Precision、Recall、F1，并保留其 TP/FP/FN。该口径不会把“北京”与“北京市”
视为相同实体，也不会使用 LLM 裁判。训练集抽样只适合链路验证；用于对外报告时应使用
固定且未参与 prompt 调优的验证/测试切分。
