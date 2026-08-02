# Agent 深度研究与定时任务工作流分析

本文对应以下代码路径，按一次任务从 UI 到最终报告的真实调用顺序说明：

- `web/src/pages/ResearchPage.tsx`
- `web/src/pages/AgentTaskPage.tsx`
- `api/app/controllers/research_controller.py`
- `api/app/controllers/agent_task_controller.py`
- `api/app/services/research_service.py`
- `api/app/services/agent_task_service.py`
- `api/app/tasks/agent_task.py`
- `api/app/core/agent/research/`
- `api/app/core/agent/loop/`
- `api/app/models/research_report_model.py`
- `api/app/models/agent_task_model.py`
- `api/app/models/loop_model.py`

## 1. 总体定位

这里的 Agent 不是一个“输入问题、调用一次模型、返回文本”的单回合聊天 Agent，而是两套复用同一研究引擎的工作流：

1. **深度研究（交互式）**：用户在浏览器发起主题，页面以 SSE 实时显示规划、搜索、写作和质量复核过程；浏览器断开后，后台仍继续运行。
2. **定时/主动任务（无人值守）**：用户保存自然语言研究指令和时间规则，Celery 定时扫描到期任务，调用同一个研究引擎，并把产物保存为研究报告；可选择发送消息通知。

两条路径的共同核心为 `run_research()`：

```text
研究主题
  -> 规划（章节 + 多视角检索词）
  -> 联网 / 知识库 / MCP 三路检索
  -> 逐源提炼为带引用号的 Learning
  -> 反思信息缺口并补搜
  -> 整理论点与证据分配
  -> 分章节流式写作、汇总
  -> Verifier Loop 评分
  -> Patch 补搜 或章节重写
  -> ResearchReport
```

## 2. 三类持久化数据及职责

### 2.1 `ResearchReport`：一次研究的业务产物

定义在 `api/app/models/research_report_model.py`。每一次交互研究或定时任务运行都创建一行：

- `topic`：原始研究主题或定时任务的 `instruction`。
- `status`：`pending -> planning/searching/writing/summarizing -> done`，任一步异常为 `failed`。
- `title`、`report_md`：最终报告标题与 Markdown。
- `outline`：规划事件中得到的标题、章节和查询词快照。
- `sources`：前端展示用的精简来源列表。
- `task_id`：若由定时任务产生，关联 `AgentTask.id`；因此任务历史无需独立“运行记录”表。

`ResearchReport` 是用户真正可以查看、导出、分享、删除或存入知识库的对象。Loop 的评分记录并不替代它。

### 2.2 `AgentTask`：计划和调度状态

定义在 `api/app/models/agent_task_model.py`，保存的是“未来如何运行”，不是报告正文：

- `name` / `instruction`：任务名与未来每次要研究的主题。
- `kb_ids`：研究时限制检索的知识库范围。
- `trigger_type`：`daily`、`weekly` 或 `interval`。
- `trigger_time`、`trigger_weekday`、`trigger_interval_hours`：不同触发器的参数。
- `enabled`、`next_run_at`：是否参与到期扫描，以及下一次应运行的时间。
- `last_run_at`、`last_status`：任务卡片显示的运行摘要。
- `notify_enabled`：报告完成后是否尝试推送摘要。

### 2.3 `LoopRun` / `LoopIteration`：质量回路审计

定义在 `api/app/models/loop_model.py`：

- `LoopRun` 是一份报告的一次“生成后复核”总记录，保存阈值、最大轮数、生成模型、验证模型、验证类型、最终分数和状态。
- `LoopIteration` 是某一轮验证/决策的审计行，保存分维分数、结构化反馈、修复动作、耗时和 artifact 摘要。

这里不会保存每一版完整报告，只保存标题、正文长度、正文哈希、来源数和标题列表等摘要。这既让失败排查有证据，又避免将多轮 Markdown 无限复制到 PostgreSQL。

## 3. 前端入口：用户实际看见什么

### 3.1 `ResearchPage.tsx`：交互式研究控制台

`web/src/pages/ResearchPage.tsx` 维护主题、当前报告、计划、来源、活动步骤、流式 Markdown 和 `loopDetail` 等状态。

