# 记忆系统与 Neo4j 图谱深读

本文解释当前项目的“主动记住 / 对话自动抽取 -> 图谱写入 -> 主动召回”链路。它有两套存储，职责不能混淆：PostgreSQL 的 `memories` 是原始文本、任务状态和审计锚点；Neo4j 才保存实体、关系、事件、溯源和可检索向量。

## 1. 总链路：两种入口，一条抽取流水线

```text
手动入口：MemoryPage -> POST /api/memories/remember
对话入口：ChatService 在用户消息完成后创建 auto Memory
                         |
                         v
PostgreSQL memories: raw_text + source + status=pending
                         |
                         v
Celery memory 队列: extract_memory_task(memory_id)
                         |
                         v
chat 模型：切块、原子陈述、实体、三元组、事件
embedding 模型：实体名向量
                         |
                         v
批内去重 -> 与 Neo4j 已有实体融合 -> 单事务写 Neo4j
                         |
                         v
PostgreSQL memories: status=done + graph_dialogue_id + graph_stats
                         |
                         v
下一轮聊天：query embedding -> Neo4j 实体/Insight 召回 -> system prompt 注入
```

`Memory` 不是图谱实体本身，而是“一次抽取任务的来源和结果索引”。删除 `Memory` 记录不等于删除已经写入的 Neo4j 图节点；删除图中 Entity 的接口也不等于删除 PG 中的原始 Memory 记录。

## 2. 前端：查看、触发和可视化，不参与抽取逻辑

### 2.1 `MemoryPage.tsx`

文件：[MemoryPage.tsx](/D:/Comet-main/web/src/pages/MemoryPage.tsx)

页面有五个视图：用户画像、主题社区、事件时间线、记忆搜索、审查纠错。它们大部分读取 Neo4j 派生数据，而非 `memories` 表。

“记住”按钮调用 `memoryApi.remember(text)`。成功响应只意味着 PG 已创建 pending 任务，页面会每 4 秒刷新画像，最多 6 次；这是一个有限的 UI 轮询，不保证长任务最终一定完成。画像卡片显示 Entity 的 type、description、aliases、关系、importance、confidence、short/long-term 层级和画像字段。

前端把置信度划为高（>= 0.85）、中（>= 0.75）和待确认；这只是展示策略，真正的召回过滤阈值在后端 settings。用户可手动触发巩固、反思、重聚类、合并重复实体，以及确认/修正/删除低置信实体。

### 2.2 `GraphPage.tsx`

文件：[GraphPage.tsx](/D:/Comet-main/web/src/pages/GraphPage.tsx)

页面请求 `memoryApi.graph()` 获取全量节点和边，再在浏览器构建邻接表与度数。默认只显示 Entity 和 Event，Dialogue、Chunk、Statement 等溯源层可以按类型开启。首次选择名为“用户/我/本人/自己”的实体为焦点；若不存在则选择度数最高的节点，并展开一跳邻居。

这意味着图谱浏览是“服务端取全量，客户端按焦点渐进显示”，不是每点一个节点就向后端请求子图。节点大小受关系度数与 importance 影响，点击 Entity 会展开其邻居并显示详情。顶部“合并重复项”调用后端的历史重复清理，然后重新加载全图。

## 3. HTTP 与服务层：用户边界和任务状态

### 3.1 `memory_controller.py`

文件：[memory_controller.py](/D:/Comet-main/api/app/controllers/memory_controller.py)

所有 `/api/memories/*` 接口都注入 `get_current_user`，把 `user.id` 传给 `MemoryService`。接口可以分为四类：

1. 任务与检索：`remember`、`search`、列表/详情/删除 PG Memory。
2. 图谱读取：profile、communities、graph、entity subgraph、timeline、insights。
3. 图谱维护：recluster、merge-duplicates、consolidate、reflect、删除 Entity。
4. 人工纠错：低置信审查、confirm、correct、带 reason 的 delete。纠错会同时维护审计记录，具体逻辑在 `MemoryService` 的后半部分及 correction repository。

