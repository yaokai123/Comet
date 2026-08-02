# 知识库 RAG 深读：从上传到对话引用

本文只解释当前项目的文档知识库链路。图片知识库走相近但独立的 `tasks/image.py` 路径，不与本文混在一起。

## 1. 一次上传在代码中的调用栈

以用户在知识库详情页上传 `report.pdf` 为例：

```text
KnowledgeDetailPage.DocTab.onUpload(file)
  -> documentApi.upload(file, kbId)
  -> POST /api/documents/upload
  -> document_controller.upload_document
  -> DocumentService.upload(user.id, file.filename, bytes, kb_id)
  -> StorageBackend.save(file_key, bytes)
  -> DocumentRepository.create(Document(... status=pending))
  -> parse_document_task.delay(document_id)
  -> HTTP 返回 DocumentItem
```

前端不会等待 `parse_document_task` 完成。`beforeUpload` 的回调返回 `false`，因此 Ant Design Upload 不会自行把文件提交到默认地址；页面完全用 `documentApi.upload` 控制 multipart 请求、提示和错误。

### 1.1 `KnowledgeBasePage.tsx`：管理检索范围

文件：[web/src/pages/KnowledgeBasePage.tsx](/D:/Comet-main/web/src/pages/KnowledgeBasePage.tsx)

这个页面不是上传入口，而是知识库容器的管理界面。初次渲染调用 Zustand store 的 `refresh`，后者请求 `GET /knowledge-bases` 并缓存列表。卡片点击只做路由跳转到 `/knowledge-bases/:id`。

`Switch` 触发 `PUT /knowledge-bases/:id/chat-enabled`。这个值写入 PostgreSQL `knowledge_bases.chat_enabled`，之后对话服务会把所有开启的库 ID 传给检索器。它改变的是服务端检索范围，不能把它理解成仅隐藏或显示一个前端元素。

创建和编辑使用同一个 Modal。前端校验限制名称必填，后端 Schema 再限制名称 1--128 字符、描述最多 512、图标最多 32、颜色最多 16。默认库在 UI 中不显示删除操作，后端也会再次拒绝删除，避免只靠 UI 防护。

### 1.2 `knowledgeBases.ts` 与 Store：请求契约和缓存

文件：[web/src/api/knowledgeBases.ts](/D:/Comet-main/web/src/api/knowledgeBases.ts)、[knowledgeBaseStore.ts](/D:/Comet-main/web/src/stores/knowledgeBaseStore.ts)

前者只定义 HTTP 调用和 TypeScript 数据形状；统一响应解包由全局 axios client 完成，所以页面拿到的是 `{ code, message, data }` 的结果。`KnowledgeBase` 中的 `doc_count`、`image_count` 是后端实时统计值，不是页面累加出来的。

Store 的 `refresh` 不吞异常：它只在 `finally` 中关闭 `loading`。因此调用方负责展示失败提示。`ensureLoaded` 防止同一会话内重复加载；`defaultKb` 仅从已加载列表中找 `is_default`，不会自行发请求。

### 1.3 `KnowledgeDetailPage.tsx`：上传、轮询、检索、预览

文件：[web/src/pages/KnowledgeDetailPage.tsx](/D:/Comet-main/web/src/pages/KnowledgeDetailPage.tsx)

`DocTab` 维护四类不同状态：文档列表 `list`、上传/导入状态、搜索命中 `hits`、预览内容。`hits === null` 的语义是“浏览模式”；一旦完成页面内搜索，列表仍在内存中但 UI 改为显示检索命中。清空搜索把 `hits` 设回 `null`，再重新请求列表。

轮询条件是列表中是否至少有一条 `pending` 或 `parsing`。满足条件时建立一个 3 秒 interval；切换到搜索模式、所有文档终态化或组件卸载时清除 interval。也就是说，系统没有通过 Celery result backend 直接显示任务状态，而是把 `documents` 表作为面向前端的任务状态视图。

`preview` 与检索不同。预览请求 `/documents/{id}/preview`，服务端重新从原始文件取字节并解析，Markdown/TXT 尽量保留原貌，其他类型提取纯文本；最长只回传 80,000 字符并标记 `truncated`。因此预览能在 ES 未写入前工作，但不能证明该文档已经完成索引。