点击开始时，`startResearch()` 会先取消旧的 `AbortController`，清空当前展示，再调用 `streamResearch(topic, handlers, signal)`。它不是使用浏览器的 `EventSource`，而是用 `fetch()` 发 `POST /api/research/stream`，手工解析 SSE 文本块；这样 POST 请求也可以携带主题和 `kb_ids`。

`buildHandlers()` 把协议事件映射为 UI 状态：

| SSE 事件 | UI 行为 |
| --- | --- |
| `meta` | 立即创建临时历史项并保存 `report_id`。 |
| `status` | 显示规划、搜索、提炼、反思、整理、写作或汇总阶段。 |
| `plan` / `sources` / `progress` | 展示提纲、来源和搜索/抓取/MCP 活动流。 |
| `section_start` / `token` | 追加章节标题和流式正文。 |
| `report` / `done` | 固化正文、刷新报告列表，并随后拉取 Loop 详情。 |
| `loop_*` | 把“开始复核、评分、补搜/重写、完成”转成可视的质量步骤。 |

打开历史报告时，若状态仍在运行，页面调用 `GET /research/{id}/events` 重订阅；若已结束则直接显示 PostgreSQL 中保存的 Markdown。完成报告会额外调用 `GET /research/{id}/loop`，有记录才渲染 `QualityCard`。因此质量卡不是凭 SSE 临时计算，而是读取已落库的审计记录。

报告还可以导出 DOCX、生成公开分享、或调用 `saveToKb()` 保存为 `.md` 文档并进入既有的文档解析/RAG 流水线。

### 3.2 `AgentTaskPage.tsx`：定时任务的配置面板

`web/src/pages/AgentTaskPage.tsx` 只管理“何时研究什么”，不直接展示流式执行过程。

- 页面加载调用 `agentTaskApi.list()`，并调用 `markSeen()` 清除定时简报红点。
- 表单支持每日、每周和每隔 N 小时；提交时把 `dayjs` 时间标准化为 `HH:mm`。
- “AI 润色”复用 `/research/optimize-topic`，因此定时任务和手动研究使用相同的提示词优化能力。
- “立即运行”仅发 `POST /agent-tasks/{id}/run` 投递 Celery；成功提示用户稍后去深度研究页查看报告。
- “历史”请求 `/agent-tasks/{id}/runs`，本质是按 `ResearchReport.task_id` 查询报告；完成行可跳转 `/research?report={id}`，并显示 verifier 的 `passed/exceeded/failed` 徽章和最终分数。

## 4. HTTP 层：薄控制器，不承载业务流程

### 4.1 `research_controller.py`

控制器把 HTTP、认证和流式响应接到 `ResearchService`：

- `POST /research/stream`：返回 `StreamingResponse`，`text/event-stream`，并关闭代理缓冲。
- `GET /research/{report_id}/events`：断线后续传或回放完成报告。
- `GET /research`、`GET /research/{id}`、`DELETE /research/{id}`：报告管理。
- `GET /research/{id}/loop`：返回 `LoopRun + LoopIteration` 给质量卡。
- `POST /research/{id}/save-to-kb`：复用文档入库任务。
- `POST /research/optimize-topic`：研究主题/定时指令的 LLM 润色。

控制器没有直接调用 LLM、Redis 或 Celery；用户身份通过依赖注入传入 Service，保证数据读取始终带 user scope。

### 4.2 `agent_task_controller.py`

该控制器提供任务 CRUD、启停、立即执行、运行历史、未读数和已读标记。它只调用 `AgentTaskService`，所以“立即运行”的 HTTP 返回只代表**已投递**，并不代表报告已经完成。

## 5. 交互式研究：SSE 与后台生成如何解耦

`api/app/services/research_service.py` 是交互路径最关键的边界层。

### 5.1 启动前置条件

`stream_research()` 先校验：

1. 主题非空；
2. 用户有默认 chat 模型；
3. 用户有 `websearch` 类型配置。

前置失败时直接发 SSE `error`，不会创建 `ResearchReport`，避免数据库中留下永远无法运行的 `pending` 记录。

### 5.2 创建报告后，不把计算绑定在 HTTP 连接上

校验通过后，Service 用独立 `SessionLocal` 创建 `ResearchReport(status=pending)`，先发送 `meta(report_id)`；随后：

