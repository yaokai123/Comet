"""connector document binding

Revision ID: 6c7d8e9f0a12
Revises: 5b6c7d8e9f01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "6c7d8e9f0a12"
down_revision = "5b6c7d8e9f01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connector_document_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connector_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_connectors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(256), nullable=False),
        sa.Column("source_uri", sa.String(2048)),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("connector_id", "external_id", name="uq_connector_external_item"),
    )
    op.create_index(
        "ix_connector_document_bindings_connector_id",
        "connector_document_bindings",
        ["connector_id"],
    )
    op.create_index(
        "ix_connector_document_bindings_document_id",
        "connector_document_bindings",
        ["document_id"],
    )
    op.create_index(
        "ix_connector_document_bindings_status",
        "connector_document_bindings",
        ["status"],
    )


def downgrade():
    op.drop_table("connector_document_bindings")
