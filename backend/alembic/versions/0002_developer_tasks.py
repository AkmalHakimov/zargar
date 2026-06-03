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


def is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def uuid_type():
    if is_postgresql():
        return postgresql.UUID(as_uuid=True)
    return sa.String(length=36)


def json_type():
    if is_postgresql():
        return postgresql.JSONB
    return sa.JSON


def table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def create_table_once(name: str, *columns, **kwargs) -> None:
    if not table_exists(name):
        op.create_table(name, *columns, **kwargs)


def upgrade() -> None:
    create_table_once(
        "developer_tasks",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
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
        sa.Column("audit_log", json_type(), server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("developer_tasks")
