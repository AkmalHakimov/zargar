import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone

from app.bot.telegram_owner_bot import (
    NO_MEMORY_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    TelegramOwnerBot,
    split_telegram_message,
)
from app.memory.episode_service import LIVE_TELEGRAM_SOURCE_TYPE
from app.models import Entity, Episode, Fact
from tests.test_vertical_slice import build_demo_memory


class FakeQAAgent:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def run(self, db, company_id, query: str) -> dict:
        self.queries.append(query)
        return {
            "answer": (
                "Answer:\n- yes\n\n"
                "Current facts:\n- current\n\n"
                "Historical/outdated facts:\n- old\n\n"
                "Evidence:\n- Ziyo Education Managers, Founder, msg 3, 2024-01-03T09:00:00"
            ),
            "retrieved_context": {},
        }


def test_unauthorized_user_rejection():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123})

    responses = asyncio.run(bot.handle_message(999, "/help"))

    assert responses == [UNAUTHORIZED_MESSAGE]


def test_command_routing_start_and_help():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123})

    start = asyncio.run(bot.handle_message(123, "/start"))
    help_ = asyncio.run(bot.handle_message(123, "/help"))

    assert "/ask <question>" in start[0]
    assert "/report_week" in help_[0]


def test_ask_calls_memory_qa_service():
    db, company, _, _ = build_demo_memory()
    seed_minimal_memory(db, company.id)
    qa_agent = FakeQAAgent()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, qa_agent=qa_agent)

    responses = asyncio.run(bot.handle_message(123, "/ask What are our current policies?"))

    assert qa_agent.queries == ["What are our current policies?"]
    assert "Answer:" in responses[0]
    assert "Historical/outdated:" in responses[0]
    assert "Historical/outdated facts:" not in responses[0]
    assert "Ziyo Education Managers, Founder, msg 3" in responses[0]


def test_policy_command_routes_to_memory_qa_question():
    db, company, _, _ = build_demo_memory()
    seed_minimal_memory(db, company.id)
    qa_agent = FakeQAAgent()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, qa_agent=qa_agent)

    asyncio.run(bot.handle_message(123, "/policies"))

    assert qa_agent.queries == ["What are our current policies?"]


def test_long_answer_splitting():
    parts = split_telegram_message("A" * 3600 + "\n\n" + "B" * 3600, limit=3500)

    assert len(parts) == 3
    assert all(len(part) <= 3500 for part in parts)


def test_no_memory_response():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, qa_agent=FakeQAAgent())

    responses = asyncio.run(bot.handle_message(123, "/ask What are our current policies?"))

    assert responses == [NO_MEMORY_MESSAGE]


def test_group_message_creates_episode():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, allowed_chat_ids={"-1001"})

    imported = bot.handle_group_message(live_message(chat_id="-1001", message_id="101", text="New lead asked about payment policy."))
    episode = find_live_episode(db, company.id, "101")

    assert imported is True
    assert episode is not None
    assert episode.chat_id == "-1001"
    assert episode.chat_title == "Zargar Managers"
    assert episode.message_id == "101"
    assert episode.actor_name == "Manager One"
    assert episode.actor_external_id == "555"
    assert episode.content == "New lead asked about payment policy."
    assert episode.processed_status == "pending"


def test_unauthorized_chat_ignored():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, allowed_chat_ids={"-1001"})

    imported = bot.handle_group_message(live_message(chat_id="-9999", message_id="102", text="Should not be saved."))

    assert imported is False
    assert find_live_episode(db, company.id, "102") is None


def test_reply_to_message_id_preserved():
    db, company, _, _ = build_demo_memory()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, allowed_chat_ids={"-1001"})

    bot.handle_group_message(live_message(chat_id="-1001", message_id="103", reply_to_message_id="99"))
    episode = find_live_episode(db, company.id, "103")

    assert episode is not None
    assert episode.raw_payload["reply_to_message_id"] == "99"


def test_status_works():
    db, company, _, _ = build_demo_memory()
    seed_minimal_memory(db, company.id)
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123})

    responses = asyncio.run(bot.handle_message(123, "/status"))

    assert "Status:" in responses[0]
    assert "- imported episodes: 6" in responses[0]
    assert "- pending episodes: 6" in responses[0]
    assert "- processed episodes: 0" in responses[0]
    assert "- active facts: 1" in responses[0]
    assert "- last message time:" in responses[0]


def session_factory(db):
    @contextmanager
    def factory():
        yield db

    return factory


def live_message(
    chat_id: str,
    message_id: str,
    text: str = "Customer complaint repeated about late replies.",
    reply_to_message_id: str | None = None,
) -> dict:
    return {
        "chat_id": chat_id,
        "chat_title": "Zargar Managers",
        "message_id": message_id,
        "actor_name": "Manager One",
        "actor_external_id": "555",
        "event_time": datetime(2024, 1, 10, 9, 30, tzinfo=timezone.utc),
        "content_type": "text",
        "content": text,
        "reply_to_message_id": reply_to_message_id,
        "raw_payload": {"message_id": message_id, "chat": {"id": chat_id}},
    }


def find_live_episode(db, company_id, message_id: str):
    return db.query(Episode).filter_by(
        company_id=company_id,
        source_type=LIVE_TELEGRAM_SOURCE_TYPE,
        message_id=message_id,
    ).one_or_none()


def seed_minimal_memory(db, company_id):
    source = Entity(
        company_id=company_id,
        entity_type="policy",
        name="Discount Policy",
        canonical_name="discount policy",
        summary="Current policy.",
    )
    target = Entity(
        company_id=company_id,
        entity_type="customer_segment",
        name="Returning Students",
        canonical_name="returning students",
        summary="Students who return.",
    )
    db.add_all([source, target])
    db.flush()
    db.add(
        Fact(
            company_id=company_id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            relation_type="HAS_POLICY",
            fact_text="Returning students get a discount.",
            valid_at=None,
            invalid_at=None,
            confidence=0.9,
            status="active",
            metadata_={"fact_type": "policy"},
        )
    )
    db.commit()