## 2. HTTP 层：只转换传输数据并绑定当前用户

### 2.1 `knowledge_base_controller.py`

文件：[knowledge_base_controller.py](/D:/Comet-main/api/app/controllers/knowledge_base_controller.py)

路由前缀为 `/knowledge-bases`，在总路由下最终成为 `/api/knowledge-bases`。每个接口都注入 `get_current_user` 和异步 PostgreSQL session。Controller 不直接操作 ORM：它把 `user.id` 和路径/请求体参数交给 `KnowledgeBaseService`，再用 `success` 包装返回。

这层的安全意义是：客户端虽然可以伪造任意 `kb_id`，但 service/repository 的读取都是 `(kb_id, user_id)` 联合条件，其他用户的库会以“不存在”返回。

### 2.2 `document_controller.py`

文件：[document_controller.py](/D:/Comet-main/api/app/controllers/document_controller.py)

上传接口接收 `UploadFile` 与 `Form` 中可选的 UUID `kb_id`。这里的 `await file.read()` 会把整份上传内容读入 API 进程内存，虽然业务限制最大 50 MB，但并非流式直传对象存储。随后 service 验证类型和大小。

URL 导入接收 JSON `UrlImportRequest`；抓网页发生在 service 内、且在 HTTP 请求期间完成。只有抓取成功后才创建 pending 文档并投递异步解析，因此“网页抓取”本身不是 Celery 任务。

`GET /documents` 是状态列表和分页查询，`GET /status` 是单记录的轻量状态查询，当前前端实际使用前者轮询。`POST /search` 的 query 最小长度 1、`top_k` 在 1--20；它调用的不是数据库查询而是 `hybrid_search`。

### 2.3 `file_controller.py`

文件：[file_controller.py](/D:/Comet-main/api/app/controllers/file_controller.py)

它仅服务本地存储时的原始文件读取。请求必须带 token；`file_key` 必须以 `${user.id}/` 开头，文件存在后才返回字节。OSS 模式不走此接口，而由 `OssStorage.get_url` 生成带时限签名的 URL。

## 3. 业务与持久化：文档、知识库、对象存储各存什么

### 3.1 ORM 模型

文件：[knowledge_base_model.py](/D:/Comet-main/api/app/models/knowledge_base_model.py)、[document_model.py](/D:/Comet-main/api/app/models/document_model.py)

`KnowledgeBase` 是资料容器。`user_id` 指向 users，`is_default` 标识不可删除的默认库，`chat_enabled` 决定该库是否属于默认对话检索范围。文档删除库时使用外键 `CASCADE` 删除 PG 记录。

`Document` 不存解析正文和向量，仅存 `file_key`、来源、解析状态与统计信息。状态常量为 `pending`、`parsing`、`done`、`failed`；`progress` 是浮点数，当前任务按 0.1/0.3/0.8/1.0 写入；`chunk_num` 只统计子块数，因为子块才是实际召回单元。

### 3.2 `DocumentService`

文件：[document_service.py](/D:/Comet-main/api/app/services/document_service.py)

`_resolve_kb_id` 有两个分支：传入库 ID 时用当前 `user_id` 查询确认归属；未传入时调用 `ensure_default`，保证文档永远归属一个库。随后 `build_file_key` 生成 `{user_id}/documents/{document_id}.{ext}`，这同时提供了存储命名空间和文件读取的归属检查依据。

`upload` 的顺序是“先存储，后建 PG 记录，后投递任务”。因此若 PG 写入失败，原文件可能成为孤儿对象；若投递失败，PG 记录会是 pending。这不是原子事务，系统目前依赖日志和人工/后续补偿处理。

`retry` 只重置状态、进度与错误，再次投递同一个 document ID。解析任务写 ES 前会删除同 `user_id + source_id` 的旧块，因此重试不会重复检索结果。

`delete` 先删 ES 块，再尝试删原始文件，最后删 PG 记录。存储删除异常只记 warning，随后仍删数据库记录；这保证用户列表不被卡住，但可能遗留对象存储文件。

`move_to_kb` 先更新 PG 中的 `kb_id`，再用 ES `update_by_query` 同步所有同源块。ES 回写失败仅 warning，因此短时间内“文档列表所属库”和“聊天检索库”可能不一致。

