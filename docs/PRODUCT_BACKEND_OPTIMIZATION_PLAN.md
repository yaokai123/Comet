# 产品与后端优化方案

审查日期：2026-07-13。范围覆盖认证、启动与部署、知识库 RAG、记忆图谱、聊天上下文与现有测试。除已运行的前端视觉回归外，以下后端结论均为静态代码审查，实施前应以集成测试复核。

## 当前结论

前端工作台主路径已有桌面和移动视觉回归：登录、Dashboard、知识库、聊天、记忆、图谱共 12 个场景。后端的领域分层、用户 ID 过滤及 Neo4j 查询参数化整体清晰，但在安全默认值、异步任务幂等、跨存储一致性和测试覆盖上仍未达到生产级闭环。

## 优先级问题

### P0：上线前必须处理

1. 不安全默认配置可直接启动。
   - `app.config` 提供开发 JWT 密钥、Fernet 密钥、数据库和 Neo4j 密码默认值；若部署遗漏环境变量，令牌签发与模型密钥加密均可被预测。
   - 处理：生产环境强制校验密钥长度、Fernet 格式、数据库密码与 `app_debug=False`；发现开发默认值立即拒绝启动。将 `.env.example` 明确标为不可生产使用。

2. 启动初始化失败后仍继续对外服务。
   - `main.py` 对 Alembic、ES 索引、Neo4j schema 初始化均捕获异常并继续启动。迁移未完成时 API 可能对旧表结构写入；索引未就绪时 RAG 会产生“上传成功但不可检索”的假成功。
   - 处理：生产环境对 PostgreSQL 迁移失败 fail-fast；ES/Neo4j 使用 readiness 状态并让依赖接口返回 `503`，不要静默降级为业务空数据。Dashboard 显示“服务待恢复”。

3. URL 导入的 SSRF 校验未覆盖重定向目标。
   - `web_crawler.py` 仅检查初始 URL，随后 `httpx` 允许 `follow_redirects=True`。公开域名可重定向到内网或云元数据地址。
   - 处理：禁用自动重定向，逐跳解析并校验协议、DNS 结果和最终连接 IP；禁止私网、loopback、link-local、CGNAT 和云元数据地址；限制响应大小与 Content-Type。

### P1：本迭代应处理

1. 认证缺少账户状态与会话撤销。
   - `get_current_user` 和刷新令牌流程仅验证用户存在，不检查账户状态；刷新令牌是无状态 JWT，登出、改密或疑似泄露后旧 refresh token 仍可使用至过期。
   - 处理：增加 `is_active`、`token_version` 或 refresh-session 表；改密、登出全部设备时递增版本或撤销 session；access token 缩短至 15-30 分钟并轮换 refresh token。

2. 上传先全量读入内存，再检查 50MB 限制。
   - `document_controller.py` 的 `await file.read()` 发生在 `DocumentService.upload` 的大小校验前。并发大文件会耗尽 API 进程内存。
   - 处理：按块流式写入临时对象存储并累计大小；超限立即中止和清理；在 Nginx/Uvicorn 设置请求体上限；为单用户设置上传并发及速率限制。

3. PostgreSQL 与 Elasticsearch 更新允许静默分叉。
   - 文档移动先写 PG，再尝试回写 ES；ES 失败只记录 warning 并仍返回成功。删除同样无法做到跨存储原子性。
   - 处理：引入 outbox / index-job 表，记录 `pending/running/succeeded/failed`；API 返回同步状态，worker 可重试且幂等；定时执行 PG 与 ES 对账、自动修复并告警。

4. 解析与记忆任务没有显式幂等锁或退避重试。
   - 文档 retry 可重复投递；任务会删除并重建同一 source 的 ES chunk。记忆任务也是按 ID 直接执行。并发 retry、删除和 worker 重启可能造成重复计算、状态倒退或索引闪断。
   - 处理：为 `document_id`/`memory_id` 使用 Redis 分布式锁和 task generation/version；状态转移采用 compare-and-set；Celery 配置有限次数指数退避、死信/失败告警；删除操作先标记 tombstone，再由任务检查 generation。

