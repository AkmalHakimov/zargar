from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.memory.episode_service import build_context_window, format_context_window
from app.models import Company, Episode, Source


def test_context_window_includes_reply_parent_outside_recent_limit():
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

    parent = episode(company.id, source.id, "1", "Founder", "Original discount policy", 1)
    db.add(parent)
    for index in range(2, 12):
        db.add(episode(company.id, source.id, str(index), "Sender", f"Message {index}", index))
    reply = episode(
        company.id,
        source.id,
        "12",
        "Madina",
        "Replying to the original policy",
        12,
        raw_payload={"reply_to_message_id": "1"},
    )
    db.add(reply)
    db.commit()

    window = build_context_window(db, reply, previous_limit=4)
    formatted = format_context_window(window)

    assert parent in window
    assert "msg 1" in formatted
    assert "Original discount policy" in formatted


def episode(company_id, source_id, message_id, actor, content, day, raw_payload=None):
    return Episode(
        company_id=company_id,
        source_id=source_id,
        source_type="telegram_export",
        chat_id="chat-1",
        chat_title="Demo Chat",
        message_id=message_id,
        actor_name=actor,
        event_time=datetime(2026, 5, day, tzinfo=timezone.utc),
        content_type="text",
        content=content,
        raw_payload=raw_payload or {},
        processed_status="pending",
    )
