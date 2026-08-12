"""model config supports Responses API and encrypted extra headers

Revision ID: 4a5b6c7d8e9f
Revises: 3f4b5c6d7e8f
"""
from alembic import op
import sqlalchemy as sa

revision = "4a5b6c7d8e9f"
down_revision = "3f4b5c6d7e8f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "model_configs",
        sa.Column("wire_api", sa.String(length=32), nullable=False,
                  server_default="chat_completions"),
    )
    op.add_column(
        "model_configs",
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column("extra_headers_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column("store_responses", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )


def downgrade():
    op.drop_column("model_configs", "store_responses")
    op.drop_column("model_configs", "extra_headers_encrypted")
    op.drop_column("model_configs", "reasoning_effort")
    op.drop_column("model_configs", "wire_api")
