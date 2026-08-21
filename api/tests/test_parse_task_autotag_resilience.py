import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.tasks.parse import _auto_tag, _parse_locked


class ParseTaskAutoTagResilienceTests(unittest.TestCase):
    def test_auto_tag_returns_when_no_chat_model(self):
        async def run():
            session = AsyncMock()
            with patch("app.tasks.parse.get_optional_client_for_type", AsyncMock(return_value=None)):
                await _auto_tag(session, uuid4(), uuid4(), "hello")

        asyncio.run(run())

    def test_parse_locked_ignores_auto_tag_failure(self):
        async def run():
            document_id = str(uuid4())
            user_id = uuid4()
            doc_id = uuid4()
            session = AsyncMock()
            session.scalar = AsyncMock(side_effect=[None, 0])
            session.execute = AsyncMock()
            session.flush = AsyncMock()
            session.commit = AsyncMock()
            session.get = AsyncMock(return_value=None)
            session.add = lambda item: setattr(item, "id", uuid4())

            class Repo:
                def __init__(self, doc):
                    self._doc = doc
                    self.save = AsyncMock(return_value=doc)

            doc = type("Doc", (), {})()
            doc.id = doc_id
            doc.user_id = user_id
            doc.kb_id = None
            doc.generation = 1
            doc.status = None
            doc.progress = 0.0
            doc.error_msg = None
            doc.file_key = "docs/test.pdf"
            doc.file_ext = ".pdf"
            doc.file_name = "test.pdf"
            doc.chunk_num = 0
            repo = Repo(doc)

            class FakeIR:
                metadata = {"parser_version": "1"}
                blocks = [type("Block", (), {"content": "hello"})()]

                @staticmethod
                def ordered_blocks():
                    return [type("Block", (), {"content": "hello"})()]

            adaptive_chunk = type("Adaptive", (), {
                "chunk_id": "chunk-1",
                "retrieval_text": "retrieval text",
                "section_path": (),
                "page_start": 1,
                "page_end": 1,
                "element_types": (),
                "block_ids": (),
                "metadata": {
                    "region_ids": [],
                    "logical_table_ids": [],
                    "artifact_paths": [],
                    "block_anchors": [],
                },
                "content": "content",
            })()
            chunk_decision = type("Decision", (), {"applied": type("Applied", (), {"value": "adaptive"})()})()
            embed_client = type("Embed", (), {"embed": AsyncMock(return_value=[[0.1, 0.2]])})()

            with patch("app.tasks.parse.DocumentRepository", return_value=repo), \
                 patch("app.tasks.parse.get_storage") as get_storage, \
                 patch("app.tasks.parse.pdf_to_ir", return_value=FakeIR()), \
                 patch("app.tasks.parse.AdaptiveChunker") as adaptive_chunker_cls, \
                 patch("app.tasks.parse.get_client_for_type", AsyncMock(return_value=embed_client)), \
                 patch("app.tasks.parse.chunk_parent_child", return_value=[type("Parent", (), {"children": ["child chunk"]})()]), \
                 patch("app.tasks.parse.build_chunk_doc", return_value={"id": "chunk-doc"}), \
                 patch("app.tasks.parse.delete_by_source", AsyncMock()), \
                 patch("app.tasks.parse.bulk_index", AsyncMock()), \
                 patch("app.tasks.parse._auto_tag", AsyncMock(side_effect=ValueError("bad chat config"))), \
                 patch("app.tasks.parse.extract_model_tokens", return_value=5), \
                 patch("app.tasks.parse.document_ir_json", return_value=b"{}"):
                get_storage.return_value.get = AsyncMock(return_value=b"pdf")
                get_storage.return_value.save = AsyncMock()
                adaptive_chunker_cls.return_value.chunk.return_value = ([adaptive_chunk], chunk_decision)
                await _parse_locked(session, document_id, doc, generation=1, job_id=None)

            self.assertEqual(doc.status, "done")
            self.assertEqual(doc.progress, 1.0)
            self.assertIsNone(doc.error_msg)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
