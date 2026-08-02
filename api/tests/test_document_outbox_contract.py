"""Contract checks for the document outbox state model (DB integration runs in CI)."""
from app.models.document_index_job_model import DocumentIndexJob


def test_document_index_job_has_reconciliation_fields():
    columns = DocumentIndexJob.__table__.c
    assert {"document_id", "generation", "status", "attempts", "error_msg"} <= set(columns.keys())
