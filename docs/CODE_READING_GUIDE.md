# Comet 代码精读路线图

这份指南按“先骨架、再链路、后细节”的顺序拆解项目。建议不要从页面或某个大 service 直接钻进去；先建立运行时地图，再沿着一条真实业务链路读到底。

## 0. 先建立整体地图

目标：知道这个项目由哪些进程、存储和代码层组成。

先读：

- `docker-compose.yml`：看服务拓扑。核心服务是 `api`、`web`、`worker`、`beat`，依赖 PostgreSQL、Elasticsearch、Neo4j、Redis。
- `.env.example`：看运行时配置从哪里来。
- `api/Dockerfile`、`web/Dockerfile`：看后端和前端如何被构建进容器。
- `api/app/config.py`：所有后端配置项的集中入口。

你需要回答：

- API、Web、Worker、Beat 分别负责什么？
- PostgreSQL、Elasticsearch、Neo4j、Redis 各自保存什么？
- `embedding_dims` 为什么重要？它会影响 ES 和 Neo4j 的向量索引维度。

## 1. 后端启动流程

目标：知道 FastAPI 启动时做了哪些初始化。

先读：

- `api/app/main.py`
- `api/app/controllers/router.py`
- `api/app/core/exceptions.py`
- `api/app/core/response.py`
- `api/app/core/dependencies.py`
- `api/app/db/postgres.py`
- `api/app/db/elastic.py`
- `api/app/db/neo4j.py`
- `api/app/db/redis.py`

阅读重点：

- `main.py` 的 `lifespan` 会自动执行数据库迁移、初始化 ES 索引、初始化 Neo4j 图谱 schema、启动 tracing recorder。
- `router.py` 把所有 controller 挂到 `/api` 下。
- controller 层只处理 HTTP 入参、权限和响应；核心业务通常在 service。

你需要回答：

- 一个请求从 `/api/...` 到 controller 再到 service 的路径是什么？
- 启动失败时，哪些初始化失败只是 warning，哪些会影响核心功能？
- 统一响应 `{ code, message, data }` 在哪里封装？

## 2. 认证和用户模型

目标：先掌握多用户隔离和 token 机制，因为后续所有业务都依赖 `current_user`。

按顺序读：

- `api/app/controllers/auth_controller.py`
- `api/app/services/auth_service.py`
- `api/app/models/user_model.py`
- `api/app/schemas/auth_schema.py`
- `api/app/core/security.py`
- `web/src/components/RequireAuth.tsx`
- `web/src/stores/authStore.ts`
- `web/src/api/client.ts`

阅读重点：

- 登录后前端把 token 放在 `localStorage`。
- `client.ts` 的 axios 拦截器给请求加 `Authorization`。
- 后端通过依赖注入拿当前用户。
- 大部分业务表都有 `user_id`，这是多租户隔离的边界。

你需要回答：

- 未登录访问受保护页面会发生什么？
- token 过期时前端如何跳转？
- 后端如何保证用户只能读自己的数据？

## 3. 模型配置和 LLM 调用

目标：理解这个项目如何接入 OpenAI/通义/智谱/DeepSeek 等模型。

按顺序读：

- `api/app/controllers/model_config_controller.py`
- `api/app/services/model_config_service.py`
- `api/app/models/model_config_model.py`
- `api/app/repositories/model_config_repository.py`
- `api/app/core/llm/provider.py`
- `api/app/core/llm/client.py`
- `api/app/core/llm/resolver.py`
- `api/app/core/llm/chat_model.py`
- `web/src/pages/ModelConfigPage.tsx`
- `web/src/pages/modelConfig/ModelConfigModal.tsx`
- `web/src/pages/modelConfig/constants.ts`

阅读重点：

- 模型配置存 PostgreSQL，API Key 用 Fernet 加密。
- embedding/chat/rerank/websearch 都是模型配置类型。
- `provider.py` 负责默认 base_url 和连接测试。
- `client.py` 是低层 HTTP 调用封装，embedding 统一走 `{base_url}/embeddings`。
- `resolver.py` 从当前用户的默认模型配置构建 LLM client。

你需要回答：

- 默认 embedding 模型是如何被找到的？
- 智谱 `embedding-3` 的 base_url 和模型名在哪里配置？
- 如果没配置默认 chat 或 embedding，业务会在哪一步失败？

## 4. 知识库 RAG 主链路

目标：读懂“上传文档 -> 异步解析 -> 分块 -> 向量化 -> 检索 -> 对话引用”的完整链路。