Controller 不直接执行 Cypher，也不把 Neo4j session 暴露给前端；它只是认证、参数约束、调用 service、包装统一响应。

### 3.2 `Memory` 模型和 `MemoryService.remember`

文件：[memory_model.py](/D:/Comet-main/api/app/models/memory_model.py)、[memory_service.py](/D:/Comet-main/api/app/services/memory_service.py)

`memories` 的关键字段：`raw_text` 是原文；`source` 为 `manual` 或 `auto`；`source_message_id` 将自动抽取关联回聊天消息；`status` 是 `pending/extracting/done/failed`；`graph_dialogue_id` 和 `graph_stats` 指向此次 Neo4j 写入的根节点与数量统计。

手动记忆先去掉空白并拒绝空文本，创建 `Memory(user_id, raw_text, source=manual, status=pending)`，提交 PG 后执行 `extract_memory_task.delay(str(memory.id))`。这里和知识库上传一样：HTTP 返回快，真正的模型调用和图谱写入属于后续 worker。

`MemoryService.search` 要求存在 embedding 配置，然后调用图谱 `search_memory`。`get_profile` 则调用 Neo4j 的 `list_all_entities` 与 `entity_type_counts`，在 Python 中按类型分组为前端数据。

### 3.3 对话自动抽取

文件：[chat_service.py](/D:/Comet-main/api/app/services/chat_service.py)

聊天服务在用户一轮消息处理完成后会调用 `_dispatch_memory(user_id, user_text)`，创建 `source=auto` 的 `Memory` 并投递同一个 `extract_memory_task`。它只把用户消息作为原文，不会直接把 AI 的回答写成用户记忆。手动与自动入口的差异仅在 source 和是否有 source_message_id；后面的抽取链完全一致。

## 4. Celery：状态机、硬依赖与结果回写

文件：[tasks/memory.py](/D:/Comet-main/api/app/tasks/memory.py)、[celery_app.py](/D:/Comet-main/api/app/celery_app.py)

`app.tasks.memory.*` 路由到 Redis 驱动的 `memory` 队列。同步 Celery 入口通过 `asyncio.run` 执行异步任务；每一项任务单独创建 SQLAlchemy engine，并在结束时关闭该任务 event loop 的 Neo4j driver。

任务首先按 memory ID 读取 PG 记录，设为 `extracting`。然后通过 `get_client_for_type` 取得当前用户的默认 chat 与 embedding client。这两个都是抽取任务的硬依赖：缺 chat 时无法抽取陈述/实体/三元组，缺 embedding 时无法进入实体去重和图谱检索准备，任务会捕获异常、将 Memory 设为 `failed` 并保存最多 500 个字符的错误。

`run_extraction` 成功后，任务把 Memory 改为 `done`，写入 `graph_dialogue_id` 和 `graph_stats`。因此 PG 是任务状态的权威来源，Neo4j 不负责 pending/failed 状态。

## 5. 图模型：为什么要有四层溯源

文件：[graph_models.py](/D:/Comet-main/api/app/core/memory/graph_models.py)

图的来源层：

```text
(:Dialogue {原始输入})
  -[:HAS_CHUNK]-> (:Chunk {文本片段})
  -[:HAS_STATEMENT]-> (:Statement {原子陈述})
  -[:MENTIONS]-> (:Entity {人、组织、偏好、目标等})
```

语义层：

```text
(:Entity)-[:RELATION {predicate, confidence, statement_id}]->(:Entity)
(:Event)-[:INVOLVES {role}]->(:Entity)
(:Entity)-[:IN_COMMUNITY]->(:Community)
(:Insight)-[:DERIVED_FROM]->(:Entity)
```

这保留了“一个结论来自哪段输入、哪句陈述”的路径。`Statement` 有事实/观点/预测/建议类型、时间属性、重要性、置信度、情绪字段和 memory layer；`Entity` 有别名、重要性、置信度、提及/访问次数、长期层级、画像字段。所有节点与边都带 `user_id`，检索查询也必须带此过滤条件。

