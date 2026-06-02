from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.telegram_export_parser import ParsedTelegramMessage
from app.models import Episode, Source

LIVE_TELEGRAM_SOURCE_TYPE = "telegram_live"
BUSINESS_RELEVANT = "business_relevant"
PERSONAL_CHAT = "personal_chat"
NOISE = "noise"
UNCLEAR_NEEDS_REVIEW = "unclear_needs_review"


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


def get_or_create_live_telegram_source(db: Session, company_id: UUID, chat_id: str, chat_title: str | None) -> Source:
    source_name = chat_title or f"Telegram chat {chat_id}"
    sources = db.scalars(
        select(Source).where(Source.company_id == company_id, Source.source_type == LIVE_TELEGRAM_SOURCE_TYPE)
    ).all()
    for source in sources:
        if str((source.config or {}).get("chat_id")) == chat_id:
            return source
    source = Source(
        company_id=company_id,
        source_type=LIVE_TELEGRAM_SOURCE_TYPE,
        source_name=source_name,
        config={"chat_id": chat_id, "chat_title": chat_title},
    )
    db.add(source)
    db.flush()
    return source


def import_live_telegram_message(db: Session, company_id: UUID, message: dict) -> Episode | None:
    chat_id = str(message["chat_id"])
    message_id = str(message["message_id"])
    source = get_or_create_live_telegram_source(db, company_id, chat_id, message.get("chat_title"))
    exists = db.scalar(
        select(Episode.id).where(
            Episode.company_id == company_id,
            Episode.source_id == source.id,
            Episode.chat_id == chat_id,
            Episode.message_id == message_id,
        )
    )
    if exists:
        return None
    raw_payload = dict(message.get("raw_payload") or {})
    if message.get("reply_to_message_id") is not None:
        raw_payload["reply_to_message_id"] = str(message["reply_to_message_id"])
    episode = Episode(
        company_id=company_id,
        source_id=source.id,
        source_type=source.source_type,
        chat_id=chat_id,
        chat_title=message.get("chat_title"),
        message_id=message_id,
        actor_name=message.get("actor_name"),
        actor_external_id=str(message["actor_external_id"]) if message.get("actor_external_id") is not None else None,
        event_time=message["event_time"],
        content_type=message.get("content_type") or "text",
        content=message.get("content") or "",
        raw_payload=raw_payload,
        processed_status="pending",
    )
    db.add(episode)
    db.flush()
    return episode


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


def pending_episodes(db: Session, company_id: UUID, limit: int | None = None, source_type: str | None = None) -> list[Episode]:
    stmt = (
        select(Episode)
        .where(Episode.company_id == company_id, Episode.processed_status == "pending")
        .order_by(Episode.event_time.asc())
    )
    if source_type:
        stmt = stmt.where(Episode.source_type == source_type)
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def should_process_episode(content: str) -> bool:
    return classify_episode_relevance(content)["classification"] == BUSINESS_RELEVANT


def classify_episode_relevance(content: str) -> dict:
    text = content.strip()
    if is_noise_message(text):
        return {"classification": NOISE, "reason": "noise_or_acknowledgement"}
    low = text.lower()
    business_terms = [
        "approval",
        "approve",
        "assigned",
        "bottleneck",
        "business",
        "call",
        "client",
        "complain",
        "complaint",
        "company",
        "customer",
        "deadline",
        "discount",
        "employee",
        "exception",
        "follow-up",
        "invoice",
        "lead",
        "manager",
        "market",
        "objection",
        "payment",
        "policy",
        "price",
        "process",
        "refund",
        "reply",
        "responsible",
        "sale",
        "sales",
        "student",
        "support",
        "task",
        "workflow",
    ]
    if any(term in low for term in business_terms):
        return {"classification": BUSINESS_RELEVANT, "reason": "business_terms_present"}
    personal_terms = [
        "birthday",
        "bro",
        "coffee",
        "dinner",
        "family",
        "friend",
        "friends",
        "game",
        "holiday",
        "lunch",
        "movie",
        "party",
        "personal",
        "restaurant",
        "vacation",
        "weekend",
        "wedding",
    ]
    if any(term in low for term in personal_terms):
        return {"classification": PERSONAL_CHAT, "reason": "personal_terms_present"}
    if len(text) > 80:
        return {"classification": UNCLEAR_NEEDS_REVIEW, "reason": "long_message_without_business_signal"}
    return {"classification": PERSONAL_CHAT, "reason": "casual_message_without_business_signal"}


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
