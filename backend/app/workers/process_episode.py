from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.provider import build_embedding_provider
from app.llm.openai_provider import build_llm_provider
from app.llm.validation import LLMValidationError
from app.memory.community_service import CommunityService
from app.memory.entity_extractor import EntityExtractor
from app.memory.entity_resolver import EntityResolver
from app.memory.episode_service import build_context_window, mark_episode, should_process_episode
from app.memory.fact_extractor import FactExtractor
from app.memory.fact_resolver import FactResolver
from app.memory.temporal_resolver import TemporalResolver
from app.models import Episode


async def process_episode(db: Session, episode: Episode) -> int:
    if not should_process_episode(episode.content):
        mark_episode(db, episode, "skipped_noise")
        return 0
    settings = get_settings()
    llm = build_llm_provider(settings)
    embeddings = build_embedding_provider(settings)
    previous = build_context_window(db, episode, previous_limit=8)

    try:
        extracted_entities = await EntityExtractor(llm).extract(episode, previous)
        entities = await EntityResolver(embeddings).resolve_many(db, episode.company_id, episode.id, extracted_entities)
        await CommunityService(embeddings).update_for_entities(db, episode.company_id, entities)

        extracted_facts = await FactExtractor(llm).extract(episode, previous, entities)
        resolver = FactResolver(embeddings)
        temporal = TemporalResolver(llm)
        created = 0
        for fact_data in extracted_facts:
            times = await temporal.resolve(episode, fact_data, previous)
            fact = await resolver.create_or_resolve(db, episode.company_id, episode.id, fact_data, times, entities, episode)
            if fact:
                created += 1
        mark_episode(db, episode, "processed")
        return created
    except LLMValidationError as exc:
        episode.raw_payload = {**(episode.raw_payload or {}), "review_error": str(exc), "review_reason": "llm_validation_failed"}
        mark_episode(db, episode, "needs_review")
        return 0
    except Exception as exc:
        episode.raw_payload = {**(episode.raw_payload or {}), "review_error": str(exc), "review_reason": "processing_failed"}
        mark_episode(db, episode, "failed")
        return 0


async def process_episode_by_id(db: Session, company_id: UUID, episode_id: UUID) -> int:
    episode = db.get(Episode, episode_id)
    if not episode or episode.company_id != company_id:
        raise ValueError("Episode not found")
    return await process_episode(db, episode)
