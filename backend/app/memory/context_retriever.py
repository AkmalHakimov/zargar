from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.models import Community, CommunityEntity, Entity, Episode, EpisodeFact, Fact
from app.memory.context_constructor import construct_context


class ContextRetriever:
    def search(
        self,
        db: Session,
        company_id: UUID,
        query: str,
        time_mode: str = "current",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
    ) -> dict:
        terms = expand_query_terms(query)
        intent = classify_query_intent(query)
        SourceEntity = aliased(Entity)
        TargetEntity = aliased(Entity)
        stmt = (
            select(Fact, Episode, SourceEntity, TargetEntity)
            .join(SourceEntity, Fact.source_entity_id == SourceEntity.id)
            .join(TargetEntity, Fact.target_entity_id == TargetEntity.id)
            .outerjoin(EpisodeFact, EpisodeFact.fact_id == Fact.id)
            .outerjoin(Episode, Episode.id == EpisodeFact.episode_id)
            .where(Fact.company_id == company_id)
        )
        if time_mode == "current":
            now = datetime.now(timezone.utc)
            stmt = stmt.where(Fact.status == "active", or_(Fact.invalid_at.is_(None), Fact.invalid_at > now))
        if start_date:
            stmt = stmt.where(Fact.valid_at >= start_date)
        if end_date:
            stmt = stmt.where(Fact.valid_at <= end_date)
        rows = db.execute(stmt).all()
        scored = []
        for fact, episode, source, target in rows:
            score = keyword_score(query, terms, fact, source, target) + intent_score(intent, fact)
            if score <= 0:
                continue
            if fact.status == "active":
                score += 0.25
            if fact.valid_at:
                age_days = max((datetime.now(timezone.utc) - aware(fact.valid_at)).days, 0)
                score += max(0, 0.2 - min(age_days, 365) / 3650)
            scored.append((score, fact, episode, source, target))
        scored.sort(key=lambda item: item[0], reverse=True)
        if intent == "policies":
            scored.sort(key=lambda item: (item[1].status == "active", item[1].invalid_at is None, item[1].valid_at or datetime.min), reverse=True)
        top = [(fact, episode, source, target) for _, fact, episode, source, target in scored[:limit]]
        entity_ids = {source.id for _, _, source, _ in top if source} | {target.id for _, _, _, target in top if target}
        entities = list(db.scalars(select(Entity).where(Entity.id.in_(entity_ids))).all()) if entity_ids else []
        communities = []
        if entity_ids:
            communities = list(
                db.scalars(
                    select(Community)
                    .join(CommunityEntity, CommunityEntity.community_id == Community.id)
                    .where(CommunityEntity.entity_id.in_(entity_ids))
                    .distinct()
                ).all()
            )
        sources = [
            {
                "episode_id": str(episode.id),
                "chat_title": episode.chat_title,
                "actor": episode.actor_name,
                "message_id": episode.message_id,
                "event_time": episode.event_time.isoformat(),
            }
            for _, episode, _, _ in top
            if episode
        ]
        return {
            "context": construct_context(top, entities, communities),
            "facts": [serialize_fact(fact, source, target) for fact, _, source, target in top],
            "entities": [serialize_entity(entity) for entity in entities],
            "communities": [serialize_community(community) for community in communities],
            "sources": sources,
        }


def keyword_score(query: str, terms: list[str], fact: Fact, source: Entity, target: Entity) -> float:
    fact_type = (getattr(fact, "metadata_", None) or {}).get("fact_type", "")
    haystack = f"{fact.fact_text} {fact.relation_type} {fact_type} {source.name} {source.summary or ''} {target.name} {target.summary or ''}".lower()
    score = sum(0.35 for term in terms if term in haystack)
    if query.lower() in haystack:
        score += 0.5
    return score


def classify_query_intent(query: str) -> str:
    lowered = query.lower()
    if "decision" in lowered or "decisions" in lowered or "important" in lowered:
        return "decisions"
    if "complaint" in lowered or "complaints" in lowered:
        return "complaints"
    if "policies" in lowered or "policy" in lowered:
        return "policies"
    if "bottleneck" in lowered or "dropped" in lowered:
        return "bottlenecks"
    if "task" in lowered or "tasks" in lowered:
        return "tasks"
    return "general"


def intent_score(intent: str, fact: Fact) -> float:
    fact_type = (getattr(fact, "metadata_", None) or {}).get("fact_type", "workflow")
    relation = fact.relation_type
    if intent == "decisions":
        if fact_type == "decision":
            return 3.0
        if relation in {"APPROVED", "DECIDED", "UPDATED_RULE", "CREATED_POLICY"}:
            return 2.0
        return -2.0
    if intent == "complaints":
        return 3.0 if fact_type == "complaint" else -1.5
    if intent == "policies":
        return 3.0 if fact_type == "policy" else -1.5
    if intent == "bottlenecks":
        return 3.0 if fact_type in {"bottleneck", "customer_objection"} else -1.0
    if intent == "tasks":
        return 3.0 if fact_type == "task" else -1.0
    return 0.0


def expand_query_terms(query: str) -> list[str]:
    lowered = query.lower()
    stopwords = {"what", "are", "our", "the", "was", "were", "made", "important", "current"}
    terms = [
        term.strip("?.!,")
        for term in lowered.split()
        if len(term.strip("?.!,")) > 2 and term.strip("?.!,") not in stopwords
    ]
    if "decision" in lowered or "important" in lowered:
        terms.extend(["approved", "policy", "updated_rule", "has_policy", "assigned", "task"])
    if "policies" in lowered or "policy" in lowered:
        terms.extend(["policy", "has_policy", "requires", "approval", "discount", "refund"])
    if "complaint" in lowered or "complaints" in lowered:
        terms.extend(["complained", "late", "reply", "complaint"])
    return list(dict.fromkeys(terms))


def serialize_fact(fact: Fact, source: Entity, target: Entity) -> dict:
    return {
        "id": str(fact.id),
        "source_entity": source.name,
        "target_entity": target.name,
        "relation_type": fact.relation_type,
        "fact_type": (getattr(fact, "metadata_", None) or {}).get("fact_type", "workflow"),
        "fact_text": fact.fact_text,
        "valid_at": fact.valid_at.isoformat() if fact.valid_at else None,
        "invalid_at": fact.invalid_at.isoformat() if fact.invalid_at else None,
        "status": fact.status,
        "confidence": fact.confidence,
    }


def serialize_entity(entity: Entity) -> dict:
    return {"id": str(entity.id), "name": entity.name, "type": entity.entity_type, "summary": entity.summary}


def serialize_community(community: Community) -> dict:
    return {"id": str(community.id), "name": community.name, "summary": community.summary}


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
