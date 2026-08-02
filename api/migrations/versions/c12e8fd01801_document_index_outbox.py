"""document index outbox and generation

Revision ID: c12e8fd01801
Revises: f81eaa21c101
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c12e8fd01801"
down_revision = "f81eaa21c101"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("documents", sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("documents", "generation", server_default=None)
    op.create_table("document_index_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("error_msg", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_document_index_jobs_document_id", "document_index_jobs", ["document_id"])
    op.create_index("ix_document_index_jobs_status", "document_index_jobs", ["status"])

def downgrade():
    op.drop_index("ix_document_index_jobs_status", table_name="document_index_jobs")
    op.drop_index("ix_document_index_jobs_document_id", table_name="document_index_jobs")
    op.drop_table("document_index_jobs")
    op.drop_column("documents", "generation")