### 3.3 Repository 与任务内部读取

文件：[document_repository.py](/D:/Comet-main/api/app/repositories/document_repository.py)

所有用户发起的读取都带 `Document.user_id == user_id`。`list_paged` 先构建同一 user 的查询，再可选按库和标签 join 过滤，最后独立 count、按创建时间倒序分页。

`get_by_id` 不带 user 条件，只被 Celery 任务使用。任务参数不是用户可直接传入的 HTTP 参数，而是由上传/retry 的 service 生成；任务读到文档后把 `doc.user_id` 带入 embedding 配置、ES 写入与删除。这个设计减少了任务参数，但意味着任何未来新增的任务投递入口都必须只允许经过受鉴权 service。

### 3.4 存储抽象

文件：[core/storage/base.py](/D:/Comet-main/api/app/core/storage/base.py)、[factory.py](/D:/Comet-main/api/app/core/storage/factory.py)、[local_storage.py](/D:/Comet-main/api/app/core/storage/local_storage.py)、[oss_storage.py](/D:/Comet-main/api/app/core/storage/oss_storage.py)

业务层只依赖 `save/get/delete/exists/get_url` 五个方法。Factory 按 `settings.storage_backend` 缓存一个 local 或 OSS 实例。两种实现都用 `asyncio.to_thread` 包装底层阻塞 I/O，避免卡住 FastAPI/Celery 的事件循环。local 模式把 key 拼到 `storage_dir` 下，OSS 模式把 key 作为 bucket object key；调用 service 不需要知道实际后端。

## 4. 异步任务：如何把原文件变成 ES 块

### 4.1 任务路由和执行环境

文件：[celery_app.py](/D:/Comet-main/api/app/celery_app.py)、[tasks/parse.py](/D:/Comet-main/api/app/tasks/parse.py)

Celery broker 是 Redis DB 1，结果后端是 Redis DB 2。`app.tasks.parse.*` 路由到 `parse` 队列，因此 worker 必须订阅该队列。`parse_document_task` 是普通同步函数；它用 `asyncio.run(_run(...))` 承接异步数据库、ES 与 HTTP embedding 调用。

每个任务创建一个独立 async engine，结束时 dispose，并关闭本事件循环创建的 ES client。这一点很重要：API 进程的全局 async client 不能安全地跨 Celery task 的 event loop 复用。

### 4.2 解析、分块、向量化、索引

`_parse` 先把状态设为 `parsing/0.1`。它读取对象存储、调用 `parse_document`，若纯文本为空直接失败；解析成功写为 `0.3`。解析器按文件类型选择实现：PDF 用 PyMuPDF、DOCX 用 python-docx、Markdown 转 HTML 后由 BeautifulSoup 取文本、HTML 删除 script/style、TXT 用 chardet 推断编码。

`chunk_parent_child` 先按句号、问号、叹号和换行切句。它用 `cl100k_base` 计 token，而不是按字符数。句子合并成约 1024 token 父块；每个父块再合并为约 256 token 子块，子块保留末尾约 10% 句子形成 overlap。过长单句不会再切，而是单独成为块，因此单条异常长句可超过目标 token 数。

任务通过 `get_client_for_type(session, doc.user_id, "embedding")` 取得当前用户的默认 embedding 配置。没有配置时抛业务错误，整个文档进入 failed。每个父块写一条无 vector 的 ES 文档；其 children 批量调用 `embed` 后各写一条带 vector 和 `parent_id` 的 ES 文档。任务把构造完成的列表一次 bulk 写入，并在写前删除旧的同源块。

自动标签是可选步骤：没有 chat 模型时直接跳过；有模型时从全文和该用户已有标签中分类，将结果同时写入 PG 的文档标签关联表与 ES 的 `tags` 字段。`classify_content` 会自行捕获模型调用和 JSON 解析异常并返回空标签，因此分类模型失败不会阻断解析；但标签关联表或 ES 标签回写本身若异常，仍会冒泡到任务总处理器并使文档进入 failed。

发生任何异常时，任务将状态设为 failed，并保存最多 500 字符的异常内容。任务函数仍返回 document ID，而不是把异常重新抛给 Celery；因此用户可见的失败来源应以 `documents.error_msg` 和任务日志为准。

