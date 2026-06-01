import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Company, Episode, Source
from app.workers.process_backfill import backfill_plan
from app.workers.process_episode import process_episode


class BadLLMProvider:
    async def json_completion(self, system: str, user: str) -> dict:
        return {"entities": [{"type": "policy"}]}

    async def text_completion(self, system: str, user: str) -> str:
        return "{}"


def setup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    company = Company(name="Demo", industry="education")
    db.add(company)
    db.flush()
    source = Source(company_id=company.id, source_type="telegram_export", source_name="Export")
    db.add(source)
    db.flush()
    return db, company, source


def make_episode(company, source, message_id, content):
    return Episode(
        company_id=company.id,
        source_id=source.id,
        source_type="telegram_export",
        chat_id="chat",
        chat_title="Demo Chat",
        message_id=message_id,
        actor_name="Founder",
        event_time=datetime(2026, 5, 20, tzinfo=timezone.utc),
        content_type="text",
        content=content,
        raw_payload={},
        processed_status="pending",
    )


def test_noise_filter_skips_low_value_messages():
    db, company, source = setup_db()
    episode = make_episode(company, source, "1", "ok")
    db.add(episode)
    db.commit()

    created = asyncio.run(process_episode(db, episode))

    assert created == 0
    assert episode.processed_status == "skipped_noise"


def test_backfill_plan_respects_limit():
    db, company, source = setup_db()
    db.add(make_episode(company, source, "1", "ok"))
    db.add(make_episode(company, source, "2", "Returning students get 10% discount."))
    db.add(make_episode(company, source, "3", "Payment issue needs review."))
    db.commit()

    plan = backfill_plan(db, company.id, limit=2)

    assert plan == {"pending": 2, "llm_calls": 1, "skipped_noise": 1}


def test_episode_status_needs_review_on_failed_extraction(monkeypatch):
    db, company, source = setup_db()
    episode = make_episode(company, source, "1", "Returning students get 10% discount.")
    db.add(episode)
    db.commit()

    monkeypatch.setattr("app.workers.process_episode.build_llm_provider", lambda settings: BadLLMProvider())

    created = asyncio.run(process_episode(db, episode))

    assert created == 0
    assert episode.processed_status == "needs_review"
    assert episode.raw_payload["review_reason"] == "llm_validation_failed"
