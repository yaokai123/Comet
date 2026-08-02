"""create projects and link work objects

Revision ID: f81eaa21c101
Revises: a41f2db7e82c
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f81eaa21c101"
down_revision: Union[str, None] = "a41f2db7e82c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])
    for table in ("conversations", "knowledge_bases", "research_reports", "agent_tasks"):
        op.add_column(table, sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_project_id", table, "projects", ["project_id"], ["id"], ondelete="SET NULL")
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    for table in ("agent_tasks", "research_reports", "knowledge_bases", "conversations"):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_column(table, "project_id")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