```text
HTTP SSE 连接
  -> Redis Pub/Sub 订阅者（只转发）

asyncio.create_task(_run_research_bg(...))
  -> 独立数据库 session 消费 run_research() 事件
  -> Redis 发布事件 + 写 Redis 续传缓冲
  -> PostgreSQL 最终落报告
```

Redis 的 turn lock 让同一 `report_id` 只会启动一个后台协程。`_BG_TASKS` 集合保留 task 引用，避免 `create_task()` 后被垃圾回收。

### 5.3 断线续传

后台任务持续维护 Redis stream buffer：当前 phase、计划、来源、最近 40 条活动、最后 6000 字符正文和全局 token 序号。每 8 个 token 刷新一次。

`resume_events()` 的行为：

- 若 Redis 显示仍在生成：先订阅频道，再二次读取 buffer 防止订阅窗口丢事件，发送 `resume` 快照，然后用 token 序号过滤已包含在快照中的 token。
- 若报告已 `done`：从 PostgreSQL 重放 `report + done`。
- 若报告已失败：发送 `error`；其部分文本仍保留在 `ResearchReport.report_md` 供排查。

这使“页面关闭”不等于“研究取消”。但它不是持久队列：交互研究后台任务仍依赖 API 进程存活；进程重启后只剩数据库里的中间状态，不能从中间阶段继续生成。

### 5.4 状态回写与最终落库

`_run_research_bg()` 将引擎事件翻译为 Redis 事件：`status`、`plan`、`sources`、`progress`、章节、token、`report` 及所有 `loop_*`。普通阶段状态通过独立 session 尽力回写；最终 `report` 才由 `_finish()` 设置 `done` 并写完整 Markdown、outline、sources。

任何异常进入 `_fail()`：状态为 `failed`，错误截断为 2000 字符，已生成的 Markdown、outline、sources 尽力保存。这样前端不会把失败任务误显示为成功，也不会完全丢失已生成部分。

## 6. 研究引擎：`core/agent/research/`

### 6.1 中间模型：用显式结构而不是隐式 prompt 文本

`models.py` 定义了阶段间的数据契约：

- `ResearchPlan`：标题、`PlanSection` 和扁平化查询词。
- `Source`：来源编号、类型（`web/kb/mcp`）、标题、正文片段和可选 URL。
- `Learning`：从单一来源提炼出来的一条事实/观点，**强制携带 `source_index`**。
- `CuratedSection`：章节标题、核心论点、应使用的 `Learning` 全局编号。

这条 `Source -> Learning(source_index) -> 写作 [来源 N]` 链是引用对齐的关键：写作者不直接面对大量未结构化网页正文，而是消费已绑定来源号的证据。

### 6.2 规划：`planner.py`

`make_plan()` 用 `plan.jinja2` 让 chat 模型生成标题、章节、每节子问题。随后将所有章节的 `sub_questions` 和兼容的顶层 `queries` 去重、截断为 `research_max_queries`。

若 LLM 调用或 JSON 解析失败，`_fallback_plan()` 返回“综合分析 + 主题本身作为查询”。因此后续链路不会因为规划格式异常而完全中断。

### 6.3 检索：`retriever.py`

引擎 `_retrieve()` 按顺序收集三类来源，任何一类整体失败只记录 warning：

1. **联网来源**：对每个子查询按当前年月补充时效词（已有年份则保留），限并发搜索、遇限流退避重试；跨查询按 URL 去重；限并发抓正文，超时或失败则用搜索摘要兜底；最后按正文长度、权威域名启发式和低质站点降权排序。 
2. **知识库来源**：对每个查询调用 `hybrid_search()`，按文档聚合命中片段为一个 `Source`。它会排除名为“深度研究报告”的知识库，防止“旧研究报告再次成为新研究唯一证据”的自循环。
3. **MCP 来源**：仅当全局开关开启、当前模型支持 function calling、用户配置了 MCP 工具时运行。通过受最大轮数和总超时限制的工具循环，把成功工具结果转成来源。

收集结束后 `assign_indices()` 在全文范围连续编号；反思补搜会从已有来源数加一继续编号，因此引用号不会冲突。

### 6.4 提炼：`distiller.py`

`distill_sources()` 并发处理来源，单源正文最多传入 4000 字符。每个 `_distill_one()` 调用 `distill.jinja2`，要求 LLM 返回 JSON `learnings`；再把文本、来源编号、日期提示、相关度转换为 `Learning`。

