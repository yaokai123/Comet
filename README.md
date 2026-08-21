# Comet（彗记）

### 可私有部署的 AI 知识库、长期记忆与企业级 RAG 平台

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Elasticsearch](https://img.shields.io/badge/Elasticsearch-8-005571?logo=elasticsearch&logoColor=white)](https://www.elastic.co/elasticsearch)

Comet 将文档、表格、图片、网页、对话记忆和联网信息统一为可检索、可引用、可治理的个人或企业知识空间。它不仅提供聊天界面，还覆盖文档解析、Root+Leaf 分块、混合检索、数值推理、权限隔离、知识图谱、异步任务、评测与生产部署的完整链路。

> 项目仍在快速迭代。生产部署前请替换所有示例密钥，按实际负载调整数据库、Elasticsearch、Celery 和模型服务资源。

## 为什么是 Comet

| 能力 | 说明 |
|---|---|
| 企业知识库 | 多知识库、组织与成员管理、角色权限、资源授权、审计日志 |
| 结构化文档理解 | PDF、DOCX、Excel、Markdown、HTML、TXT；可选 MinerU 版面解析 |
| Root+Leaf RAG | PostgreSQL 保存完整上下文 Root，Elasticsearch 检索轻量 Leaf，命中后回填原始上下文 |
| 混合检索 | BM25、向量、RRF、Rerank、查询扩展、元数据过滤和严格 Scope 规划 |
| 财务问答 | 表格与财报检索、结构化数值提取、公式规划、确定性计算和引用校验 |
| 多模态知识 | 图片描述、OCR、图片语义检索、页面与区域级引用 |
| 长期记忆 | 对话记忆萃取、Neo4j 图谱、事件时间线、社区聚类和主动召回 |
| Agent 与研究 | 知识库、记忆、联网工具编排；深度研究、Verifier Loop 和定时任务 |
| 可靠异步入库 | Celery durable outbox、幂等任务锁、过期任务恢复、解析失败重试 |
| 可量化评测 | FinanceBench、TAT-QA、CRUD-RAG、ViDoRe 的统一数据与计分契约 |

## 系统架构

```text
┌────────────────────────────── React / TypeScript Web ──────────────────────────────┐
│ 对话 · 知识库 · 图片 · 记忆 · 研究 · 图谱 · RBAC · 模型配置 · 运行观测          │
└─────────────────────────────────────┬───────────────────────────────────────────────┘
                                      │ REST + SSE
┌─────────────────────────────────────▼───────────────────────────────────────────────┐
│                                FastAPI API                                          │
│  Agent / QueryPlan / RBAC / RAG / Financial QA / Memory / Research / Model Router  │
└──────────────┬─────────────────┬──────────────────┬──────────────────┬───────────────┘
               │                 │                  │                  │
        ┌──────▼──────┐   ┌──────▼──────┐   ┌───────▼──────┐   ┌──────▼──────┐
        │ PostgreSQL  │   │Elasticsearch│   │    Neo4j     │   │    Redis    │
        │业务/RBAC/Root│   │ Leaf/BM25/向量│   │ 记忆知识图谱 │   │缓存/锁/队列 │
        └─────────────┘   └─────────────┘   └──────────────┘   └──────┬──────┘
                                                                      │
                                              ┌───────────────────────▼──────────────┐
                                              │ Celery Worker + Beat + Durable Outbox│
                                              │ 解析 · 向量化 · 记忆 · 研究 · 维护   │
                                              └──────────────────────────────────────┘
```

### 企业文档入库链路

```text
上传文件
  → durable outbox
  → Celery 解析任务
  → MinerU / PyMuPDF / Excel parser
  → canonical Document IR
  → Adaptive Root+Leaf chunking
  → Embedding + Elasticsearch Leaf index
  → PostgreSQL Root / version / provenance
  → 可引用的混合检索与答案生成
```

任务以文档 generation 保证幂等；worker 异常退出后，过期的 `queued/running` outbox 会自动回收，不会永久停留在 `parsing`。

## 技术栈

| 层 | 技术 |
|---|---|
| Web | React 18、TypeScript、Vite、Ant Design、Zustand、ECharts、AntV X6 |
| API | Python 3.12、FastAPI、Pydantic、SQLAlchemy Async、Alembic |
| 检索 | Elasticsearch 8、BM25、Dense Vector、RRF、Rerank |
| 数据 | PostgreSQL 16、Neo4j 5、Redis |
| 异步任务 | Celery Worker、Celery Beat、durable outbox |
| 文档解析 | MinerU、PyMuPDF、python-docx、openpyxl、BeautifulSoup |
| LLM | OpenAI-compatible providers、LangChain、可配置 Chat / Embedding / Rerank / Vision / ASR |
| 评测 | Pytest、FinanceBench、TAT-QA、CRUD-RAG、ViDoRe |

## 快速开始

### 1. 环境要求

- Docker Desktop 或 Docker Engine + Compose
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- 至少一个 Chat 模型和一个 Embedding 模型

默认端口：PostgreSQL `5432`、Elasticsearch `9200`、Neo4j `7474/7687`、Redis `6379`、API `8000`、Web `5173`。

### 2. 克隆与配置

```bash
git clone https://github.com/yaokai123/Comet.git
cd Comet
cp .env.example .env
cp api/.env.example api/.env
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
Copy-Item api/.env.example api/.env
```

至少修改以下配置：

```dotenv
JWT_SECRET=replace-with-a-long-random-secret
FERNET_KEY=replace-with-a-generated-fernet-key
```

生成 Fernet Key：

```bash
cd api
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`.env`、上传文件、解析产物和模型密钥均已从 Git 提交中排除。

### 3. 启动基础设施

```bash
docker compose up -d postgres elasticsearch neo4j redis
```

可选的本地 BGE Reranker 和完整容器部署配置位于 [`docker-compose.yml`](docker-compose.yml)。生产覆盖配置见 [`docker-compose.prod.yml`](docker-compose.prod.yml)。

### 4. 启动后端

```bash
cd api
uv sync
uv run alembic upgrade head
uv run python run.py
```

验证服务：

```bash
curl http://localhost:8000/api/health
```

### 5. 启动异步任务

在两个独立终端中运行：

```bash
cd api
uv run celery -A app.celery_app.celery_app worker -l info -Q default,parse,memory,beat,research,knowledge --pool=solo
```

```bash
cd api
uv run celery -A app.celery_app.celery_app beat -l info
```

Windows 本地开发建议使用 `--pool=solo`；Linux 容器使用 Compose 中配置的进程池与并发度。

### 6. 启动前端

```bash
cd web
npm install
npm run dev
```

打开 <http://localhost:5173>，注册账号后前往“模型配置”，设置默认 Chat 与 Embedding 模型。

## Docker 部署

### 开发/单机

```bash
docker compose up -d --build
```

### 生产覆盖

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

生产配置限制基础服务的公网暴露，并为 4 核 4 GB 级服务器提供保守资源配置。正式上线前仍需配置 HTTPS 证书、强密码、备份、监控和外部对象存储。

### 高可用 SSE 验证

```bash
docker compose -f docker-compose.yml -f docker-compose.sse-ha.yml up -d --build
```

对应的探针和验证说明位于 `api/eval/sse_ha_probe.py` 与项目文档目录。

## MinerU 文档解析

默认 PDF 可以通过 PyMuPDF 解析。需要更强的版面、表格和图片结构识别时，可启用仓库内的 MinerU 适配服务：

```bash
docker compose -f docker/mineru/docker-compose.yml up -d --build
```

然后配置：

```dotenv
MINERU_ENDPOINT=http://host.docker.internal:18080/parse
MINERU_TIMEOUT_SECONDS=1800
MINERU_FALLBACK_ENABLED=true
```

MinerU 的多次瞬时错误重试共享同一个总超时预算；超时且允许 fallback 时会切换到内置 PDF 解析器。

## 企业权限模型

Comet 同时支持个人知识库和企业组织：

- 系统角色：`owner`、`admin`、`editor`、`viewer`、`auditor`
- 资源层级：organization → knowledge base → document / image
- 授权主体：用户或角色
- 检索前先计算 RBAC 可见范围，再与 QueryPlan Scope 求交
- 显式文件或知识库无法解析时使用严格空结果，不自动扩大检索范围
- 成员、角色和资源授权变更写入审计事件

迁移到最新数据库结构：

```bash
cd api
uv run alembic upgrade head
```

Root+Leaf 存量数据重建：

```bash
cd api
uv run python scripts/reindex_root_leaf.py --limit 100
```

确认小批量任务正常后再去掉 `--limit`。

## 企业知识库评测

统一评测套件覆盖：

| Benchmark | 主要能力 |
|---|---|
| FinanceBench | 财报 PDF、证据检索、财务回答与引用 |
| TAT-QA | 表格与正文联合检索、数值推理 |
| CRUD-RAG | 中文问答、摘要与幻觉识别 |
| ViDoRe | 页面级视觉检索、页码与 BBox |

下载公开数据并生成统一评测输入：

```bash
cd api
uv run python -m eval.benchmarks.fetch --with-financebench-pdfs
uv run python -m eval.run_eval --benchmark financebench
```

FinanceBench 真实生产链路示例：

```bash
uv run python -m eval.benchmarks.financebench.runner \
  --worker-ingest \
  --use-mineru \
  --ingest-timeout 3600
```

数据下载方式、许可、参数和输出结构见 [`api/eval/README.md`](api/eval/README.md)。评测原始数据、用户上传文件和运行结果不会提交到仓库。

## 项目结构

```text
Comet/
├── api/
│   ├── app/
│   │   ├── controllers/     # FastAPI 路由
│   │   ├── services/        # 业务服务
│   │   ├── repositories/    # PostgreSQL / Neo4j 数据访问
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── core/
│   │   │   ├── agent/       # Agent 与工具
│   │   │   ├── knowledge/   # IR、Root+Leaf、QueryPlan、财务推理
│   │   │   ├── memory/      # 长期记忆与图谱
│   │   │   ├── rag/         # 索引、检索与引用
│   │   │   └── llm/         # 模型客户端与路由
│   │   └── tasks/           # Celery 任务与 outbox
│   ├── eval/                # 企业知识库评测
│   ├── migrations/          # Alembic 迁移
│   ├── scripts/             # 运维与重建脚本
│   └── tests/
├── web/                     # React Web / Electron 壳
├── docker/
│   ├── es/                  # Elasticsearch + IK
│   └── mineru/              # MinerU adapter
├── docs/                    # 架构、能力与发布文档
├── docker-compose.yml
├── docker-compose.prod.yml
└── docker-compose.sse-ha.yml
```

## 文档导航

- [完整文档索引](docs/README.md)
- [企业 Root+Leaf、QueryPlan 与 RBAC](docs/09-enterprise-root-leaf-queryplan-rbac.md)
- [企业知识库四基准评测](docs/08-评测体系/01-企业知识库四基准评测.md)
- [API 开发说明](api/README.md)
- [评测使用说明](api/eval/README.md)
- [发布说明](docs/release-notes/v0.0.5.md)

## 开发与验证

后端：

```bash
cd api
uv run ruff check app tests
uv run pytest -q
```

前端：

```bash
cd web
npm run lint
npm run build
```

提交前请确保没有包含 `.env`、模型密钥、用户上传文件、解析 IR、数据库快照或评测原始数据。

## 安全说明

- API Key 使用 Fernet 加密后存储。
- JWT、数据库密码和第三方模型密钥必须通过环境变量提供。
- 示例环境文件只包含占位值或本地开发默认值。
- 企业检索必须经过 RBAC Scope；公共 benchmark 不能替代跨租户泄漏测试。
- 生产环境应启用 HTTPS、密钥轮换、数据库备份、审计留存和最小权限网络策略。

## 参与项目

欢迎通过 GitHub Issues 提交问题、功能建议和可复现的评测结果。提交代码前请保持 controller → service → repository → model/db 的单向分层，并为关键行为补充测试。
