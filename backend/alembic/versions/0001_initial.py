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


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("config", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), index=True),
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
        sa.Column("raw_payload", postgresql.JSONB, server_default="{}"),
        sa.Column("processed_status", sa.String(length=64), server_default="pending", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "source_id", "chat_id", "message_id", name="uq_episode_source_message"),
    )
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("entity_type", sa.String(length=96), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False, index=True),
        sa.Column("summary", sa.Text),
        sa.Column("embedding", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE entities ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    op.create_table(
        "episode_entities",
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("episode_id", "entity_id", name="uq_episode_entity"),
    )
    op.create_table(
        "facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source_entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
        sa.Column("relation_type", sa.String(length=128), nullable=False, index=True),
        sa.Column("fact_text", sa.Text, nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), index=True),
        sa.Column("invalid_at", sa.DateTime(timezone=True), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.Float, server_default="0.5"),
        sa.Column("status", sa.String(length=64), server_default="active", index=True),
        sa.Column("embedding", sa.Text),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
    )
    op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    op.create_table(
        "episode_facts",
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("episode_id", "fact_id", name="uq_episode_fact"),
    )
    op.create_table(
        "communities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("embedding", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE communities ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector")
    op.create_table(
        "community_entities",
        sa.Column("community_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("communities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("community_id", "entity_id", name="uq_community_entity"),
    )
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("agent_type", sa.String(length=96), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text),
        sa.Column("allowed_sources", postgresql.JSONB, server_default="[]"),
        sa.Column("permissions", postgresql.JSONB, server_default="{}"),
        sa.Column("schedule", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=64), server_default="running"),
        sa.Column("query", sa.Text),
        sa.Column("retrieved_context", postgresql.JSONB, server_default="{}"),
        sa.Column("output", postgresql.JSONB, server_default="{}"),
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