失败的单个来源仅返回空列表；汇总时按相关度过滤（默认低于 `0.3` 丢弃）、排序并限制最大要点数。这一步将“检索材料”压缩为可控 token 的事实层。

### 6.5 反思补搜：`reflector.py`

若 `research_reflection_rounds > 0` 且已有要点，`find_gap_queries()` 使用初始章节和最多 40 条要点判断覆盖缺口，最多输出 `research_reflection_max_queries` 个新查询。引擎再执行一次相同的检索和提炼流程。它是有上限的自我纠正，而不是无限 Agent loop。

### 6.6 大纲整理与写作：`curator.py`、`writer.py`

`curate_outline()` 让 LLM 为每一节生成 thesis 并挑选 `learning_ids`。若失败，fallback 用字符二元组相似度把要点分配给最接近的章节，保证每章尽量有材料。

写作时 `write_section_stream()` 仅输入本章论点、分配到的 Learning 和前文章节摘要。模型 token 直接向上游 yield，构成页面实时正文；章节失败时输出占位文字，不让一章失败中断整篇报告。`summarize()` 最后生成 TL;DR 与核心要点，失败则返回空摘要。

`engine._build_markdown()` 统一拼装标题、TL;DR、要点、章节与来源表，并把正文中的 `[来源 N]` 替换为带标题/域名提示的 Markdown 链接。

## 7. Verifier Loop：生成后如何复核和修复

### 7.1 引擎接入点

`engine.py` 在初稿 Markdown 构造完成后检查 `settings.loop_enabled`：关闭时直接发 `report`；开启时把 `{title, markdown, sources, headings}` 作为 artifact 交给 `LoopController`。

`run_research()` 同时注入两个业务回调，使通用 Loop 不依赖 Research 模块：

- `patch_callback(queries)`：按 verifier 提供的查询补搜、补提炼，把新证据追加/合并为“补充信息(质量复核反馈后追加)”章节。
- `rewrite_callback(chapters)`：只对指定章节重新调用 writer，再替换对应正文。

这是一种依赖倒置：Loop 决定“修什么”，Research 引擎负责“怎样检索和怎样重写”。

### 7.2 评分模型：`loop/models.py` 与 `rubric/research.py`

`RubricDef` 用 0~5 原始分归一化后按权重计算 `total`。研究报告 rubric 的六个维度是：

| 维度 | 权重 | 硬门槛 |
| --- | ---: | ---: |
| coverage 覆盖度 | 0.20 | 3 |
| faithfulness 引用对齐 | 0.25 | 3 |
| depth 论证深度 | 0.15 | 2 |
| timeliness 时效性 | 0.15 | 3 |
| relevance 相关性 | 0.15 | 3 |
| readability 结构与可读性 | 0.10 | 2 |

通过条件不是只有总分：`total >= 0.7` 且任何维度都不低于硬门槛。`task.py` 暂时复制同一套 rubric，只是名称为 `task`，为未来定时任务使用不同权重预留扩展点。

### 7.3 Verifier：`verifier/llm_verifier.py`

Verifier 使用独立的 critic system prompt 和 `verify_research.jinja2`，把主题、rubric、完整 artifact Markdown 和来源列表交给模型，期待结构化 JSON：原始分和反馈（问题、缺失覆盖、错误引用、薄弱章节、摘要）。解析失败的维度默认 0 分。

支持两种模式：

- `same`：复用生成模型，但以独立 messages session 作为 self-critique 基线。
- `cross`：读取用户 `type='verifier'` 的默认模型，以不同模型作 judge；若未配置或构造失败，日志警告后**降级为 same**。

所以 `loop_verifier_kind='cross'` 并不保证实际使用跨模型；应通过 `LoopRun.verifier_kind` 和 `verifier_model` 审计真实结果。

### 7.4 决策：`policy.py`

`Policy.decide()` 依评分采取有限状态决策：

```text
总分达标且无硬门槛失败 -> pass
达到轮数上限             -> exceed
至少 3 个维度低于门槛     -> exceed（全面失败，不盲目重做）
depth/relevance 有问题    -> retry_rewrite
coverage/faithfulness/
timeliness 有问题         -> retry_patch
仅总分偏低                -> retry_patch 兜底
```

