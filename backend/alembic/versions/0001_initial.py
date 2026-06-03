"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
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
    if is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    create_table_once(
        "companies",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    create_table_once(
        "sources",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("config", json_type(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    create_table_once(
        "episodes",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_id", uuid_type(), sa.ForeignKey("sources.id", ondelete="CASCADE"), index=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.String(length=255), index=True),
        sa.Column("chat_title", sa.String(length=255)),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("actor_name", sa.String(length=255)),
        sa.Column("actor_external_id", sa.String(length=255)),
        sa.Column("event_time", sa.DateTime(timezone=True), index=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("content_type", sa.String(length=64), server_default="text"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("raw_payload", json_type(), server_default="{}"),
        sa.Column("processed_status", sa.String(length=64), server_default="pending", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "source_id", "chat_id", "message_id", name="uq_episode_source_message"),
    )
    create_table_once(
        "entities",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("entity_type", sa.String(length=96), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False, index=True),
        sa.Column("summary", sa.Text),
        sa.Column("embedding", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    if is_postgresql():
        op.execute("ALTER TABLE entities ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    create_table_once(
        "episode_entities",
        sa.Column("episode_id", uuid_type(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", uuid_type(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("episode_id", "entity_id", name="uq_episode_entity"),
    )
    create_table_once(
        "facts",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_entity_id", uuid_type(), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
        sa.Column("target_entity_id", uuid_type(), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
        sa.Column("relation_type", sa.String(length=128), nullable=False, index=True),
        sa.Column("fact_text", sa.Text, nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), index=True),
        sa.Column("invalid_at", sa.DateTime(timezone=True), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.Float, server_default="0.5"),
        sa.Column("status", sa.String(length=64), server_default="active", index=True),
        sa.Column("embedding", sa.Text),
        sa.Column("metadata", json_type(), server_default="{}"),
    )
    if is_postgresql():
        op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    create_table_once(
        "episode_facts",
        sa.Column("episode_id", uuid_type(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fact_id", uuid_type(), sa.ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("episode_id", "fact_id", name="uq_episode_fact"),
    )
    create_table_once(
        "communities",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("embedding", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    if is_postgresql():
        op.execute("ALTER TABLE communities ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    create_table_once(
        "community_entities",
        sa.Column("community_id", uuid_type(), sa.ForeignKey("communities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", uuid_type(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("community_id", "entity_id", name="uq_community_entity"),
    )
    create_table_once(
        "agents",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("agent_type", sa.String(length=96), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text),
        sa.Column("allowed_sources", json_type(), server_default="[]"),
        sa.Column("permissions", json_type(), server_default="{}"),
        sa.Column("schedule", json_type(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    create_table_once(
        "agent_runs",
        sa.Column("id", uuid_type(), primary_key=True),
        sa.Column("company_id", uuid_type(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("agent_id", uuid_type(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=64), server_default="running"),
        sa.Column("query", sa.Text),
        sa.Column("retrieved_context", json_type(), server_default="{}"),
        sa.Column("output", json_type(), server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for table in [
        "agent_runs",
        "agents",
        "community_entities",
        "communities",
        "episode_facts",
        "facts",
        "episode_entities",
        "entities",
        "episodes",
        "sources",
        "companies",
    ]:
        op.drop_table(table)