### P2：可靠性与性能优化

1. 聊天主动召回缓存位于 API 进程内存。
   - `chat_service.py` 的 `_recall_cache` 在多 worker 或重启后不一致，缓存淘汰也不是 LRU，难以观测命中率。
   - 处理：迁移到 Redis，设置用户级 TTL、版本号和最大长度；记录命中率、首字延迟和召回失败率。

2. 记忆修正跨 PostgreSQL 与 Neo4j 的补偿策略不一致。
   - 部分操作允许 Neo4j 成功但 PG 审计记录失败后继续，其他删除操作则反向阻断。审计完整性语义不统一。
   - 处理：定义统一原则：核心图变更与审计写入使用 outbox/saga；所有 API 返回 `applied` 与 `audit_pending` 状态；对账任务补写审计。

3. 后端自动化覆盖不足。
   - 当前仅发现两项记忆相关单测。没有认证撤销、上传限流、SSRF redirect、解析重试、ES/PG 对账和多用户隔离的集成测试。

## 分阶段实施

### 阶段 0：建立安全与可观测基线（2-3 天）

1. 为生产配置增加 fail-fast 校验与启动 readiness。
2. 收紧 CORS、上传大小、结构化错误日志和 `/health/ready`。
3. 增加 CI：Ruff、Pytest、前端构建和现有 12 个视觉回归。

验收：缺失生产密钥、迁移失败、ES/Neo4j 不可用时，系统不会伪装成可用；CI 每次提交均输出明确失败原因。

### 阶段 1：身份和文件入口加固（3-5 天）

1. 引入 refresh session / token version，补齐账户禁用、改密和注销语义。
2. 将文档与图片上传改为流式、限额、可清理的暂存流程。
3. 重写 URL 导入为逐跳 SSRF 校验，并限制 MIME、字节数和下载时长。

验收：旧 refresh token 在注销或改密后失效；大文件并发不导致 API 内存异常；私网和重定向到私网的 URL 被拒绝。

### 阶段 2：RAG 与记忆任务可靠性（5-8 天）

1. 建立 document/index outbox、任务 generation 和 Redis 幂等锁。
2. 规范状态机：`pending -> parsing -> indexed -> done/failed/deleted`，每个状态带更新时间、任务 ID 和失败原因。
3. 建立 PG、ES、Neo4j 的对账与修复任务，并在 Dashboard 展示积压与失败数。

验收：重复投递不产生重复 chunk；删除与解析并发后不会重新出现已删除文档；ES 故障恢复后可自动补索引。

### 阶段 3：聊天、记忆与产品可运营性（3-5 天）

1. 将召回缓存迁移 Redis，增加 cache key 版本和指标。
2. 统一记忆图变更与审计的补偿策略。
3. 增加任务中心：重试、取消、失败原因、最近成功时间和管理员修复入口。

验收：多 API 实例下召回一致；记忆修正均可审计；运营人员无需查容器日志即可定位失败任务。

### 阶段 4：测试闭环与发布门禁（持续）

1. Pytest 单测：token、URL 校验、状态机、幂等键、查询用户过滤。
2. Docker 集成测试：PostgreSQL、Redis、Elasticsearch、Neo4j 的上传到检索、记忆到图谱闭环。
3. Playwright：保留现有 12 个视觉场景，新增上传、失败重试、移动端抽屉和图谱空/满状态。
4. 发布前执行数据迁移演练、备份恢复演练和 24 小时队列积压观察。

验收：关键链路同时具备单测、集成测试和用户界面回归；发布失败可回滚，数据恢复步骤可复现。

## 推荐实施顺序

先完成阶段 0 和阶段 1，再做阶段 2。它们优先消除密钥泄露、SSRF、内存耗尽和“数据已写入但不可检索”的风险。阶段 3、4 可与功能迭代并行推进，但不得替代前述数据可靠性工作。
