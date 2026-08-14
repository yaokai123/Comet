"""enterprise knowledge core

Revision ID: 5b6c7d8e9f01
Revises: 4a5b6c7d8e9f
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "5b6c7d8e9f01"
down_revision = "4a5b6c7d8e9f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parser_name", sa.String(64)),
        sa.Column("parser_version", sa.String(64)),
        sa.Column("ir_key", sa.String(1024)),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_version_no"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_document_content_hash"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_status", "document_versions", ["status"])

    op.create_table(
        "knowledge_connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("connector_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("cursor_value", sa.Text()),
        sa.Column("config_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("secret_ref", sa.String(512)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("next_sync_at", sa.DateTime(timezone=True)),
        sa.Column("error_msg", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_knowledge_connectors_user_id", "knowledge_connectors", ["user_id"])
    op.create_index("ix_knowledge_connectors_kb_id", "knowledge_connectors", ["kb_id"])
    op.create_index("ix_knowledge_connectors_type", "knowledge_connectors", ["connector_type"])
    op.create_index("ix_knowledge_connectors_status", "knowledge_connectors", ["status"])

    op.create_table(
        "knowledge_sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("source_version", sa.String(256), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(1024), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("error_msg", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_knowledge_sync_job_idempotency"),
    )
    op.create_index("ix_knowledge_sync_jobs_connector_id", "knowledge_sync_jobs", ["connector_id"])
    op.create_index("ix_knowledge_sync_jobs_external_id", "knowledge_sync_jobs", ["external_id"])
    op.create_index("ix_knowledge_sync_jobs_status", "knowledge_sync_jobs", ["status"])
    op.create_index("ix_knowledge_sync_jobs_available_at", "knowledge_sync_jobs", ["available_at"])

    op.create_table(
        "wiki_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("kb_id", "slug", name="uq_wiki_page_kb_slug"),
    )
    op.create_index("ix_wiki_pages_kb_id", "wiki_pages", ["kb_id"])
    op.create_index("ix_wiki_pages_status", "wiki_pages", ["status"])

    op.create_table(
        "wiki_page_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("build_trace_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("page_id", "version_no", name="uq_wiki_page_version"),
    )
    op.create_index("ix_wiki_page_versions_page_id", "wiki_page_versions", ["page_id"])
    op.create_index("ix_wiki_page_versions_content_hash", "wiki_page_versions", ["content_hash"])
    op.create_index("ix_wiki_page_versions_trace", "wiki_page_versions", ["build_trace_id"])

    op.create_table(
        "wiki_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_page_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_page_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(32), nullable=False, server_default="related"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_page_id", "target_page_id", name="uq_wiki_link_edge"),
    )
    op.create_index("ix_wiki_links_source", "wiki_links", ["source_page_id"])
    op.create_index("ix_wiki_links_target", "wiki_links", ["target_page_id"])

    op.create_table(
        "wiki_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("wiki_page_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chunk_id", sa.String(128), nullable=False),
        sa.Column("block_id", sa.String(256)),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("bbox_json", postgresql.JSONB()),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("page_version_id", "chunk_id", name="uq_wiki_evidence_chunk"),
    )
    op.create_index("ix_wiki_evidence_page_version", "wiki_evidence", ["page_version_id"])
    op.create_index("ix_wiki_evidence_document_version", "wiki_evidence", ["document_version_id"])
    op.create_index("ix_wiki_evidence_chunk_id", "wiki_evidence", ["chunk_id"])

    op.create_table(
        "knowledge_quality_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issue_type", sa.String(48), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(256), nullable=False),
        sa.Column("fingerprint", sa.String(512), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("fingerprint", name="uq_knowledge_quality_fingerprint"),
    )
    op.create_index("ix_knowledge_quality_kb_id", "knowledge_quality_issues", ["kb_id"])
    op.create_index("ix_knowledge_quality_type", "knowledge_quality_issues", ["issue_type"])
    op.create_index("ix_knowledge_quality_entity", "knowledge_quality_issues", ["entity_id"])
    op.create_index("ix_knowledge_quality_status", "knowledge_quality_issues", ["status"])


def downgrade():
    op.drop_table("knowledge_quality_issues")
    op.drop_table("wiki_evidence")
    op.drop_table("wiki_links")
    op.drop_table("wiki_page_versions")
    op.drop_table("wiki_pages")
    op.drop_table("knowledge_sync_jobs")
    op.drop_table("knowledge_connectors")
    op.drop_table("document_versions")
