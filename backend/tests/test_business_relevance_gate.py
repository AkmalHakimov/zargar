import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Company, Episode, Fact, Source
from app.workers.process_episode import process_episode


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


def make_episode(company, source, message_id: str, content: str) -> Episode:
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


def process_content(content: str):
    db, company, source = setup_db()
    episode = make_episode(company, source, "1", content)
    db.add(episode)
    db.commit()

    created = asyncio.run(process_episode(db, episode))

    return db, company, episode, created


def test_friends_chat_creates_zero_facts():
    db, company, episode, created = process_content("Friends are coming for dinner this weekend, see you at the restaurant.")

    assert created == 0
    assert episode.processed_status == "skipped_personal"
    assert episode.raw_payload["relevance_classification"] == "personal_chat"
    assert fact_count(db, company.id) == 0


def test_business_discount_message_creates_policy_fact():
    db, company, episode, created = process_content("Decision: returning students now get 15% discount from Monday.")

    assert created >= 1
    assert episode.processed_status == "processed"
    assert episode.raw_payload["relevance_classification"] == "business_relevant"
    assert has_fact_type(db, company.id, "policy")


def test_payment_issue_creates_payment_issue_fact():
    db, company, episode, created = process_content("Payment issue: confirmations are delayed because they are checked manually.")

    assert created >= 1
    assert episode.processed_status == "processed"
    assert has_fact_type(db, company.id, "payment_issue")


def test_complaint_creates_complaint_fact():
    db, company, episode, created = process_content("Two customers complained about late reply after the price message.")

    assert created >= 1
    assert episode.processed_status == "processed"
    assert has_fact_type(db, company.id, "complaint")


def test_task_assignment_creates_task_fact():
    db, company, episode, created = process_content("Open task: call leads who did not respond after the price message by Friday.")

    assert created >= 1
    assert episode.processed_status == "processed"
    assert has_fact_type(db, company.id, "task")


def fact_count(db, company_id) -> int:
    return len(db.scalars(select(Fact).where(Fact.company_id == company_id)).all())


def has_fact_type(db, company_id, fact_type: str) -> bool:
    return any(
        (fact.metadata_ or {}).get("fact_type") == fact_type
        for fact in db.scalars(select(Fact).where(Fact.company_id == company_id))
    )
