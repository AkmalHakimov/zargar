from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.base import EmbeddingProvider
from app.models import Community, CommunityEntity, Entity


COMMUNITY_RULES = {
    "Discount Policies": ["discount", "policy", "approval"],
    "Payment Process": ["payment", "invoice", "confirm", "follow-up"],
    "Customer Complaints": ["complaint", "late reply", "refund", "angry"],
    "Sales Process": ["lead", "price", "trial", "customer", "student"],
    "Support Workflow": ["support", "reply", "escalate", "manager"],
    "Tasks": ["task", "deadline", "assigned"],
}


class CommunityService:
    def __init__(self, embeddings: EmbeddingProvider):
        self.embeddings = embeddings

    async def update_for_entities(self, db: Session, company_id: UUID, entities: list[Entity]) -> None:
        for entity in entities:
            haystack = f"{entity.name} {entity.summary or ''} {entity.entity_type}".lower()
            for name, terms in COMMUNITY_RULES.items():
                if any(term in haystack for term in terms):
                    community = await self.get_or_create(db, company_id, name)
                    exists = db.scalar(
                        select(CommunityEntity).where(
                            CommunityEntity.community_id == community.id,
                            CommunityEntity.entity_id == entity.id,
                        )
                    )
                    if not exists:
                        db.add(CommunityEntity(community_id=community.id, entity_id=entity.id))

    async def get_or_create(self, db: Session, company_id: UUID, name: str) -> Community:
        community = db.scalar(select(Community).where(Community.company_id == company_id, Community.name == name))
        if community:
            return community
        summary = f"Business memory area for {name.lower()}."
        community = Community(
            company_id=company_id,
            name=name,
            summary=summary,
            embedding=await self.embeddings.embed(f"{name} {summary}"),
        )
        db.add(community)
        db.flush()
        return community