建议按真实流程读：

1. 前端入口
   - `web/src/pages/KnowledgeBasePage.tsx`
   - `web/src/pages/KnowledgeDetailPage.tsx`
   - `web/src/api/knowledge.ts`
   - `web/src/api/documents.ts`

2. HTTP 入口
   - `api/app/controllers/knowledge_base_controller.py`
   - `api/app/controllers/document_controller.py`
   - `api/app/controllers/file_controller.py`

3. 业务层
   - `api/app/services/knowledge_base_service.py`
   - `api/app/services/document_service.py`
   - `api/app/models/knowledge_base_model.py`
   - `api/app/models/document_model.py`

4. 异步任务和索引
   - `api/app/celery_app.py`
   - `api/app/tasks/parse.py`
   - `api/app/core/rag/es_index.py`
   - `api/app/core/rag/search.py`

阅读重点：

- 文档上传后通常不会同步完成解析，耗时工作进 Celery。
- ES 同时承担 BM25 和向量检索。
- embedding client 是检索链路的硬依赖。

你需要回答：

- 文档状态在哪里更新？
- 文档内容是如何进入 ES 的？
- 查询时是如何生成 query embedding 的？

## 5. 记忆系统和 Neo4j 图谱

目标：理解“对话/主动记忆 -> 抽取实体和陈述 -> 去重 -> 写图谱 -> 主动召回”。

按顺序读：

- `web/src/pages/MemoryPage.tsx`
- `web/src/pages/GraphPage.tsx`
- `api/app/controllers/memory_controller.py`
- `api/app/services/memory_service.py`
- `api/app/models/memory_model.py`
- `api/app/tasks/memory.py`
- `api/app/core/memory/graph_schema.py`
- `api/app/core/memory/graph_models.py`
- `api/app/core/memory/extraction/orchestrator.py`
- `api/app/core/memory/extraction/embedder.py`
- `api/app/core/memory/extraction/dedup.py`
- `api/app/core/memory/retrieval/active_recall.py`
- `api/app/core/memory/retrieval/searcher.py`
- `api/app/repositories/neo4j/`

阅读重点：

- PostgreSQL 保存任务/记录状态，Neo4j 保存图谱结构。
- 记忆抽取依赖 chat 模型，去重和召回依赖 embedding。
- 图谱 schema 初始化在 `main.py` 启动流程里触发。

你需要回答：

- 哪些信息进入 PostgreSQL，哪些进入 Neo4j？
- 实体向量和陈述向量分别用于什么？
- 对话时主动召回记忆是如何被注入上下文的？

## 6. 对话 Agent 主链路

目标：理解用户发消息后，系统如何选择工具、流式返回、保存消息、触发记忆和情绪分析。

按顺序读：

- `web/src/pages/ChatPage.tsx`
- `web/src/pages/chat/`
- `web/src/api/chat.ts`
- `api/app/controllers/chat_controller.py`
- `api/app/services/chat_service.py`
- `api/app/models/conversation_model.py`
- `api/app/core/agent/tools/registry.py`
- `api/app/core/agent/tools/builtin/memory.py`
- `api/app/core/agent/tools/builtin/knowledge.py`
- `api/app/core/agent/tools/builtin/web_search.py`
- `api/app/core/llm/chat_model.py`

阅读重点：

- 前端对话通常是 SSE/流式读取。
- 工具注册在 agent tools registry。
- 强模型可能走 function calling，弱模型可能走 ReAct 降级。
- 对话完成后可能触发记忆抽取、情绪分析等异步任务。

你需要回答：

- 一轮用户消息如何被保存？
- Agent 有哪些内置工具？
- 工具调用结果如何回到最终回答？

## 7. 深度研究、定时任务和 Verifier Loop

目标：理解项目里更复杂的 Agent 工作流。

按顺序读：

- `web/src/pages/ResearchPage.tsx`
- `web/src/pages/AgentTaskPage.tsx`
- `api/app/controllers/research_controller.py`
- `api/app/controllers/agent_task_controller.py`
- `api/app/services/research_service.py`
- `api/app/services/agent_task_service.py`
- `api/app/tasks/agent_task.py`
- `api/app/core/agent/research/`
- `api/app/core/agent/loop/controller.py`
- `api/app/core/agent/loop/verifier/`
- `api/app/core/agent/loop/repair/`
- `api/app/models/research_report_model.py`
- `api/app/models/agent_task_model.py`
- `api/app/models/loop_model.py`

