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

## 浏览器恢复与图片上下文

浏览器 GET SSE 使用带抖动的指数退避自动重连，`online` 和页面重新可见事件会立即重建连接；初次 POST
流断开后会自动切换到同一会话的 GET 续传，不会重复提交用户消息。

对话图片只允许 JPEG、PNG、WEBP，单张不超过 10MB、单轮最多 6 张。后端同时校验真实图片内容、扩展名、
对象是否存在以及 file key 的用户归属。视觉路由会扫描最近 20 轮历史；历史图片按原消息聚合回放，优先保留
最近图片，并受 8 张、压缩后 12MB 的总预算约束。

图片检索证据包含统一 `citation_index`、鉴权原图 URL 和鉴权缩略图 URL，回答页按同一编号展示缩略图引用。

## 多实例故障测试

以下脚本会启动隔离的 PostgreSQL、Redis、两个 API 探针实例和 Nginx，验证刷新、客户端断连重连、负载均衡
以及停止一个实例后的游标续传，结束后自动删除测试卷：

```powershell
powershell -ExecutionPolicy Bypass -File D:\Comet-main\scripts\test-sse-ha.ps1
```

## Phase 4：请求幂等、视觉成本与生产验收

- 每次发送携带 `client_request_id`；`stream_runs(user_id, client_request_id)` 唯一，网络重试只回放原 Run，不重复写消息或调用模型。
- 首次 POST 在会话 `meta` 前断开时，通过 `/api/chat/runs/by-request/{id}/events` 找回原生成。
- 已完成 Run 一小时后压缩 token/tool/trace 事件，最终回答从 `messages` 快照恢复；Run 仍按 24 小时保留。
- 图片执行真实格式、单帧、最大边、最大像素、日数量/字节配额校验，并重新编码移除 EXIF；未附加图片 24 小时后清理。
- 图片引用使用稳定的 `/api/images/{id}/content` 和 `/thumbnail` 鉴权接口，缩略图预生成并缓存到对象存储。
- 历史图片仅在问题明确指代图片、截图、图表或表格时重新发送视觉模型；普通追问不重复支付多模态成本。
- `/api/health/runtime-metrics` 提供续传、转发失败、DB 轮询、重复请求、视觉路由、图片拒绝及三级检索质量信号。
- HA 脚本同时运行协议探针和真实 `ChatService + Playwright` 浏览器离线恢复测试，断言消息与终态恰好一次。

企业 PDF 回归评分：

```powershell
cd D:\Comet-main\api
.\.venv\Scripts\python.exe -m eval.benchmarks.enterprise_rag.runner `
  --predictions eval/results/enterprise_rag/predictions.jsonl `
  --output eval/results/enterprise_rag/scorecard.json
```

预测文件每行包含 `query_id`、`retrieved_source_ids`、`cited_source_ids`。报告按正文、表格、图片和跨页表格分别输出 Recall@5、MRR@5、nDCG@5 与引用 Precision/Recall。
