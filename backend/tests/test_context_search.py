from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.memory.context_constructor import construct_context
from app.memory.context_retriever import keyword_score


def test_keyword_score_matches_fact_and_entities():
    fact = SimpleNamespace(fact_text="Customers complained about late reply after price message.", relation_type="COMPLAINED_ABOUT")
    source = SimpleNamespace(name="Customers", summary="")
    target = SimpleNamespace(name="Late Reply", summary="Support delay")

    score = keyword_score("late reply complaint", ["late", "reply", "complaint"], fact, source, target)

    assert score > 0


def test_context_constructor_includes_source_citations():
    fact = SimpleNamespace(
        fact_text="Returning students get 15% discount.",
        relation_type="UPDATED_RULE",
        valid_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        invalid_at=None,
    )
    episode = SimpleNamespace(
        id=uuid4(),
        chat_title="Education Group",
        chat_id="group-1",
        actor_name="Founder",
        message_id="3",
        event_time=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    source = SimpleNamespace(name="Discount Policy")
    target = SimpleNamespace(name="Returning Students")
    context = construct_context([(fact, episode, source, target)], [], [])

    assert "Returning students get 15% discount" in context
    assert "Source: Education Group, Founder, msg 3" in context

