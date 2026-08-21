# Enterprise Root+Leaf、QueryPlan 与 RBAC

## 文档接入

支持 PDF、DOCX、XLSX、XLSM、XLS、Markdown、HTML 和 TXT。Excel 解析保留工作表、表头、行号、公式文本与逻辑表 ID；工作表名进入 `section_path`，引用锚点包含 `sheet_name` 和 `row_number`。

## Root+Leaf

- Root：章节、页面窗口、表格行窗口或 Excel 工作表结构块，存储于 PostgreSQL `knowledge_roots`。
- Leaf：Root 内的小检索单元，只有 Leaf 写入 Elasticsearch 并生成向量。
- Leaf 命中后按 `root_id` 从 PostgreSQL回填；Redis使用 `knowledge-root:{id}` 做短期只读缓存。
- ES保留旧 `parent_id` 回填兼容路径，存量文档重新索引后切换到 `chunk_schema=root_leaf_v1`。

存量数据迁移：

```powershell
cd api
uv run python scripts/reindex_root_leaf.py --limit 100
# 确认后可去掉 --limit；也可使用 --kb-id 或 --organization-id
```

脚本只写入 `document_index_jobs` durable outbox，由现有 reconciler 分批投递，不在迁移事务中调用 embedding 服务。

## QueryPlan 与 Scope

`POST /api/documents/query-plan` 可检查当前用户实际使用的计划。规划器识别知识库名、书名/文件名、章节、页码、设备型号及 comparison/exhaustive/timeline/fact 意图。所有 Scope 都先与 RBAC 可见范围求交；显式文件线索无法解析时使用 `strict_empty`，不会退化为跨库搜索。

## RBAC

企业权限由以下对象组成：

- `organizations`：租户边界。
- `rbac_roles`：系统角色和企业自定义角色，权限采用显式 permission strings。
- `organization_memberships`：一个企业内一个成员对应一个角色。
- `resource_grants`：面向 knowledge_base/document/image 的用户或角色授权。
- `rbac_audit_events`：角色、成员和授权变更审计。

系统角色：owner、admin、editor、viewer、auditor。资源权限从企业角色继承到知识库，再继承到文档和图片；单文档授权在检索时保持 source 级约束，不会扩大为整库权限。个人知识库继续采用原 user ownership 规则。

管理 API 位于 `/api/organizations`，包括角色 CRUD、成员列表/增删改、资源授权 CRUD 和审计事件查询。

企业检索 scorecard 新增 `excel_table` 和 `excel_multi_sheet` 场景，正式评测时需以真实工作簿的 Root/Leaf ID 替换示例 gold ID。
