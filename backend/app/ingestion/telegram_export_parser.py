import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ParsedTelegramMessage:
    chat_id: str
    chat_title: str | None
    message_id: str
    actor_name: str | None
    actor_external_id: str | None
    event_time: datetime
    content_type: str
    content: str
    reply_to_message_id: str | None
    forwarded_from: str | None
    edited_at: datetime | None
    media_metadata: dict[str, Any]
    raw_payload: dict[str, Any]


def load_export(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(value)


def extract_forwarded_from(message: dict[str, Any]) -> str | None:
    for key in ("forwarded_from", "forwarded_from_id", "forwarded_from_chat", "forwarded_from_name"):
        value = message.get(key)
        if value:
            return str(value)
    return None


def extract_reply_to_message_id(message: dict[str, Any]) -> str | None:
    value = message.get("reply_to_message_id")
    if value is None:
        value = message.get("reply_to_msg_id")
    if value is None and isinstance(message.get("reply_to_message"), dict):
        value = message["reply_to_message"].get("id")
    return str(value) if value is not None else None


def extract_media_metadata(message: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "media_type",
        "mime_type",
        "file",
        "thumbnail",
        "photo",
        "width",
        "height",
        "duration_seconds",
        "location_information",
        "contact_information",
        "poll",
    ]
    return {key: message[key] for key in keys if key in message and message[key] is not None}


def media_placeholder(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    media_type = metadata.get("media_type") or ("file" if metadata.get("file") else "media")
    file_name = metadata.get("file")
    return f"[{media_type}: {file_name}]" if file_name else f"[{media_type}]"


def parse_telegram_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def should_skip_message(message: dict[str, Any], content: str, media_metadata: dict[str, Any]) -> bool:
    if message.get("type") != "message":
        return True
    if content.strip() or media_metadata:
        return False
    return True


def parse_messages(export: dict[str, Any]) -> list[ParsedTelegramMessage]:
    chat_title = export.get("name") or export.get("title")
    chat_id = str(export.get("id") or export.get("chat_id") or chat_title or "telegram_export")
    parsed: list[ParsedTelegramMessage] = []
    for message in export.get("messages", []):
        content = normalize_text(message.get("text") or message.get("caption") or message.get("file_name"))
        media_metadata = extract_media_metadata(message)
        if should_skip_message(message, content, media_metadata):
            continue
        if not content.strip():
            content = media_placeholder(media_metadata)
        content_type = "file_caption" if media_metadata else "text"
        edited_at = parse_telegram_datetime(message["edited"]) if message.get("edited") else None
        reply_to_message_id = extract_reply_to_message_id(message)
        forwarded_from = extract_forwarded_from(message)
        raw_payload = {
            **message,
            "reply_to_message_id": reply_to_message_id,
            "forwarded_from": forwarded_from,
            "edited_at": edited_at.isoformat() if edited_at else None,
            "media_metadata": media_metadata,
        }
        parsed.append(
            ParsedTelegramMessage(
                chat_id=chat_id,
                chat_title=chat_title,
                message_id=str(message["id"]),
                actor_name=message.get("from"),
                actor_external_id=str(message.get("from_id")) if message.get("from_id") else None,
                event_time=parse_telegram_datetime(message["date"]),
                content_type=content_type,
                content=content.strip(),
                reply_to_message_id=reply_to_message_id,
                forwarded_from=forwarded_from,
                edited_at=edited_at,
                media_metadata=media_metadata,
                raw_payload=raw_payload,
            )
        )
    parsed.sort(key=lambda item: item.event_time)
    return parsed
