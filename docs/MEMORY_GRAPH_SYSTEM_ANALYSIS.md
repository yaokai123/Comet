# 记忆系统与 Neo4j 图谱：代码级说明

本文依据当前仓库实现说明记忆系统的真实运行方式，覆盖前端、HTTP 接口、PostgreSQL 任务记录、Celery 抽取、Neo4j 图谱、去重、社区、反思与主动召回。

## 1. 系统分工

系统并非把聊天文本直接作为“记忆”返回，而是把文本转为可溯源的图结构。

```text
手动输入 / 用户聊天消息
  -> PostgreSQL memories 任务记录
  -> Celery 异步抽取
  -> Chat 模型：陈述、实体、三元组、事件
  -> Embedding：实体名向量
  -> 批内去重 + 图内去重
  -> Neo4j：溯源层和语义层图谱
  -> 检索 / 主动召回：实体、关系、Insight 注入聊天上下文
```

| 存储 | 保存内容 | 主要目的 |
| --- | --- | --- |
| PostgreSQL `memories` | 原始文本、来源、任务状态、错误、图写入统计 | 异步任务账本、审计、列表展示 |
| Neo4j | 对话溯源、陈述、实体、事件、关系、社区、洞察 | 图关系查询、向量/全文检索、画像与召回 |
| Redis | 新增实体计数、聊天侧召回缓存 | 反思触发和低延迟聊天 |

`Memory` 不是 Neo4j 图节点的 ORM 映射。它只是 PostgreSQL 中的一次“待/已处理记忆输入”记录；真正被查询和召回的实体图在 Neo4j。

## 2. 前端：用户能看到什么

### 2.1 `web/src/pages/MemoryPage.tsx`

该页是记忆业务控制台，分为五个模式：个人画像、主题社区、时间线、记忆搜索、审查纠错。

#### 主动记忆与画像

`ProfilePanel` 加载 `/memories/profile`，将 Neo4j 中所有 `Entity` 按 `type` 分组，显示名称、描述、别名、出边关系、置信度、短/长期层级、提及和访问次数。

用户点击“记住”时：

1. 前端清除首尾空白并拒绝空输入；
2. 调用 `memoryApi.remember(value)`，即 `POST /memories/remember`；
3. API 立即返回 `pending` 记录，而不是等待模型抽取；
4. 前端开始每 4 秒刷新一次画像，最多 6 次；因此界面表现为“提交后稍后出现实体”。

页面的“记忆巩固”调用 `/memories/consolidate`；“重新认识你”调用 `/memories/reflect`；两者均是在已有 Neo4j 图上工作的后处理，不重新解析原文。

#### 搜索、社区、时间线和审查

* 搜索面板将 query 发至 `POST /memories/search`，呈现实体卡和其一跳关系。
* 社区面板调用 `/memories/communities`，默认隐藏只有一个成员的社区；点击卡片再获取成员详情；“重新聚类”调用 `/memories/recluster`。
* 时间线读取 `/memories/timeline` 中的 `Event` 节点及其 `INVOLVES` 参与者。
* 审查面板面向低置信实体。确认或修正会把 Neo4j 实体标为人工确认，并在 PostgreSQL `memory_corrections` 保存操作前后快照；删除先记录快照再删图节点。

### 2.2 `web/src/pages/GraphPage.tsx`

该页不是只显示 `Entity`。它请求 `/memories/graph` 的完整溯源图，包含 `Dialogue`、`Chunk`、`Statement`、`Entity`、`Event` 五类节点，和 `HAS_CHUNK`、`HAS_STATEMENT`、`MENTIONS`、`RELATION`、`INVOLVES` 五类边。

它用力导向图绘制，提供：节点类型筛选、按名称搜索、高亮邻居、拖动固定节点、点击展示节点属性。源节点名称可能很长，所以服务层会截断 `Dialogue/Chunk/Statement` 的显示名称；实体和事件保留名称/标题。

## 3. HTTP 层：`memory_controller.py`

控制器不包含抽取或 Cypher。其职责是当前用户鉴权、创建 `MemoryService` 并返回统一响应。

主要接口如下：

| 路由 | Service 方法 | 真实数据来源 |
| --- | --- | --- |
| `POST /memories/remember` | `remember` | 先写 PostgreSQL，再投递 Celery |
| `POST /memories/search` | `search` | Neo4j 向量、全文、关系遍历 |
| `GET /memories/profile` | `get_profile` | Neo4j `Entity` |
| `GET /memories/graph` | `get_graph` | Neo4j 全量溯源图 |
| `GET /memories/timeline` | `get_timeline` | Neo4j `Event` |
| `POST /memories/recluster` | `recluster` | Neo4j 社区重建 |
| `POST /memories/consolidate` | `consolidate` | Neo4j 短期提升、画像增强 |
| `POST /memories/reflect` | `reflect` | Neo4j `Insight` 归纳 |