默认最大两轮，目的是控制成本和避免“越改越乱”。`exceeded` 的含义是“得到了报告但未通过质量门槛”，不同于系统异常 `failed`。

### 7.5 修复执行器：`repair/`

`RepairExecutor` 抽象为两个阶段：`plan()` 只根据评分/反馈生成 `RepairAction`，`execute()` 通过注入回调获得新 artifact。

- `PatchRepair`：提取 `missing_coverage`、覆盖/引用/时效问题详情与错误来源号，去重并最多生成 3 个补搜查询；成本低，适合局部事实和引用问题。
- `ChapterRewrite`：只接受确实存在于 artifact headings 的 `weak_chapters`，也会从 depth/relevance issue 文本匹配章节；最多重写 2 章，避免 verifier 虚构标题或无限重写。

两个执行器的 callback 缺失、回调异常或返回非字典时都保留旧 artifact。这保证 Repair 自身的故障不会清空已生成报告。

### 7.6 `LoopController` 状态机

`LoopController.run()` 做以下事情：

1. 构建 verifier，创建 `LoopRun(status=running)`，发送 `loop_started`。
2. 每轮发送 `loop_verify_start`，调用 `verifier.verify()`，得到结构化评分。
3. 调用 Policy，发送 `loop_verify_done`；将这一轮的 artifact 摘要、评分、反馈和决策写入 `LoopIteration`。
4. 若需要修复，发送 `loop_repair_start`，执行 Patch 或章节重写，再发送 `loop_repair_done`，进入下一轮验证。
5. `pass`、`exceed` 或意外错误时结束 `LoopRun`，发送 `loop_finished(final_artifact=...)`。

Controller 还创建 tracing span，把 iteration id 同时关联 verifier 与 repair。Research 引擎获得 `loop_started.run_id` 后会把它关联到 trace，前端历史页可进一步跳转执行轨迹。

需要特别理解两个容错取舍：

- verifier 调用异常时，Controller 记录一轮并将 loop 视为 `passed`，保留报告，避免“质量检查服务短暂不可用”阻断主业务；`note` 会标记 verifier 异常。
- repair 异常时，Controller 用旧 artifact 继续下一轮验证；整体 Controller 异常则引擎捕获，直接保留初稿。

因此 Quality Loop 在此项目中是**增强性质量控制**，不是生成报告的硬依赖。

## 8. 定时 Agent：从到期扫描到通知

### 8.1 任务服务：`agent_task_service.py`

`AgentTaskService` 负责输入校验和时间计算，使用 `Asia/Shanghai`：

- daily：当天目标时间已过则加一天。
- weekly：计算到目标星期的偏移；若同日时间已过则加 7 天。
- interval：当前时间加间隔小时。

创建、编辑和启用任务时都会计算 `next_run_at`；停用时置空。`run_now()` 仅校验任务归属并 `delay()` Celery，不改变原有下次运行时间，因此手动运行不会扰乱既定日程。

`list_runs()` 查询关联该 `task_id` 的研究报告，并批量查询每份报告最新的 `LoopRun`，为前端补充 `verified` 和 `final_score`。

### 8.2 Celery worker：`tasks/agent_task.py`

定时流程分两层：

```text
beat 定期调用 heartbeat_task
  -> AgentTaskRepository.list_due() 以 FOR UPDATE SKIP LOCKED 领取到期任务
  -> 先更新所有任务的 next_run_at 并 commit
  -> 为每个任务投递 run_agent_task_task

run_agent_task_task
  -> asyncio.run(_run_task())
  -> Redis SET NX + TTL 防重
  -> 创建 ResearchReport(task_id=...)
  -> 标记 AgentTask.last_status=running
  -> 消费同一个 run_research()
  -> 保存报告、更新任务状态、按需通知
```

`SKIP LOCKED` 解决多 worker 同时 heartbeat 时的重复领取；运行任务还有 Redis 锁，TTL 为研究超时加缓冲。Redis 不可用时选择“允许运行并记录 warning”，这是可用性优先的取舍。

`_execute_research()` 不走 Redis/SSE：它本地消费引擎事件，只累积 outline、正文和最终 report 后写数据库。整个执行包在 `asyncio.wait_for(... research_task_timeout)` 中，默认 900 秒。

