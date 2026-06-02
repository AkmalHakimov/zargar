"""add developer tasks

Revision ID: 0002_developer_tasks
Revises: 0001_initial
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_developer_tasks"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "developer_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("repo", sa.String(length=255), nullable=False, index=True),
        sa.Column("branch", sa.String(length=255), index=True),
        sa.Column("status", sa.String(length=64), server_default="pending", index=True),
        sa.Column("task_text", sa.Text, nullable=False),
        sa.Column("requester_id", sa.String(length=255), nullable=False, index=True),
        sa.Column("telegram_chat_id", sa.String(length=255)),
        sa.Column("telegram_message_id", sa.String(length=255)),
        sa.Column("pr_url", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("audit_log", postgresql.JSONB, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("developer_tasks")