## 5. ES：一套索引服务两种召回

### 5.1 映射与写入

文件：[es_index.py](/D:/Comet-main/api/app/core/rag/es_index.py)、[es_store.py](/D:/Comet-main/api/app/core/rag/es_store.py)

索引固定为 `comet_chunks`。`content` 是 IK 分词文本字段，写入使用 `ik_max_word`、搜索使用 `ik_smart`；`vector` 是 `dense_vector`，余弦相似度，维度等于 `settings.embedding_dims`。当前默认是 1024，必须与 embedding 服务实际返回长度一致。

`ensure_index` 在应用启动运行：不存在则创建；旧索引缺 `kb_id` 时补 mapping；若旧字段类型不是 keyword，则通过临时索引 reindex 重建。重建不是无影响操作，在单用户小数据量设定下可接受，但运行时应避免与大量写入并发。

`build_chunk_doc` 为每个块生成随机 `chunk_id`，写入 user、知识库、来源、父子关系、正文、标签、向量与时间。`delete_by_source` 使用 `user_id + source_id` 删除，不能只按 source ID 删除；`update_kb_by_source` 与 `update_tags_by_source` 也是同样的范围。

### 5.2 `hybrid_search`

文件：[search.py](/D:/Comet-main/api/app/core/rag/search.py)

函数先构建所有召回共用的 `base_filter`：`user_id` 必须相等，文档仅允许 child 块，图片仅允许 image_desc 块；随后可加 `kb_ids`、tags、source_type。`kb_ids=[]` 会形成 ES 的空 `terms` 过滤，因此正确地返回空结果。

第一路召回：用同一用户 embedding client 对 query 调 `embed_one`，再发 ES kNN，默认取 20 条，候选池为 100。第二路召回：同样过滤条件下 `match(content=query)`，由 ES 的 BM25 完成词法相关性排序。

两路得分不能直接相加，因为量纲不同，所以代码先各自 min-max 归一化；若某一路全部相等，全部给 1。最终分数为 60% 向量 + 40% BM25。可选 rerank 模型拿候选正文重新排序；异常时保留融合顺序。

最终命中的 child 块不会直接交给模型。`_resolve_parent_content` 根据 `parent_id` 且再次加 `user_id` 查父块，将父块正文返回；父块找不到才退回 child 文本。这是 small-to-big 检索：小块负责精确命中，大块负责提供可读上下文。

## 6. 聊天如何获得引用

文件：[chat_service.py](/D:/Comet-main/api/app/services/chat_service.py)、[knowledge.py](/D:/Comet-main/api/app/core/agent/tools/builtin/knowledge.py)

`ChatService._tool_scope` 先计算本轮工具开关，再计算知识库范围。若当前 skill 显式绑定 `kb_id`，只查这一库；否则查询当前用户所有 `chat_enabled=True` 的库。这使“对话页临时关闭知识库搜索”“技能绑定单库”“常规对话搜索启用的多个库”可以共存。

知识库工具以该范围调用 `hybrid_search(session, user_id, query, top_k=5, kb_ids=kb_ids)`。返回命中时，它将正文拼给 Agent，同时按 `source_id` 去重写入 `citations`：来源 ID、来源类型、文档名与分数都会保存。模型得到的是检索文本；前端需要展示来源时依赖的是 citations 元数据。

## 7. 建议你实际断点追一次

1. 在 `KnowledgeDetailPage.onUpload` 看 `kbId` 和 File 如何进入 FormData。
2. 在 `DocumentService.upload` 看 file_key、pending 记录、`delay` 三件事按什么顺序发生。
3. 在 `tasks/parse._parse` 看同一 document 从 0.1 到 1.0 的每次保存。
4. 在 `hybrid_search` 对比 kNN 与 BM25 的 `base_filter`，确认它们的用户和知识库范围完全一致。
5. 在知识库工具中观察命中如何写入 `citations`，再追聊天响应中的引用展示。

做到这五步，你就不只是知道“RAG 的概念”，而是能定位本项目中上传卡住、向量失败、搜索不准、跨库命中或引用缺失各自应从哪里排查。