成功后若 `notify_enabled` 为真，worker 会先检查该报告的 Loop 结果：Loop 关闭、无 LoopRun 或检查异常时当前实现仍允许通知；只有确实存在且不是 `passed` 的质量回路结果会阻止通知。通知内容提取报告 TL;DR/要点，并链接到 `/research?report={id}`。

## 9. 状态机与事件契约汇总

### 9.1 报告状态

```text
pending
  -> planning
  -> searching（提炼/反思/整理也复用此展示状态）
  -> writing
  -> summarizing
  -> done

任意阶段异常 -> failed（保留部分正文和错误）
```

`distilling`、`reflecting`、`curating` 是引擎/SSE 的细粒度 phase，`ResearchReport.status` 的数据库枚举没有为它们逐一建值；前端可显示细阶段，数据库保持较粗粒度状态。

### 9.2 Loop 状态

```text
running -> passed
        -> exceeded（生成成功但未达到质量门槛）
        -> failed（Controller 未捕获异常）
```

### 9.3 关键配置边界

`api/app/config.py` 控制了复杂度和成本：最多查询数、搜索并发与重试、网页抓取并发/超时、每源正文上限、提炼并发、相关度阈值、最大 Learning 数、反思轮数、MCP 最大工具轮数/超时、定时任务总超时，以及 `loop_enabled`、`loop_verifier_kind`、`loop_max_iterations`。

这些参数意味着系统的“主动性”是严格受限的：搜索、MCP、反思和修复都存在轮数、并发或 token 边界，不会无限自我迭代。

## 10. 文件—实现—功能逐项索引

下面按用户入口到后台执行的顺序列出本模块的主要文件。阅读时先看“关键实现”，再回到上文理解其输入、输出和失败处理。

### 10.1 前端与接口层

| 文件 | 关键实现 | 实现的功能 |
| --- | --- | --- |
| `web/src/pages/ResearchPage.tsx` | `startResearch()`、`openReport()`、`buildHandlers()` | 发起 POST SSE 研究；把计划、来源、token 和 `loop_*` 事件更新到页面；重开进行中报告时订阅续传；完成后加载质量卡。 |
| `web/src/pages/AgentTaskPage.tsx` | `load()`、`submit()`、`runNow()`、`openRuns()`、`polish()` | 维护定时任务的创建/编辑/启停；将时间表单转为 API 参数；手动投递任务；显示以研究报告为载体的运行历史；复用研究指令润色。 |
| `web/src/api/research.ts` | `streamResearch()`、`subscribeResearchEvents()`、`consumeSSE()`、`dispatch()` | 用 `fetch` 读取 POST/GET SSE 流并手工解析事件；定义研究报告、来源、质量回路的 TypeScript 契约。 |
| `web/src/api/agentTask.ts` | `agentTaskApi`、`AgentTask`、`AgentTaskRun` | 封装定时任务 CRUD、启停、立即运行、运行历史和红点接口；将后端 Loop 状态映射为 UI 可展示字段。 |
| `api/app/controllers/research_controller.py` | `/stream`、`/{id}/events`、`/{id}/loop`、`/save-to-kb` | 认证后的 HTTP/SSE 路由层；负责 StreamingResponse、报告管理、导出、分享和质量详情出口。 |
| `api/app/controllers/agent_task_controller.py` | `/agent-tasks`、`/{id}/run`、`/{id}/runs` | 定时任务管理接口；“立即运行”只触发异步投递，不同步等待研究完成。 |
| `api/app/schemas/research_schema.py` | `ResearchStartRequest`、`SaveToKbRequest`、`OptimizeTopicRequest` | 校验主题长度、知识库范围、目标知识库和待润色指令。 |
| `api/app/schemas/agent_task_schema.py` | `AgentTaskUpsertRequest` | 校验任务名、研究指令、触发类型、星期和间隔范围。 |

### 10.2 服务层、调度和持久化

