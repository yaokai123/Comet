"""Contract checks for the document outbox state model (DB integration runs in CI)."""
from datetime import datetime, timezone

from app.models.document_index_job_model import DocumentIndexJob
from app.tasks.document_outbox import _recoverable_jobs_statement


def test_document_index_job_has_reconciliation_fields():
    columns = DocumentIndexJob.__table__.c
    assert {"document_id", "generation", "status", "attempts", "error_msg"} <= set(columns.keys())


def test_recovery_only_selects_current_generation_of_incomplete_documents():
    sql = str(_recoverable_jobs_statement(datetime.now(timezone.utc)))

    assert "document_index_jobs JOIN documents" in sql
    assert "document_index_jobs.generation = documents.generation" in sql
    assert "documents.status !=" in sql