阅读重点：

- `beat` 队列做轻量调度心跳，`research` 队列跑重任务。
- 研究报告是多阶段流水线，不是单次 LLM 调用。
- Verifier Loop 会对产出打分，不合格时进入修复或重写。

你需要回答：

- 定时任务如何被发现并投递？
- 研究报告生成分为哪几个阶段？
- verifier 和 repair 的职责边界是什么？

## 8. 可观测性和成本

目标：理解 trace、token、cost 如何记录和展示。

按顺序读：

- `web/src/pages/TracesPage.tsx`
- `web/src/components/trace/`
- `api/app/controllers/trace_controller.py`
- `api/app/services/trace_service.py`
- `api/app/models/agent_trace_model.py`
- `api/app/core/agent/tracing/tracer.py`
- `api/app/core/agent/tracing/span_recorder.py`
- `api/app/core/agent/tracing/pricing.py`

阅读重点：

- trace 是跨 Agent/LLM/tool 的执行链路记录。
- LLM 和 embedding 调用都会尝试记录 token 和成本。
- recorder 是异步落库，启动/关闭在 `main.py`。

你需要回答：

- span 在哪里创建，在哪里落库？
- 模型成本单价在哪里维护？
- 前端如何把 trace 展示成时间线？

## 9. 前端整体结构

目标：能从路由定位任意页面，再从页面定位 API。

按顺序读：

- `web/src/App.tsx`
- `web/src/layouts/MainLayout.tsx`
- `web/src/api/client.ts`
- `web/src/api/`
- `web/src/pages/HomePage.tsx`
- `web/src/pages/ModelConfigPage.tsx`
- `web/src/pages/KnowledgeBasePage.tsx`
- `web/src/pages/ChatPage.tsx`
- `web/src/pages/MemoryPage.tsx`
- `web/src/pages/ResearchPage.tsx`
- `web/src/stores/`
- `web/src/index.css`
- `web/src/theme.ts`

阅读重点：

- `App.tsx` 是路由总表。
- `MainLayout` 决定菜单、导航和页面外壳。
- `api/client.ts` 统一处理 baseURL、token、错误和 401 跳转。
- 每个页面通常对应一个 `web/src/api/*.ts` 文件和一个后端 controller。

你需要回答：

- 浏览器模式和桌面端模式的路由有什么区别？
- 一个页面按钮点击后，如何定位到对应后端接口？
- Dashboard 首页的数据从哪些 API 来？

## 10. 建议的精读节奏

第一轮：只读骨架，不深挖实现。

1. `docker-compose.yml`
2. `api/app/main.py`
3. `api/app/controllers/router.py`
4. `web/src/App.tsx`
5. `api/app/celery_app.py`

第二轮：读最小可用产品链路。

1. 注册/登录
2. 模型配置
3. 知识库上传和检索
4. 单人对话

第三轮：读智能能力。

1. 记忆抽取和主动召回
2. Agent 工具编排
3. 深度研究
4. Verifier Loop
5. Tracing 和成本

第四轮：读工程质量。

1. 多用户隔离是否一致
2. 异步任务失败如何恢复
3. embedding 维度变更的影响面
4. API Key 加密和日志泄露风险
5. 前端错误态、loading 态和鉴权跳转

## 11. 精读时的笔记模板

每读一个模块，建议记录这 6 件事：

```text
模块：
入口文件：
核心数据表：
依赖的外部服务：
主流程：
失败路径：
我还没弄懂的问题：
```

示例：

```text
模块：模型配置
入口文件：api/app/controllers/model_config_controller.py
核心数据表：model_configs
依赖的外部服务：各模型 provider 的 OpenAI 兼容接口
主流程：前端提交配置 -> 后端加密 API Key -> 入库 -> 设置默认 -> 测试连接
失败路径：Key 无效、base_url 错误、没有默认模型、embedding 维度不兼容
我还没弄懂的问题：测试连接失败时前端是否能展示 provider 返回的详细错误？
```

## 12. 不建议一开始深挖的地方

这些模块重要，但不适合作为第一批阅读入口：

- `api/app/core/agent/loop/`：抽象层较多，先读完 research 再看。
- `api/app/core/memory/clustering/`：属于记忆系统高级优化，先掌握 extraction/retrieval。
- `api/eval/`：离线评测体系，等主业务链路清楚后再看。
- 大量 prompt 模板：先看调用它们的 service/orchestrator，再回头看 prompt。