| 文件 | 关键实现 | 实现的功能 |
| --- | --- | --- |
| `api/app/services/research_service.py` | `stream_research()`、`resume_events()`、`_run_research_bg()` | 创建报告、启动独立后台协程、将引擎事件广播到 Redis/SSE、维护续传缓冲、成功/失败落库。 |
| `api/app/services/research_service.py` | `_finish()`、`_fail()`、`save_to_kb()`、`get_loop_detail()` | 写最终报告或部分失败产物；把完成报告变成 Markdown 文档并投递解析；返回 LoopRun/Iteration 质量审计。 |
| `api/app/services/agent_task_service.py` | `compute_next_run()`、`create/update/set_enabled()`、`run_now()`、`list_runs()` | 以 `Asia/Shanghai` 计算 daily/weekly/interval 的下次执行时间；管理任务；关联报告历史和 verifier 状态。 |
| `api/app/tasks/agent_task.py` | `heartbeat_task()`、`_heartbeat()` | 周期性扫描到期任务；使用数据库锁领取任务、先推进 `next_run_at`，再投递真正执行任务。 |
| `api/app/tasks/agent_task.py` | `run_agent_task_task()`、`_run_task()`、`_do_run()`、`_execute_research()` | Celery 同步入口通过 `asyncio.run()` 运行异步流程；Redis 锁防重复；创建关联报告；消费共享研究引擎并更新任务状态。 |
| `api/app/models/research_report_model.py` | `ResearchReport`、状态常量 | 保存一次研究的主题、阶段状态、Markdown、提纲、来源、错误和可选 `task_id`。 |
| `api/app/models/agent_task_model.py` | `AgentTask`、触发/运行状态常量 | 保存调度规则、是否启用、下次运行时间、最后结果和通知开关。 |
| `api/app/models/loop_model.py` | `LoopRun`、`LoopIteration` | 保存质量复核的配置快照、最终状态/分数，以及每轮评分、反馈、决策、修复与 artifact 摘要。 |
| `api/app/repositories/research_report_repository.py` | `create()`、`list_paged()`、`list_by_task()` | 对报告做用户隔离的创建、查询、分页、按任务查历史和删除。 |
| `api/app/repositories/agent_task_repository.py` | `list_due()` | 查询到期启用任务，并以 `FOR UPDATE SKIP LOCKED` 防止多 worker 重复领取。 |
| `api/app/core/agent/loop/store.py` | `create_run()`、`record_iteration()`、`finish_run()` | 将 LoopController 的运行、每轮结果和最终结论写入/读取 PostgreSQL。 |

### 10.3 深度研究引擎

| 文件 | 关键实现 | 实现的功能 |
| --- | --- | --- |
| `api/app/core/agent/research/engine.py` | `run_research()` | 总编排器：建立 trace，调用规划、检索、提炼、反思、整理、写作、汇总和质量回路，并持续 yield 统一 dict 事件。 |
| `api/app/core/agent/research/engine.py` | `_retrieve()`、`_pump()` | 把 web/知识库/MCP 来源聚合；将带进度回调的后台协程转换为可逐条向上游转发的事件流。 |
| `api/app/core/agent/research/engine.py` | `_build_markdown()`、`_linkify_citations()` | 将正文 `[来源 N]` 与来源表拼装为最终 Markdown，并把可访问 URL 转为带说明的链接。 |
| `api/app/core/agent/research/planner.py` | `make_plan()`、`_fallback_plan()` | 由主题生成报告标题、章节和子问题；LLM/JSON 失败时提供单章节兜底计划。 |
| `api/app/core/agent/research/retriever.py` | `gather_web_sources()` | 多查询联网检索、URL 去重、网页抓取、摘要兜底、质量过滤、权威性启发式排序。 |
| `api/app/core/agent/research/retriever.py` | `gather_kb_sources()` | 使用混合检索取用户知识库资料，按文档合并片段，并排除系统自产的“深度研究报告”库。 |
| `api/app/core/agent/research/retriever.py` | `gather_mcp_sources()`、`_run_mcp_loop()` | 在模型支持函数调用且用户配有 MCP 时执行有界工具循环，把工具结果变为来源。 |
| `api/app/core/agent/research/distiller.py` | `distill_sources()`、`_distill_one()` | 并发把每份原始来源压缩为带 `source_index` 的 Learning，并按相关度过滤与截断。 |
| `api/app/core/agent/research/reflector.py` | `find_gap_queries()` | 根据计划和已有 Learning 判断信息缺口，生成有限数量的补充查询。 |
| `api/app/core/agent/research/curator.py` | `curate_outline()`、`_fallback_curate()` | 给每章确定核心论点并分配 Learning 编号；模型异常时用字符二元组相似度降级分配。 |
| `api/app/core/agent/research/writer.py` | `write_section_stream()`、`summarize()` | 按章节流式写 Markdown；基于全文提炼 TL;DR 与要点；单章或汇总失败均局部降级。 |
| `api/app/core/agent/research/models.py` | `ResearchPlan`、`Source`、`Learning`、`CuratedSection` | 定义阶段间明确的数据结构，尤其用 Learning 的来源编号确保写作引用可追溯。 |
| `api/app/core/agent/research/prompt_renderer.py` 与 `prompts/*.jinja2` | `render_research_prompt()` | 加载规划、提炼、补搜、整理、写作、汇总和指令润色的提示词模板；将 prompt 内容从 Python 控制流分离。 |

