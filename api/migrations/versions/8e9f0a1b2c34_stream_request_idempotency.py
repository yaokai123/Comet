"""add request-level idempotency to durable streams

Revision ID: 8e9f0a1b2c34
Revises: 7d8e9f0a1b23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "8e9f0a1b2c34"
down_revision = "7d8e9f0a1b23"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stream_runs",
        sa.Column("client_request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_stream_runs_client_request_id",
        "stream_runs",
        ["client_request_id"],
    )
    op.create_unique_constraint(
        "uq_stream_runs_user_request",
        "stream_runs",
        ["user_id", "client_request_id"],
    )


def downgrade():
    op.drop_constraint("uq_stream_runs_user_request", "stream_runs", type_="unique")
    op.drop_index("ix_stream_runs_client_request_id", table_name="stream_runs")
    op.drop_column("stream_runs", "client_request_id")
