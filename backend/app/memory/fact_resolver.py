from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.base import EmbeddingProvider
from app.models import Entity, EpisodeFact, Fact


class FactResolver:
    def __init__(self, embeddings: EmbeddingProvider):
        self.embeddings = embeddings

    async def create_or_resolve(
        self,
        db: Session,
        company_id: UUID,
        episode_id: UUID,
        fact_data: dict,
        temporal: dict,
        entities: list[Entity],
        episode,
    ) -> Fact | None:
        source = find_entity(entities, fact_data["source_entity"])
        target = find_entity(entities, fact_data["target_entity"])
        if not source or not target:
            return None

        active = db.scalars(
            select(Fact).where(
                Fact.company_id == company_id,
                Fact.source_entity_id == source.id,
                Fact.target_entity_id == target.id,
                Fact.relation_type == fact_data["relation_type"],
                Fact.status == "active",
            )
        ).all()

        normalized = normalize_fact_text(fact_data["fact_text"])
        for existing in active:
            if normalize_fact_text(existing.fact_text) == normalized:
                link_supporting_episodes(db, existing, episode, fact_data)
                return existing

        now = datetime.now(timezone.utc)
        if should_invalidate(active, fact_data):
            for existing in active:
                existing.status = "invalidated"
                existing.invalid_at = temporal["valid_at"]
                existing.expired_at = now

        fact = Fact(
            company_id=company_id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            relation_type=fact_data["relation_type"],
            fact_text=fact_data["fact_text"],
            valid_at=temporal["valid_at"],
            invalid_at=temporal["invalid_at"],
            confidence=fact_data["confidence"],
            status="needs_review" if fact_data["confidence"] < 0.45 else "active",
            embedding=await self.embeddings.embed(fact_data["fact_text"]),
            metadata_={
                "temporal_reasoning": temporal.get("temporal_reasoning"),
                "supporting_message_ids": fact_data.get("supporting_message_ids", []),
                "fact_type": fact_data.get("fact_type", "workflow"),
            },
        )
        db.add(fact)
        db.flush()
        link_supporting_episodes(db, fact, episode, fact_data)
        return fact


def find_entity(entities: list[Entity], name: str) -> Entity | None:
    normalized = " ".join(name.lower().split())
    for entity in entities:
        if entity.canonical_name == normalized or entity.name.lower() == normalized:
            return entity
    return None


def normalize_fact_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def should_invalidate(active: list[Fact], new_fact: dict) -> bool:
    if not active:
        return False
    text = normalize_fact_text(new_fact["fact_text"])
    replacement_terms = ["updated", "changed", "now", "from monday", "instead", "new rule", "replaces"]
    return any(term in text for term in replacement_terms)


def link_supporting_episodes(db: Session, fact: Fact, episode, fact_data: dict) -> None:
    ids = {str(episode.message_id), *[str(item) for item in fact_data.get("supporting_message_ids", [])]}
    episodes = db.scalars(
        select(type(episode)).where(
            type(episode).company_id == episode.company_id,
            type(episode).chat_id == episode.chat_id,
            type(episode).message_id.in_(ids),
        )
    ).all()
    for supporting_episode in episodes:
        exists = db.get(EpisodeFact, {"episode_id": supporting_episode.id, "fact_id": fact.id})
        if not exists:
            db.add(EpisodeFact(episode_id=supporting_episode.id, fact_id=fact.id))
