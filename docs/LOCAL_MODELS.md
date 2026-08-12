# 本地 BGE-M3 与 Responses 模型

项目支持将 OpenAI 兼容的本地 BGE-M3 设为默认 embedding 模型，并将使用
Responses API 的聊天模型作为附加模型导入。API Key 和额外鉴权头均通过项目的
`FERNET_KEY` 加密后写入 PostgreSQL，导入脚本不会输出明文。

## 导入配置

先启动 PostgreSQL 并应用迁移：

```powershell
docker compose up -d postgres
cd api
.\.venv\Scripts\alembic.exe upgrade head
```

从 PersonaMem/XiaoBa 环境文件幂等导入：

```powershell
.\.venv\Scripts\python.exe scripts\import_personamem_models.py `
  --source D:\path\to\.env.personamem-gpt55 `
  --runtime docker
```

`--runtime docker` 会把 embedding 地址中的 `127.0.0.1` 转换为
`host.docker.internal`；如果 API 直接在 Windows 主机运行，请改用
`--runtime host`。

导入结果：

- `Local BGE-M3`：默认 embedding 模型，向量维度须与项目的 1024 维设置一致。
- `GPT-5.5 Responses`：新增的非默认聊天模型，保留原默认聊天模型；支持
  Responses API、reasoning effort 和加密额外请求头。

## 启动与验证

```powershell
docker compose --progress plain build api worker beat
docker compose up -d api worker beat
curl.exe http://127.0.0.1:8000/api/health/ready
```

本地 BGE 服务需要监听可被 Docker Desktop 访问的主机端口。默认示例端点为
`http://127.0.0.1:8081/v1`，其 `/v1/embeddings` 返回的向量长度必须为 1024。

不要把包含真实密钥的 `.env.personamem-*` 文件复制或提交到本仓库。
