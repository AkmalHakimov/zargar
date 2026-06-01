from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.embeddings.base import EmbeddingProvider
from app.models import Entity, EpisodeEntity


def canonicalize(name: str) -> str:
    return " ".join(name.lower().strip().split())


class EntityResolver:
    def __init__(self, embeddings: EmbeddingProvider):
        self.embeddings = embeddings

    async def resolve_many(self, db: Session, company_id: UUID, episode_id: UUID, extracted: list[dict]) -> list[Entity]:
        resolved: list[Entity] = []
        linked: set[UUID] = set()
        for item in extracted:
            entity = await self.resolve_one(db, company_id, item)
            if entity.id not in linked:
                db.add(EpisodeEntity(episode_id=episode_id, entity_id=entity.id))
                linked.add(entity.id)
            resolved.append(entity)
        db.flush()
        return resolved

    async def resolve_one(self, db: Session, company_id: UUID, item: dict) -> Entity:
        canonical = canonicalize(item["name"])
        existing = db.scalar(
            select(Entity).where(
                Entity.company_id == company_id,
                or_(Entity.canonical_name == canonical, Entity.name.ilike(item["name"])),
            )
        )
        if existing:
            if item.get("summary") and item["summary"] not in (existing.summary or ""):
                existing.summary = ((existing.summary or "") + " " + item["summary"]).strip()
            return existing
        entity = Entity(
            company_id=company_id,
            entity_type=item["type"],
            name=item["name"],
            canonical_name=canonical,
            summary=item.get("summary"),
            embedding=await self.embeddings.embed(f"{item['name']} {item.get('summary', '')}"),
        )
        db.add(entity)
        db.flush()
        return entity
