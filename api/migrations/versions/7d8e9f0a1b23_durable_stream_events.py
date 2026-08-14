"""durable stream events

Revision ID: 7d8e9f0a1b23
Revises: 6c7d8e9f0a12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "7d8e9f0a1b23"
down_revision = "6c7d8e9f0a12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stream_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stream_type", sa.String(32), nullable=False),
        sa.Column("stream_key", sa.String(128), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column(
            "final_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
        ),
        sa.Column("error_msg", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_stream_runs_stream_type", "stream_runs", ["stream_type"])
    op.create_index("ix_stream_runs_stream_key", "stream_runs", ["stream_key"])
    op.create_index("ix_stream_runs_user_id", "stream_runs", ["user_id"])
    op.create_index("ix_stream_runs_status", "stream_runs", ["status"])
    op.create_index("ix_stream_runs_created_at", "stream_runs", ["created_at"])
    op.create_index(
        "uq_stream_runs_active_key",
        "stream_runs",
        ["stream_type", "stream_key"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "stream_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stream_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("data_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_stream_events_run_id", "stream_events", ["run_id"])
    op.create_index("ix_stream_events_event_type", "stream_events", ["event_type"])
    op.create_index("ix_stream_events_created_at", "stream_events", ["created_at"])
    op.create_index("ix_stream_events_run_id_id", "stream_events", ["run_id", "id"])


def downgrade():
    op.drop_table("stream_events")
    op.drop_table("stream_runs")
