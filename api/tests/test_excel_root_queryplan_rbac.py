import asyncio
import uuid
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.knowledge.excel_adapter import _sheet_blocks, excel_to_ir
from app.core.knowledge.ir import BlockKind
from app.core.knowledge.query_planner import build_query_plan
from app.core.exceptions import BizError
from app.core.rbac import RBACService, validate_permissions
from app.core.rag.parser import SUPPORTED_EXTS
from app.core.rag.search import _filters


def test_excel_rows_preserve_sheet_headers_and_coordinates():
    blocks = _sheet_blocks(
        [["型号", "压力"], ["X-27", "12 MPa"]],
        sheet_name="设备参数",
        sheet_index=1,
        document_id="doc",
        version_id="v1",
        start_order=0,
    )
    assert ".xlsx" in SUPPORTED_EXTS and ".xls" in SUPPORTED_EXTS
    assert [block.kind for block in blocks] == [BlockKind.HEADING, BlockKind.TABLE, BlockKind.TABLE_ROW]
    assert "型号: X-27" in blocks[-1].content
    assert blocks[-1].section_path == ("设备参数",)
    assert blocks[-1].metadata["row_number"] == 2


def test_xlsx_workbook_is_parsed_into_sheet_scoped_ir():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "财务数据"
    sheet.append(["区域", "收入"])
    sheet.append(["华东", 1234])
    stream = io.BytesIO()
    workbook.save(stream)
    ir = excel_to_ir(stream.getvalue(), file_ext=".xlsx", document_id="doc", version_id="v1", title="report.xlsx")
    assert ir.metadata["sheet_names"] == ["财务数据"]
    assert any(block.kind == BlockKind.TABLE_ROW and "收入: 1234" in block.content for block in ir.blocks)


def test_query_plan_resolves_document_chapter_model_and_page():
    kb_id, doc_id = uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace()
    session.scalars = AsyncMock(side_effect=[
        [SimpleNamespace(id=kb_id, name="设备库")],
        [SimpleNamespace(id=doc_id, file_name="X-27设备维护手册.pdf")],
    ])
    plan = asyncio.run(build_query_plan(
        session,
        "请查《X-27设备维护手册》第三章第27页，比较 X-27-A 与 X-27-B",
        allowed_kb_ids=[str(kb_id)],
    ))
    assert plan.intent == "comparison"
    assert plan.scope.document_ids == [str(doc_id)]
    assert "第三章" in plan.scope.sections
    assert 27 in plan.scope.pages
    assert plan.scope.explicit is True


def test_rbac_permission_inheritance_and_validation():
    assert RBACService._allows(["*"], "document.manage")
    assert RBACService._allows(["document.manage"], "document.read")
    assert not RBACService._allows(["document.read"], "document.write")
    assert validate_permissions(["document.read", "document.read"]) == ["document.read"]
    with pytest.raises(Exception):
        validate_permissions(["root.shell"])


def test_retrieval_authorization_keeps_direct_source_grants_narrow():
    filters = _filters({
        "user_id": uuid.uuid4(),
        "source_type": "document",
        "kb_ids": ["visible-kb"],
        "authorized_kb_ids": ["whole-kb"],
        "authorized_source_ids": ["single-doc"],
    })
    authorization = next(item["bool"] for item in filters if "bool" in item)
    assert {"terms": {"kb_id": ["whole-kb"]}} in authorization["should"]
    assert {"terms": {"source_id": ["single-doc"]}} in authorization["should"]
    assert {"terms": {"kb_id": ["visible-kb"]}} not in filters


def test_unresolved_explicit_title_is_fail_closed():
    kb_id = uuid.uuid4()
    session = SimpleNamespace()
    session.scalars = AsyncMock(side_effect=[
        [SimpleNamespace(id=kb_id, name="设备库")],
        [],
    ])
    plan = asyncio.run(build_query_plan(
        session, "请查《不存在的机密文件》", allowed_kb_ids=[str(kb_id)]
    ))
    assert plan.scope.strict_empty is True
    assert plan.scope.document_ids == []


def test_role_manager_cannot_delegate_permissions_it_does_not_hold():
    actor, org_id = uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(owner_id=uuid.uuid4())))
    service = RBACService(session)
    service._membership = AsyncMock(return_value=(object(), SimpleNamespace(permissions=["role.manage", "document.read"])))
    with pytest.raises(BizError) as error:
        asyncio.run(service.require_delegable_permissions(actor, org_id, ["document.manage"]))
    assert error.value.status_code == 403