## 13. 推荐的第一天阅读任务

如果你今天只读 2 到 3 小时，建议这样安排：

1. 30 分钟：读 `docker-compose.yml`、`api/app/main.py`、`api/app/controllers/router.py`。
2. 40 分钟：读登录链路，画出 token 从登录到请求头的流动。
3. 50 分钟：读模型配置链路，搞清楚默认 chat/embedding 如何被取出。
4. 40 分钟：读 `web/src/App.tsx` 和 `web/src/api/client.ts`，把每个页面和后端 controller 对上。

第一天不要追求读完业务细节。目标是：以后看到任意 bug 或功能，都知道应该从哪个入口往下追。

## 14. 第 4 章精读笔记：知识库 RAG 主链路

### 先画出真实数据流

```text
KnowledgeDetailPage
  -> POST /api/documents/upload (multipart file + kb_id)
  -> DocumentService.upload
  -> 对象存储保存原始文件；PostgreSQL 新建 documents(status=pending)
  -> parse_document_task.delay(document_id)
  -> Redis broker -> Celery parse 队列 -> tasks/parse.py
  -> 读取原文件 -> 解析纯文本 -> 父子分块 -> 子块 embedding
  -> bulk_index 写入 Elasticsearch comet_chunks
  -> documents.status=done

页面每 3 秒查询 documents 列表，直到 pending/parsing 消失。

聊天侧：ChatService -> knowledge_search 工具 -> hybrid_search
  -> query embedding + ES knn + ES BM25 -> 0.6/0.4 融合
  -> 可选 rerank -> 返回父块内容 -> 记录 citations -> 给模型作答
```

### 1. 前端的职责：发起、展示和轮询，不做 RAG

- `web/src/pages/KnowledgeBasePage.tsx` 管理知识库卡片、创建、编辑、删除和 `chat_enabled` 开关；真正的 API 文件是 `web/src/api/knowledgeBases.ts`，不是旧阅读清单中的 `knowledge.ts`。
- `web/src/pages/KnowledgeDetailPage.tsx` 从路由参数取得 `kbId`。文件上传调用 `documentApi.upload(file, kbId)`，网页导入调用 `documentApi.importUrl(url, kbId)`，两者都把资料放进当前知识库。
- 列表中只要存在 `pending` 或 `parsing` 文档，页面会每 3 秒重新请求 `/documents?kb_id=...`；这就是用户能看见解析进度的原因。它不轮询单个 Celery task，也没有 WebSocket 推送。
- 页面内搜索调用 `/documents/search`，结果是 RAG 检索结果，不是 PostgreSQL 的文件名模糊查询。预览则读取原始文件并临时解析，和 ES 检索是两条独立路径。

### 2. HTTP 与业务：先建立可追踪的文档记录，再交给异步任务

- `document_controller.py` 的上传接口先把 `UploadFile` 读成字节，交给 `DocumentService.upload`；URL 导入则交给 `DocumentService.import_url`。两条路径均有 `get_current_user`，因此用户 ID 从 token 进入所有后续调用。
- `DocumentService._resolve_kb_id` 会验证指定的知识库属于当前用户；未指定时创建或使用该用户的默认知识库。`Document` 随后保存 `user_id`、`kb_id`、`file_key`、来源、状态、进度与错误信息。
- 上传限制为 50 MB，支持 PDF、DOCX、Markdown、TXT、HTML。原始字节先保存到对象存储，`documents` 表保存元数据，初始状态为 `pending`，然后调用 `parse_document_task.delay(...)` 投递任务。HTTP 响应此时已经返回，不能把“上传成功”理解为“已可检索”。
- `file_controller.py` 不参与上传或解析；它仅在本地存储模式下通过 `/api/files/{file_key}` 返回原始文件，并要求 `file_key` 必须以当前用户 ID 开头。

### 3. 数据如何分工

- PostgreSQL `knowledge_bases`：知识库容器、`user_id`、`chat_enabled`。默认知识库会自动创建，且默认开启对话检索。
- PostgreSQL `documents`：文件元数据与任务状态，不保存完整解析文本或向量。
- 对象存储：原始文件；`file_key` 以用户 ID 为前缀。
- Elasticsearch `comet_chunks`：可检索内容。每条记录带 `user_id`、`kb_id`、`source_id`、`doc_name`、`chunk_type`、`content`、`tags` 和 `vector`。

