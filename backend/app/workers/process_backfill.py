from uuid import UUID

from sqlalchemy.orm import Session

from app.memory.episode_service import LIVE_TELEGRAM_SOURCE_TYPE, pending_episodes, should_process_episode
from app.workers.process_episode import process_episode

SKIPPED_STATUSES = {"skipped_noise", "skipped_personal", "unclear_needs_review"}


async def process_backfill(db: Session, company_id: UUID, limit: int | None = None) -> tuple[int, int]:
    return await process_pending(db, company_id, limit=limit)


async def process_new_live(db: Session, company_id: UUID, limit: int | None = None) -> tuple[int, int]:
    return await process_pending(db, company_id, limit=limit, source_type=LIVE_TELEGRAM_SOURCE_TYPE)


async def process_pending(
    db: Session,
    company_id: UUID,
    limit: int | None = None,
    source_type: str | None = None,
) -> tuple[int, int]:
    processed = 0
    skipped = 0
    for episode in pending_episodes(db, company_id, limit=limit, source_type=source_type):
        before = episode.processed_status
        await process_episode(db, episode)
        if episode.processed_status in SKIPPED_STATUSES:
            skipped += 1
        elif before != episode.processed_status:
            processed += 1
        db.commit()
    return processed, skipped


def backfill_plan(db: Session, company_id: UUID, limit: int | None = None) -> dict:
    episodes = pending_episodes(db, company_id, limit=limit)
    llm_calls = sum(1 for episode in episodes if should_process_episode(episode.content))
    skipped_noise = len(episodes) - llm_calls
    return {"pending": len(episodes), "llm_calls": llm_calls, "skipped_noise": skipped_noise}
