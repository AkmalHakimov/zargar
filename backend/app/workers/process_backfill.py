from uuid import UUID

from sqlalchemy.orm import Session

from app.memory.episode_service import pending_episodes, should_process_episode
from app.workers.process_episode import process_episode


async def process_backfill(db: Session, company_id: UUID, limit: int | None = None) -> tuple[int, int]:
    processed = 0
    skipped = 0
    for episode in pending_episodes(db, company_id, limit=limit):
        before = episode.processed_status
        await process_episode(db, episode)
        if episode.processed_status == "skipped_noise":
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
