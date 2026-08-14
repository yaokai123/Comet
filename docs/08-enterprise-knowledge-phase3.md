# 企业知识库第三阶段：可信复合检索与无损流式恢复

## 复合检索

Agent 只看到一个 `enterprise_federated_search` 工具。工具内部并发访问企业知识库、飞书文档、
飞书消息和妙记，避免让模型把多个独立搜索结果自行拼接。每条证据统一为带来源类型、来源名称、
权威度、版本与 Chunk 标识的证据对象。

默认权威顺序为：飞书正式文档（0.98）> 企业知识库（0.88）> 妙记（0.82）> 群消息（0.65）。
冲突裁决先比较来源权威度，再比较问题相关性和质量分；回答必须显式展示冲突、采用来源和未采用来源，
不能静默合并。连接失败的来源也会明确列出，避免把“未搜到”误解为“不存在”。

## 三级质量 Pipeline

1. `FastPass`：确定性校验空结果、错误文本、重复证据和 Prompt Injection；只有单一高权威且高度直接
   命中的结果可以走零模型调用快通道。
2. `Reranker`：使用已配置的本地 `bge-reranker-v2-m3` 粗排，并执行阈值和候选预算裁剪。
3. `LLM item review`：对候选逐条判断相关性、证据支持度、注入风险并抽取结构化 claim；最终分数综合
   精评分、Reranker 分和来源权威度。

每一级都会写入工具统计中的 `pipeline`，包括输入数、输出数、拒绝数和实际实现，便于在执行轨迹中观测。

## Durable SSE

单聊不再通过 Redis Pub/Sub 传输 token。PostgreSQL 的 `stream_runs` 与 `stream_events` 是唯一事件真相源，
每个事件有全局递增 ID。浏览器保存会话的最后事件 ID，重连时通过 `Last-Event-ID` 请求：服务端先重建游标前
的回答快照，再仅发送游标后的增量，因此刷新或换网络不会缺字或重字。

同实例使用内存队列唤醒；跨实例只在 Redis 哈希中登记 `run_id -> instance URL` 路由，并通过带共享密钥的
内部 HTTP 接口直接通知目标实例。通知只携带唤醒信息，目标实例仍按 ID 回读 PostgreSQL；直连失败时每
0.75 秒轮询补齐，所以通知失败不会丢事件。Redis 不保存 token、累计回答或 SSE 广播频道。

部署前执行：

```powershell
cd D:\Comet-main\api
.\.venv\Scripts\alembic.exe upgrade head
```

多 API 实例部署时，每个实例配置唯一 `STREAM_INSTANCE_ID`、可被其他实例访问的
`STREAM_INTERNAL_URL`，并共享同一个高强度 `STREAM_FORWARD_SECRET`。内部通知路由不应暴露到公网。
