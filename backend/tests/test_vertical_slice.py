import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents.memory_qa_agent import MemoryQAAgent
from app.db import Base
from app.ingestion.telegram_export_parser import load_export, parse_messages
from app.memory.context_retriever import ContextRetriever
from app.memory.episode_service import create_telegram_export_source, import_telegram_messages
from app.models import Company, Entity, Episode, Fact
from app.workers.process_backfill import process_backfill


def build_demo_memory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    company = Company(name="Demo Education Center", industry="education")
    db.add(company)
    db.commit()
    db.refresh(company)

    export = load_export(Path("seed/demo_telegram_export.json"))
    messages = parse_messages(export)
    source = create_telegram_export_source(db, company.id, export["name"])
    imported, skipped = import_telegram_messages(db, company.id, source, messages)
    db.commit()

    return db, company, imported, skipped


def test_telegram_export_creates_episodes():
    db, company, imported, skipped = build_demo_memory()

    assert imported == 6
    assert skipped == 0
    assert db.scalar(select(Episode).where(Episode.company_id == company.id).limit(1)) is not None


def test_process_backfill_creates_entities_and_facts():
    db, company, _, _ = build_demo_memory()

    processed, skipped = asyncio.run(process_backfill(db, company.id))

    assert processed == 6
    assert skipped == 0
    assert db.scalar(select(Entity).where(Entity.company_id == company.id).limit(1)) is not None
    assert db.scalar(select(Fact).where(Fact.company_id == company.id).limit(1)) is not None


def test_temporal_invalidation_works():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    old_fact = db.scalar(select(Fact).where(Fact.fact_text == "Returning students get 10% discount."))
    new_fact = db.scalar(select(Fact).where(Fact.fact_text == "Returning students now get 15% discount from Monday."))

    assert old_fact is not None
    assert new_fact is not None
    assert old_fact.status == "invalidated"
    assert old_fact.invalid_at == new_fact.valid_at
    assert new_fact.status == "active"


def test_context_search_returns_active_current_policy():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    result = ContextRetriever().search(db, company.id, "What is our current discount policy?", time_mode="current")

    assert result["facts"][0]["fact_text"] == "Returning students now get 15% discount from Monday."
    assert result["facts"][0]["fact_type"] == "policy"
    assert all("10% discount" not in fact["fact_text"] for fact in result["facts"])
    assert result["sources"][0]["chat_title"] == "Ziyo Education Managers"


def test_memory_qa_includes_source_evidence():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    result = asyncio.run(MemoryQAAgent().run(db, company.id, "What is our current discount policy?"))
    answer = result["answer"]

    assert "Answer:" in answer
    assert "Evidence:" in answer
    assert "15% discount" in answer
    assert "Valid:" in answer
    assert "Ziyo Education Managers, Founder, msg 3" in answer
    assert "Historical/outdated facts:" in answer
