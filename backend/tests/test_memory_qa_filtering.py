import asyncio

from tests.test_vertical_slice import build_demo_memory
from app.agents.memory_qa_agent import MemoryQAAgent


def test_important_decisions_returns_decision_not_complaints():
    db, company, _, _ = build_demo_memory()
    from app.workers.process_backfill import process_backfill

    asyncio.run(process_backfill(db, company.id))
    result = asyncio.run(MemoryQAAgent().run(db, company.id, "What important decisions were made?"))

    assert "[decision]" in result["answer"]
    assert "Founder decided to update" in result["answer"]
    assert "complained" not in result["answer"].lower()


def test_complaints_repeated_returns_complaint_facts():
    db, company, _, _ = build_demo_memory()
    from app.workers.process_backfill import process_backfill

    asyncio.run(process_backfill(db, company.id))
    result = asyncio.run(MemoryQAAgent().run(db, company.id, "What complaints repeated?"))

    assert "[complaint]" in result["answer"]
    assert "late replies" in result["answer"]
    assert "Founder decided" not in result["answer"]


def test_current_policies_separates_active_and_outdated():
    db, company, _, _ = build_demo_memory()
    from app.workers.process_backfill import process_backfill

    asyncio.run(process_backfill(db, company.id))
    result = asyncio.run(MemoryQAAgent().run(db, company.id, "What are our current policies?"))
    answer = result["answer"]

    assert "Current facts:" in answer
    assert "Returning students now get 15% discount" in answer
    assert "Historical/outdated facts:" in answer
    assert "Returning students get 10% discount" in answer
    assert "Status: invalidated" in answer