## 6. 图谱 Schema：启动时建立的约束与索引

文件：[graph_schema.py](/D:/Comet-main/api/app/core/memory/graph_schema.py)、[main.py](/D:/Comet-main/api/app/main.py)

应用启动 lifecycle 会调用 `ensure_graph_schema()`。它逐条执行 `IF NOT EXISTS` Cypher：

1. Dialogue、Chunk、Statement、Entity、Event、Community、Insight 的 `id` 唯一约束，保证 `MERGE` 幂等。
2. 高频 `user_id`、Entity name、memory_layer、Insight theme 等普通属性索引。
3. Entity、Statement、Event、Insight 的 CJK 全文索引，支持中文关键词检索。
4. Entity name、Statement、Event、Insight 的余弦向量索引，维度为 `settings.embedding_dims`。

每条 schema 语句单独 try/catch；某项失败只记 warning，启动不会被阻断。例如旧 Neo4j 不支持 vector index 时，应用可以启动，但向量召回将降级或失败。Embedding 输出维度与 `embedding_dims` 不一致时，写向量/查向量会有运行时问题，必须同步调整和重建索引。

## 7. 抽取编排：`run_extraction` 的 12 个阶段

文件：[extraction/orchestrator.py](/D:/Comet-main/api/app/core/memory/extraction/orchestrator.py)

1. 清理空文本；空文本直接返回零统计，不写图。
2. 创建 Dialogue 根节点并记录其 ID。
3. `chunker.split_chunks` 切来源文本，创建有 sequence 的 Chunk 节点。
4. 对每个 Chunk 调 chat 模型抽取原子 Statement；多块时传入全文作为 context，减少跨块失真。
5. 对每条 Statement 调 chat 模型批量抽取实体、三元组和事件；实体类型与谓词经过 ontology 标准化。
6. 先构造内存中的 Statement、Entity、MENTIONS、待连的三元组和事件，不立即逐条写库。
7. 批量生成 Entity name embedding。embedding 失败时 `embedder` 返回等长 `None` 列表，仍允许图谱写入，但相似去重和向量检索质量下降。
8. 批内去重：相同 name+type 直接合并；同类型且名称文本/向量相似度达到 0.80 或存在包含关系时，再让 chat 模型判断是否为同一实体，置信度至少 0.80 才合并。
9. 图内去重：按类型取用户现有 Entity，同名直接复用已有节点；近似候选再用同样的 LLM 判定。两个阶段产出 old ID -> canonical ID 的 redirect 表。
10. 用 redirect 重写 MENTIONS、三元组的 subject/object 和 Event participants，避免关系连到已合并的临时节点。
11. 单次 Neo4j 写事务写入所有节点与边；随后尽力增量聚类和累积计数触发反思，它们失败不影响此次抽取成功。
12. 返回统计，供 Celery 写回 PostgreSQL。

如果本次没有抽到 Entity，系统仍会写 Dialogue、Chunk、Statement，保留完整溯源；只是没有 RELATION/Event/Entity。

## 8. Neo4j Repository 与 Cypher：原子写入和查询边界

文件：[memory_graph_repository.py](/D:/Comet-main/api/app/repositories/neo4j/memory_graph_repository.py)、[cypher_queries.py](/D:/Comet-main/api/app/repositories/neo4j/cypher_queries.py)

`save_graph` 在一个 `execute_write` 事务中按依赖顺序写 Dialogue、Chunk、Statement、Entity、Event，再写 MENTIONS、RELATION、INVOLVES。Cypher 统一使用 `UNWIND $rows + MERGE` 批量操作：节点按 id MERGE，关系按确定业务键 MERGE，因此重试不会无界复制节点和边。

