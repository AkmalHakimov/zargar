from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.telegram_export_parser import ParsedTelegramMessage
from app.models import Episode, Source


def create_telegram_export_source(db: Session, company_id: UUID, source_name: str, config: dict | None = None) -> Source:
    source = Source(
        company_id=company_id,
        source_type="telegram_export",
        source_name=source_name,
        config=config or {},
    )
    db.add(source)
    db.flush()
    return source


def import_telegram_messages(db: Session, company_id: UUID, source: Source, messages: list[ParsedTelegramMessage]) -> tuple[int, int]:
    imported = 0
    skipped = 0
    for message in messages:
        exists = db.scalar(
            select(Episode.id).where(
                Episode.company_id == company_id,
                Episode.source_id == source.id,
                Episode.chat_id == message.chat_id,
                Episode.message_id == message.message_id,
            )
        )
        if exists:
            skipped += 1
            continue
        episode = Episode(
            company_id=company_id,
            source_id=source.id,
            source_type=source.source_type,
            chat_id=message.chat_id,
            chat_title=message.chat_title,
            message_id=message.message_id,
            actor_name=message.actor_name,
            actor_external_id=message.actor_external_id,
            event_time=message.event_time,
            content_type=message.content_type,
            content=message.content,
            raw_payload=message.raw_payload,
            processed_status="pending",
        )
        db.add(episode)
        imported += 1
    return imported, skipped


def get_previous_messages(db: Session, episode: Episode, limit: int = 8) -> list[Episode]:
    stmt = (
        select(Episode)
        .where(
            Episode.company_id == episode.company_id,
            Episode.chat_id == episode.chat_id,
            Episode.event_time < episode.event_time,
        )
        .order_by(Episode.event_time.desc())
        .limit(limit)
    )
    return list(reversed(db.scalars(stmt).all()))


def get_reply_parent(db: Session, episode: Episode) -> Episode | None:
    reply_to_message_id = (episode.raw_payload or {}).get("reply_to_message_id")
    if not reply_to_message_id:
        return None
    return db.scalar(
        select(Episode).where(
            Episode.company_id == episode.company_id,
            Episode.chat_id == episode.chat_id,
            Episode.message_id == str(reply_to_message_id),
        )
    )


def build_context_window(db: Session, episode: Episode, previous_limit: int = 8) -> list[Episode]:
    previous = get_previous_messages(db, episode, limit=previous_limit)
    reply_parent = get_reply_parent(db, episode)
    if reply_parent and all(item.id != reply_parent.id for item in previous):
        previous = [reply_parent, *previous]
    deduped: dict[str, Episode] = {}
    for item in previous:
        deduped[str(item.id)] = item
    return sorted(deduped.values(), key=lambda item: sortable_time(item.event_time))


def format_context_window(messages: list[Episode]) -> str:
    lines = []
    for msg in messages:
        reply_marker = ""
        reply_to = (msg.raw_payload or {}).get("reply_to_message_id")
        if reply_to:
            reply_marker = f" reply_to={reply_to}"
        lines.append(
            f"- msg {msg.message_id}{reply_marker} | {msg.event_time.isoformat()} | {msg.actor_name}: {msg.content}"
        )
    return "\n".join(lines)


def sortable_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def pending_episodes(db: Session, company_id: UUID, limit: int | None = None) -> list[Episode]:
    stmt = (
        select(Episode)
        .where(Episode.company_id == company_id, Episode.processed_status == "pending")
        .order_by(Episode.event_time.asc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def should_process_episode(content: str) -> bool:
    text = content.strip()
    if is_noise_message(text):
        return False
    low = text.lower()
    business_terms = [
        "discount",
        "payment",
        "customer",
        "lead",
        "complain",
        "approve",
        "price",
        "task",
        "deadline",
        "policy",
        "manager",
        "student",
        "reply",
    ]
    return len(text) > 40 or any(term in low for term in business_terms)


def is_noise_message(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    if not normalized:
        return True
    noise = {"ok", "okay", "ha", "haha", "rahmat", "thanks", "done", "👍", "+", "++", "✅"}
    if normalized in noise:
        return True
    if len(normalized) < 3:
        return True
    chars = [char for char in normalized if not char.isspace()]
    if chars and all(not char.isalnum() for char in chars):
        return True
    return False


def mark_episode(db: Session, episode: Episode, status: str) -> None:
    episode.processed_status = status