任何读取或写入都围绕 `user_id` 过滤；这是 RAG 多用户隔离的关键，而不是只靠前端隐藏知识库。

### 4. Celery 解析任务：状态机和写索引顺序

`celery_app.py` 把 `app.tasks.parse.*` 路由到 `parse` 队列。`tasks/parse.py` 的同步 Celery 入口用 `asyncio.run` 运行异步内部流程，并为每个任务创建独立数据库引擎、在结束时关闭 ES 客户端，避免复用已关闭事件循环的连接。

任务状态依次为：`pending -> parsing(0.1) -> parsing(0.3) -> parsing(0.8) -> done(1.0)`；任何异常都会写为 `failed`，并把异常文本截断到 500 字符写进 `error_msg`。前端据此显示进度和“重试”按钮。

具体顺序是：

1. 从对象存储读取 `file_key` 对应字节。
2. `parse_document` 按扩展名转换成纯文本：PDF 使用 PyMuPDF，DOCX 使用 python-docx，Markdown 先转 HTML 再取文本，HTML 去除 script/style，TXT 自动识别编码。
3. `chunk_parent_child` 按中英文句界切分。父块目标约 1024 tokens，只作为更完整的上下文；父块内部再生成约 256 tokens 的子块，子块保留约 10% 的句子重叠。
4. 从当前用户的默认 embedding 模型配置创建 client，批量为每个子块生成向量。父块没有向量，子块才是向量召回目标。
5. 先按 `user_id + source_id` 删除旧块，再批量写入新块并 refresh 索引。这使“重试解析”保持幂等，不会遗留旧版本块。
6. 若存在 chat 模型，额外自动分类并把标签写入 PG 关联表和同一来源的 ES 块；分类失败不影响文档完成解析。

### 5. ES 映射与检索

`es_index.py` 在应用启动时保证 `comet_chunks` 存在。`content` 使用 IK 分词（写入 `ik_max_word`，查询 `ik_smart`）；`vector` 是余弦相似度的 `dense_vector`，维度由 `settings.embedding_dims` 决定。更换 embedding 模型时，向量维度必须匹配该 mapping，否则写入或查询会失败。

`hybrid_search` 的顺序是：

1. 强制构造 `user_id` 过滤，并叠加来源类型、标签和可选 `kb_id` 范围。
2. 用 embedding client 对 query 生成向量，向 ES 发起 kNN 召回（默认取 20 个候选）。
3. 对同一过滤范围执行 `match(content=query)`，得到 BM25 候选。
4. 分别对两组分数做 min-max 归一化，以 `0.6 * vector + 0.4 * BM25` 融合排序。
5. 如果用户配置了 rerank 模型，则对候选再排；rerank 失败会退回融合排序。
6. 命中的子块会根据 `parent_id` 再取父块正文，最终把更大的上下文、来源 ID、文件名和分数返回。

因此“ES 同时承担 BM25 和向量检索”是准确的：同一个 `comet_chunks` 索引保存文本与向量，但两种召回独立执行、再融合。

### 6. 检索如何变成对话引用

聊天服务计算工具作用域时，优先使用技能绑定的知识库；没有绑定时读取该用户所有 `chat_enabled=True` 的知识库 ID。知识库搜索工具把这组 ID 传入 `hybrid_search`，即转换成 ES 的 `terms(kb_id)` 过滤。空列表代表当前用户没有启用任何库，检索应返回空结果。

工具会把命中父块文本返回给 Agent，同时按 `source_id` 去重收集 `{source_id, source_type, doc_name, score}` 到 `citations`。后续模型回答和前端引用展示依赖的就是这份引用元数据，而不是让模型自己猜文件来源。

### 7. 最重要的失败边界

- 没有默认 embedding 模型：解析在生成子块向量时失败；搜索在生成 query 向量时失败。两者都会由 `get_client_for_type(..., "embedding")` 抛出“未配置 Embedding 模型”。
- Celery worker 未运行或未监听 `parse` 队列：文档会长期停在 `pending`，HTTP 接口本身仍可能返回上传成功。
- 对象存储读取、文档解析、ES 写入或 embedding 调用异常：任务写入 `failed`，用户可以调用 `/documents/{id}/retry` 重新入队。
- 用户删除文档或知识库：服务层同时清理 ES 块、对象存储原文件和 PG 记录。因为跨 PG、ES、存储没有分布式事务，清理失败只会记录 warning，可能遗留孤儿数据，后续应关注日志与补偿机制。