### 10.4 质量验证与修复回路

| 文件 | 关键实现 | 实现的功能 |
| --- | --- | --- |
| `api/app/core/agent/loop/controller.py` | `LoopController.run()` | 通用 `verify -> decide -> repair -> verify` 状态机；创建运行记录、发 Loop SSE 事件、调用修复回调、结束审计。 |
| `api/app/core/agent/loop/models.py` | `RubricDef`、`VerifyScore`、`RepairAction`、`IterationOutcome` | 定义评分、修复和每轮审计的 Pydantic 数据契约；计算加权分与硬门槛失败项。 |
| `api/app/core/agent/loop/policy.py` | `Policy.decide()` | 依据分维阈值、总分、轮数和失败维度数，在通过、补搜、重写、超限之间做成本受控的选择。 |
| `api/app/core/agent/loop/rubric/research.py` | `RESEARCH_RUBRIC` | 定义研究报告的六维评分标准、权重、通过线和每维硬门槛。 |
| `api/app/core/agent/loop/rubric/task.py` | `TASK_RUBRIC` | 当前复制研究 rubric 并改名为 task，为未来定时任务特化评分标准保留接口。 |
| `api/app/core/agent/loop/verifier/base.py` | `Verifier` | 抽象 verifier 的统一 `verify()` 接口，允许同模型和跨模型实现互换。 |
| `api/app/core/agent/loop/verifier/llm_verifier.py` | `SameModelVerifier`、`CrossModelVerifier`、`build_verifier()` | 用 critic prompt 让 LLM 输出结构化评分；跨模型配置不存在或构建失败时降级为同模型。 |
| `api/app/core/agent/loop/verifier/prompts/*.jinja2` | `critic_role.jinja2`、`verify_research.jinja2` | 定义批评者身份、六维评分要求和反馈 JSON 格式。 |
| `api/app/core/agent/loop/repair/base.py` | `RepairExecutor` | 抽象“规划修复动作”和“执行修复”两个阶段，避免 Controller 依赖具体业务引擎。 |
| `api/app/core/agent/loop/repair/patch_repair.py` | `PatchRepair.plan()`、`execute()` | 从缺失覆盖、错误引用等反馈生成最多三个补搜查询，并调用上层注入的补搜回调。 |
| `api/app/core/agent/loop/repair/chapter_rewrite.py` | `ChapterRewrite.plan()`、`execute()` | 从薄弱章节/深度相关性反馈中选择最多两章，仅重写真实存在的章节，并调用上层重写回调。 |

## 11. 阅读代码时应重点关注的设计取舍

1. **服务层与引擎解耦**：研究引擎产出普通 dict 事件；SSE、Redis、Celery 都是外层消费者，因此同一引擎可复用。
2. **证据优先于原始长文本**：`Learning` 绑定 `source_index`，在写作前先压缩和过滤资料，减少上下文噪声并强化引用关系。
3. **质量回路不是无限重生成**：Policy 以问题类型选择补搜或重写，且有全面失败阈值和最大轮数。
4. **可观测性与业务产物分层**：报告正文在 `ResearchReport`，质量审计在 `LoopRun/LoopIteration`，trace 再承担执行链路观测。
5. **容错策略偏向保留产物**：检索源、单源提炼、反思、汇总、Verifier、Repair 均可局部降级；这提高可用性，但也意味着 `done` 不必然等价于“所有增强步骤完整成功”。质量卡和 `LoopRun.note` 是识别降级的依据。