实体写入会保留更高 importance、累积 mention_count、保留已有 long-term layer 与 access_count。关系用 `(source, predicate, target)` 作为合并键，保留更高 importance。这个策略意味着“新抽取不应降级稳定记忆”，但也意味着错误的高 importance/高 confidence 需要走人工审查更正。

Repository 每个对外查询都把 `user_id` 作为 Cypher 参数：向量索引先返回候选节点，再用 `WHERE node.user_id = $user_id` 做租户隔离；全文检索同理。因为 Neo4j 的 vector/fulltext index 本身是跨用户的，所以这个后置过滤绝不能漏。

## 9. 检索：普通搜索和主动召回的区别

### 9.1 `searcher.py`

文件：[retrieval/searcher.py](/D:/Comet-main/api/app/core/memory/retrieval/searcher.py)

普通记忆搜索执行两路召回：Entity name embedding 的 Neo4j vector index，以及 Entity name/description/aliases 的 CJK fulltext index。两路独立失败：向量失败会退化为全文；全文失败也可以仅返回向量结果。

常规融合先分别 min-max 归一化，再计算 `0.55 * vector + 0.30 * fulltext + 0.15 * importance`。非可靠性模式下，long-term 再加 0.05；主动召回的可靠性模式则使用 `semantic_score * confidence * long_term_weight(1.1)`，并过滤低 confidence。

命中后会把 Entity 的 `access_count + 1`、`last_access_at` 回写，失败不阻断响应。然后额外查一跳 `RELATION` 邻居，把“实体 + 关联事实”一起返回给模型或 UI。

### 9.2 `active_recall.py`

文件：[retrieval/active_recall.py](/D:/Comet-main/api/app/core/memory/retrieval/active_recall.py)

主动召回在每轮聊天的 system prompt 之前执行。它最多 3.5 秒，超时、缺 embedding、任一路失败都返回空字符串，不阻断对话。query 只 embedding 一次；Insight 向量召回与 Entity 搜索并行执行，Entity 搜索复用已计算 query vector。

它使用 `active_recall_entity_top_k`、`active_recall_insight_top_k`、最小余弦分数、最小置信度等配置。低于 `active_recall_uncertain_confidence` 的内容会加“待确认”前缀，提醒模型不要当成确定事实。最后截断到 `active_recall_max_chars`，避免记忆挤占对话上下文。

`ChatService._recall_memory` 使用 optional embedding client：用户未配 embedding 时直接跳过。服务还有用户级缓存：首轮无缓存时同步计算一次；后续将缓存立即注入，并对实质消息在后台刷新，降低首 token 延迟。

## 10. 你应当记住的失败边界

1. Worker 未运行或未监听 `memory` 队列：PG Memory 将长期是 pending。
2. 未配置 chat 或 embedding：任务进入 failed，错误在 `memories.error_msg`；主动召回则静默跳过以保障聊天。
3. Neo4j schema 中向量索引创建失败：图谱仍可写入，普通搜索可能仅全文可用，主动实体召回会缺失。
4. embedding 失败：抽取会写入无向量的图，但跨轮去重和向量召回退化；不是所有记忆抽取都会失败。
5. 人工删除 Entity：`DETACH DELETE` 会删除其相连关系，但 PG 原文溯源记录仍存在。
6. PG、Redis、Neo4j 不是分布式事务：任务状态与图写入发生在不同系统；应以失败状态、日志和重试/修复流程处理不一致。

## 11. 推荐的断点顺序

1. `MemoryPage.onRemember`：观察文本进入 `/remember`。
2. `MemoryService.remember`：确认 PG commit 后才 `.delay`。
3. `tasks/memory._extract`：观察 `pending -> extracting -> done/failed`。
4. `run_extraction`：观察 Entity 临时 ID 怎样经过两张 redirect 表变成最终 Neo4j ID。
5. `MemoryGraphRepository.save_graph`：观察单事务中的节点先写、边后写。
6. `search_memory`：对比 vector/fulltext 两组结果和融合得分。
7. `recall_context`：观察最终注入 prompt 的文本为何带“待确认”与长度上限。