所有接口都将 `user.id` 传入 Service；Neo4j 查询也继续带 `user_id`，形成第二道租户隔离。

## 4. PostgreSQL 任务记录：`memory_model.py`

`Memory` 的关键字段：

* `raw_text`：原始输入，手动记忆或用户聊天文本；
* `source`：`manual` 或 `auto`；
* `status`：`pending`、`extracting`、`done`、`failed`；
* `error_msg`：抽取异常时最多保存 500 字符；
* `graph_dialogue_id`：此次抽取创建的 Neo4j `Dialogue.id`；
* `graph_stats`：抽取统计，例如 chunk、statement、entity、relation、event 数量及实体 ID；
* `source_message_id`：为对话消息溯源预留的 UUID 字段。

状态机为：

```text
创建 Memory -> pending -> Celery 开始 -> extracting -> done
                                            `-> failed
```

当前自动对话入库会保存 `raw_text` 与 `source=auto`，但未给 `source_message_id` 赋值；所以字段已经存在，消息 ID 级溯源尚未被实际使用。

## 5. Service：`memory_service.py`

### 5.1 创建、列表和删除

`remember(user_id, text)` 先 `strip()` 和校验空文本，创建 `Memory(status=pending)`，持久化后调用 `extract_memory_task.delay(str(memory.id))`。这保证接口快返回，但也意味着“任务已投递”与“图已成功写入”是两个阶段。

`get_detail` 比对 `memory.user_id`，避免通过 UUID 越权读取。`list_memories` 只访问 PostgreSQL，因此其列表反映的是任务记录。`to_out_dict` 直接输出原文、状态、错误及图统计。

`delete(memory_id)` 当前只删除 PostgreSQL `Memory`。它不会删除 Neo4j 中由该记录创建的 `Dialogue/Chunk/Statement/Entity/Relation`，因此“删除任务记录”不等于“遗忘图谱事实”。实体删除应走 `delete_entity` 或审查删除接口，但后者也会影响所有来源共享的实体。

### 5.2 画像与图可视化

`get_profile` 调用 `MemoryGraphRepository.list_all_entities` 和 `entity_type_counts`；它把仓储结果转换为 UI 所需字段并按实体类型分组。`get_graph` 读取完整节点、完整边和社区，做 JSON 友好转换。`get_entity_subgraph` 获取目标实体的一跳邻居；`get_timeline` 将 `Event` 与参与者格式化。

### 5.3 人工反馈闭环

`confirm_entity`：先读图节点快照，Neo4j 将 `human_verified=true`、`confidence=1.0`、`memory_layer=long_term`，随后尽力把审计记录写入 PostgreSQL。

`correct_entity_with_reason`：更新名称、类型、描述或别名，同时人工确认；再写 `memory_corrections`。

`delete_entity_with_reason`：与前两者不同，它先写 PostgreSQL 审计；审计写入失败就取消删除，避免没有撤销依据的硬删除；成功后用 Neo4j `DETACH DELETE` 删除实体和所有关系。

## 6. 对话自动入口：`chat_service.py`

系统有两条输入路径：

```text
MemoryPage 手动输入 -> MemoryService.remember -> Celery
聊天中的用户消息   -> ChatService._dispatch_memory -> Celery
```

`_dispatch_memory` 在聊天业务成功后创建 `Memory(source=auto)` 并投递相同任务。其异常被捕获并忽略，因此 Neo4j、Redis、模型配置或队列异常不会让聊天回答失败。

聊天前还会调用 `_recall_memory` / `_recall_lagged`：如果当前用户没有召回缓存，第一轮同步计算；有缓存时立即使用旧结果，并对非闲聊消息在后台刷新。该策略优先保障首 token 延迟。

## 7. Celery 任务：`tasks/memory.py`

Celery 入口 `extract_memory_task` 是同步函数，内部用 `asyncio.run(_run(memory_id))` 执行异步数据库和 Neo4j 操作。

每个任务：

1. 以 `create_task_engine()` 建立任务级 engine/session；
2. 查询 PostgreSQL `Memory`；不存在则记录 warning 并退出；
3. 更新为 `extracting`；
4. 读取该用户所配置的 chat 与 embedding 客户端；两者都是必需项；
5. 调用 `run_extraction(...)`；
6. 成功时回写 `done`、`graph_dialogue_id`、`graph_stats`；
7. 任一步抛异常则改为 `failed` 并保存错误；
8. `finally` 中关闭任务级 PostgreSQL engine 与本事件循环内 Neo4j driver。

这套“每任务独立异步资源”的写法避免 Celery 多任务复用已关闭事件循环中的连接。

## 8. 抽取编排：`extraction/orchestrator.py`

`run_extraction` 是从文本到图的核心函数。

### 8.1 建立来源与分块

先把全文作为一个 `DialogueNode`，再用 `preprocessing.chunker.split_chunks` 拆为 `ChunkNode`。`Dialogue` 表示一次用户输入来源；`Chunk` 是模型处理粒度，避免把超长输入一次送入模型。

### 8.2 Chat 模型抽取

每一个 chunk：

1. `statement_extractor.extract_statements` 将文本化为原子陈述，并产生陈述类型、时间属性、重要度、置信度和情绪字段；
2. `triplet_extractor.extract_triplets_batch` 对陈述批量抽取实体、三元组和事件；
3. 为每条陈述创建 `StatementNode`；
4. 为抽取实体建立临时 `EntityNode`，并连 `Statement -[:MENTIONS]-> Entity`；
5. 暂存三元组和事件，等待去重后重定向到最终实体 ID。

即使某段没有实体，系统仍会把 `Dialogue/Chunk/Statement` 写入 Neo4j，保证原文溯源链不因抽取为空而丢失。

### 8.3 向量化与两层去重

`embedder.embed_texts` 批量嵌入实体名称，写入 `EntityNode.name_embedding`。embedding 调用失败会返回与输入等长的 `None`，后续仍可按精确名称处理，不阻塞整次抽取。

`dedup_within_batch` 是第一层：

* 同名、同类型实体直接合并；
* 同类型且名称字符相似、包含关系或向量余弦相似度达到候选门槛的实体，交给 chat 模型用低温度判断；
* 置信度达到阈值才合并；
* 合并时汇总别名、较长描述、最大重要度/置信度、提及次数和连接强度；
* 输出旧临时 ID 到保留 ID 的 `redirect` 表。

`merge_with_graph` 是第二层：

* 按实体类型读取 Neo4j 已有实体并缓存；
* 同名同类型直接复用已有图节点 ID；
* 对近似候选再次调用模型确认；
* 命中时把新数据合并到已有节点，不新建图节点；
* 输出本次节点 ID 到既有图节点 ID 的第二张 `redirect` 表。

编排器将两张重定向表串联，重写 `MENTIONS`、三元组 `RELATION` 以及事件参与者的目标 ID，防止边指向已被合并的临时节点。

### 8.4 关系、事件和持久化

三元组变为 `Entity -[:RELATION]-> Entity`，保留规范谓词、原始谓词、来源 Statement、有效/失效时间、重要度与置信度。事件变成 `EventNode`，参与者按当前 chunk 的名称匹配到最终实体，形成 `Event -[:INVOLVES]-> Entity`。

最后 `_persist` 调用 `MemoryGraphRepository.save_graph` 一次性保存完整图。之后增量社区聚类和自动反思都被独立 `try/except` 包住，失败不应使本次抽取失败。

## 9. 图模型：`graph_models.py`

### 9.1 四层溯源层

```text
Dialogue -> Chunk -> Statement -> Entity
```

* `Dialogue`：一次手动输入或自动对话输入，存来源全文、来源类型、可能的消息 ID 和发生时间。
* `Chunk`：来源的顺序分块，存文本和 sequence。
* `Statement`：最小可判断单元，区分 `FACT`、`OPINION`、`PREDICTION`、`SUGGESTION`；还带动态/静态/无时间语义、置信度、情绪和记忆层级。
* `Entity`：人物、组织、偏好、目标等可复用事实对象，带别名、描述、名称向量、重要度、置信度、短/长期层级、提及和访问次数。

### 9.2 语义和产品层

* `RELATION`：实体间的受控谓词三元组；
* `Event` 与 `INVOLVES`：支持按事件时间展示时间线；
* `Community` 与 `IN_COMMUNITY`：相关实体的主题聚类；
* `Insight` 与 `DERIVED_FROM`：反思引擎对多实体的高层结论，并保留其归纳依据。

所有节点与边均包含 `user_id`，使图数据能被用户维度过滤。

## 10. Schema：`graph_schema.py` 和启动过程

`main.py` 的 FastAPI lifespan 启动时调用 `ensure_graph_schema()`。该函数依次执行：

1. `Dialogue/Chunk/Statement/Entity/Event/Community/Insight` 的 `id` 唯一约束；
2. `Entity/Event/Statement/Insight` 的 `user_id` 与常用属性索引；
3. 使用 CJK analyzer 的实体、陈述、事件、洞察全文索引；
4. 实体名称、陈述、事件、洞察的 Neo4j 向量索引，维度取 `settings.embedding_dims`，相似度为 cosine。

单条 schema 语句失败仅 warning 后继续。例如较旧 Neo4j 不支持 vector index 时，图基础能力仍可启动，但向量召回会降级或失败。

## 11. Neo4j 仓储：`repositories/neo4j/`

### 11.1 `MemoryGraphRepository`

这是唯一的图数据访问层。`save_graph` 把 Pydantic 节点/边转为参数行，在同一个写事务中用 Cypher 批量 `MERGE` 写入节点和关系。业务层不直接写 Cypher。

主要方法分组：

| 类别 | 方法 |
| --- | --- |
| 写入 | `save_graph` |
| 去重候选 | `list_entities_by_type`、`get_entity_by_name` |
| 检索 | `search_entities_by_vector`、`search_entities_by_fulltext`、`get_entity_neighbors` |
| 动力学 | `bump_entity_access`、`promote_short_to_long`、`write_entity_profile` |
| 人工纠错 | `entity_snapshot`、`human_verify_entity`、`correct_entity`、`delete_entity` |
| 可视化 | `graph_full_nodes`、`graph_full_edges`、`entity_subgraph`、`event_timeline` |
| 洞察 | `upsert_insight`、`list_insights`、`search_insights_by_vector` |

`RELATION` 边保留 `statement_id`，因此关系可以回到它由哪句陈述产生。实体读取会附带一跳出边供画像卡与召回文本使用。

### 11.2 `CommunityRepository`

该仓储负责社区增量/全量聚类所需的数据操作：读取带向量的实体、读取邻居投票信息、upsert 社区、为实体分配社区、刷新成员数、生成社区成员/关系输入、写入 AI 摘要、列出并清理空社区。

社区本身是从实体向量和关系结构推导出的二级视图，不是抽取阶段必然出现的原始事实。

## 12. 检索：`retrieval/searcher.py`

`search_memory` 返回的是实体而非原文 chunk，流程为：

1. 用 embedding 模型得到 query 向量，Neo4j `entity_embedding_index` 召回候选；
2. 同时用 CJK 全文索引按 query 查询实体名称、描述、别名；
3. 分别归一化分数，以 `0.55 * vector + 0.30 * fulltext + 0.15 * importance` 融合；普通检索还给长期记忆小幅加分；
4. 可选按置信度过滤，并使用 `semantic_score * confidence * long_term_weight` 作为可靠性排序；
5. 命中实体的 `access_count` 与 `last_access_at` 回写；
6. 查询每个命中实体的一跳 `RELATION` 邻居，拼入结果。

有 `min_vector_score` 时进入精确模式：只接受余弦相似度达到门槛的向量结果，避免仅关键词命中的无关实体。

## 13. 主动召回：`retrieval/active_recall.py`

主动召回用于聊天前的 system prompt 背景，不等同于用户在页面点“搜索”。

1. 为空 query 直接返回空；
2. 总流程有 3.5 秒超时，超时或异常直接不注入；
3. query 只做一次 embedding；
4. 并行执行两路：向量检索 `Insight`，以及调用 `search_memory` 检索实体和关系；
5. 实体召回开启相似度与置信度门槛，并按可靠性排序；
6. 低置信实体或关系前添加“待确认”；
7. 把洞察和实体事实组装为“关于用户的已知信息”文本，并按 `active_recall_max_chars` 截断；
8. `ChatService` 将该文本插入聊天上下文。

这说明记忆不会被视为绝对真相：置信度不足的内容应在回答中保留不确定性或再次向用户确认。

## 14. 当前实现的关键边界

1. **两个存储不具备跨库事务。** PostgreSQL 状态回写与 Neo4j 图保存不能原子提交；任务失败状态只说明任务可见异常，不是严格分布式事务保证。
2. **删除 `Memory` 不删除图。** 这是当前最明显的生命周期不一致；应明确“删除任务记录”和“按来源遗忘图数据”的产品语义。
3. **自动来源未保存 `source_message_id`。** 模型已有字段和图节点字段，但聊天派发点没有传递消息 ID。
4. **去重依赖模型判断。** 精确同名可靠，近似名称依赖 embedding 候选和 chat 模型，因此应配合审查页、人工确认和纠错审计。
5. **向量功能受模型维度与 Neo4j 版本约束。** embedding 输出维度必须匹配 `settings.embedding_dims`；向量索引创建失败不会阻止服务启动，但会影响语义召回。
