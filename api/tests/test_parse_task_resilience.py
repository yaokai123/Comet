import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy.exc import PendingRollbackError

from app.models.document_model import DOC_STATUS_FAILED
from app.tasks.parse import _parse_locked


class ParseTaskResilienceTests(unittest.TestCase):
    def test_parse_locked_rolls_back_before_persisting_failure_status(self):
        document_id = str(uuid4())
        doc_id = uuid4()
        user_id = uuid4()
        session = AsyncMock()
        session.rollback = AsyncMock()

        doc = SimpleNamespace(
            id=doc_id,
            user_id=user_id,
            kb_id=None,
            generation=1,
            status=None,
            progress=0.0,
            error_msg=None,
            file_key="docs/test.pdf",
            file_ext=".pdf",
            file_name="test.pdf",
            chunk_num=0,
        )
        reloaded_doc = SimpleNamespace(
            id=doc_id,
            user_id=user_id,
            kb_id=None,
            generation=1,
            status=None,
            progress=0.1,
            error_msg=None,
            file_key="docs/test.pdf",
            file_ext=".pdf",
            file_name="test.pdf",
            chunk_num=0,
        )

        session.get.side_effect = [reloaded_doc]

        original_save = AsyncMock(side_effect=PendingRollbackError("flush failed"))
        recovery_save = AsyncMock(return_value=reloaded_doc)
        repo_instances = [
            SimpleNamespace(save=original_save),
            SimpleNamespace(save=recovery_save),
        ]

        def build_repo(_session):
            return repo_instances.pop(0)

        with patch("app.tasks.parse.DocumentRepository", side_effect=build_repo), patch(
            "app.tasks.parse.redis_task_lock"
        ):
            asyncio.run(_parse_locked(session, document_id, doc, generation=1, job_id=None))

        session.rollback.assert_awaited_once()
        session.get.assert_awaited_once_with(type(doc), doc_id)
        self.assertEqual(reloaded_doc.status, DOC_STATUS_FAILED)
        self.assertIn("flush failed", reloaded_doc.error_msg)
        recovery_save.assert_awaited_once_with(reloaded_doc)


if __name__ == "__main__":
    unittest.main()
