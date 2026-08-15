"""track temporary chat images for orphan cleanup

Revision ID: 9f0a1b2c3d45
Revises: 8e9f0a1b2c34
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "9f0a1b2c3d45"
down_revision = "8e9f0a1b2c34"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_image_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_key", sa.String(512), nullable=False, unique=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="temporary"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_chat_image_uploads_user_id", "chat_image_uploads", ["user_id"])
    op.create_index("ix_chat_image_uploads_status", "chat_image_uploads", ["status"])
    op.create_index("ix_chat_image_uploads_created_at", "chat_image_uploads", ["created_at"])


def downgrade():
    op.drop_table("chat_image_uploads")
