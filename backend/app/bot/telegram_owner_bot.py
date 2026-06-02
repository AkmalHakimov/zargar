import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import BottleneckAgent, FounderReportAgent, MemoryQAAgent
from app.config import Settings
from app.memory.episode_service import BUSINESS_RELEVANT, import_live_telegram_message
from app.models import Episode, Fact

logger = logging.getLogger(__name__)

UNAUTHORIZED_MESSAGE = "You are not authorized to use this Zargar company brain."
NO_MEMORY_MESSAGE = "No company memory found for this company yet. Import and process Telegram history first."
TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 3500

QUESTION_COMMANDS = {
    "/policies": "What are our current policies?",
    "/decisions_week": "What important decisions were made this week?",
    "/complaints_week": "What complaints repeated this week?",
    "/tasks_open": "What tasks are open?",
}

HELP_TEXT = """Zargar company brain commands:
/ask <question>
/report_today
/report_week
/decisions_week
/complaints_week
/tasks_open
/policies
/bottlenecks_week
/status"""


class TelegramOwnerBot:
    """Owner/manager-only Telegram bot. It never replies to customer chats automatically."""

    def __init__(
        self,
        company_id: UUID,
        db_session_factory: Callable[[], Session],
        allowed_user_ids: set[int],
        allowed_chat_ids: set[str] | None = None,
        qa_agent: MemoryQAAgent | None = None,
        report_agent: FounderReportAgent | None = None,
        bottleneck_agent: BottleneckAgent | None = None,
    ) -> None:
        self.company_id = company_id
        self.db_session_factory = db_session_factory
        self.allowed_user_ids = allowed_user_ids
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.qa_agent = qa_agent or MemoryQAAgent()
        self.report_agent = report_agent or FounderReportAgent()
        self.bottleneck_agent = bottleneck_agent or BottleneckAgent()

    def is_authorized(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.allowed_user_ids

    async def handle_message(self, user_id: int | None, text: str | None) -> list[str]:
        if not self.is_authorized(user_id):
            return [UNAUTHORIZED_MESSAGE]

        command, argument = parse_command(text or "")
        try:
            if command == "/start":
                return split_telegram_message("Zargar company brain is ready.\n\n" + HELP_TEXT)
            if command == "/help":
                return split_telegram_message(HELP_TEXT)
            if command == "/ask":
                if not argument:
                    return ["Usage: /ask <question>"]
                return await self._run_memory_question(argument)
            if command in QUESTION_COMMANDS:
                return await self._run_memory_question(QUESTION_COMMANDS[command])
            if command == "/report_today":
                return await self._run_report("today")
            if command == "/report_week":
                return await self._run_report("week")
            if command == "/bottlenecks_week":
                return await self._run_bottlenecks()
            if command == "/status":
                return self._status()
            return split_telegram_message("Unknown command.\n\n" + HELP_TEXT)
        except Exception:
            logger.exception("Telegram owner bot command failed: %s", command)
            return ["Zargar could not answer that right now. The error was logged."]

    def handle_group_message(self, message: dict) -> bool:
        chat_id = str(message.get("chat_id") or "")
        if chat_id not in self.allowed_chat_ids:
            return False
        try:
            with self.db_session_factory() as db:
                episode = import_live_telegram_message(db, self.company_id, message)
                db.commit()
                return episode is not None
        except Exception:
            logger.exception("Telegram live group ingestion failed for chat_id=%s", chat_id)
            return False

    async def _run_memory_question(self, question: str) -> list[str]:
        with self.db_session_factory() as db:
            if not has_company_memory(db, self.company_id):
                return [NO_MEMORY_MESSAGE]
            result = await self.qa_agent.run(db, self.company_id, question)
            text = normalize_sections(result.get("answer") or "")
            return split_telegram_message(text)

    async def _run_report(self, period: str) -> list[str]:
        start, end = period_window(period)
        with self.db_session_factory() as db:
            result = await self.report_agent.run(db, self.company_id, start, end)
            text = result.get("report") or ""
            return split_telegram_message(text)

    async def _run_bottlenecks(self) -> list[str]:
        start, end = period_window("week")
        with self.db_session_factory() as db:
            result = await self.bottleneck_agent.run(db, self.company_id, start, end)
            text = result.get("bottlenecks") or ""
            return split_telegram_message(text)

    def _status(self) -> list[str]:
        with self.db_session_factory() as db:
            imported = db.scalar(select(func.count()).select_from(Episode).where(Episode.company_id == self.company_id)) or 0
            pending = (
                db.scalar(
                    select(func.count())
                    .select_from(Episode)
                    .where(Episode.company_id == self.company_id, Episode.processed_status == "pending")
                )
                or 0
            )
            processed = (
                db.scalar(
                    select(func.count())
                    .select_from(Episode)
                    .where(Episode.company_id == self.company_id, Episode.processed_status == "processed")
                )
                or 0
            )
            active_facts = (
                db.scalar(
                    select(func.count())
                    .select_from(Fact)
                    .where(Fact.company_id == self.company_id, Fact.status == "active")
                )
                or 0
            )
            last_message_time = db.scalar(select(func.max(Episode.event_time)).where(Episode.company_id == self.company_id))
            skipped_personal = count_episode_status(db, self.company_id, "skipped_personal")
            unclear_needs_review = count_episode_status(db, self.company_id, "unclear_needs_review")
            business_relevant = count_relevance(db, self.company_id, BUSINESS_RELEVANT)
        return [
            "\n".join(
                [
                    "Status:",
                    f"- imported episodes: {imported}",
                    f"- pending episodes: {pending}",
                    f"- processed episodes: {processed}",
                    f"- active facts: {active_facts}",
                    f"- skipped personal: {skipped_personal}",
                    f"- business relevant: {business_relevant}",
                    f"- unclear needs review: {unclear_needs_review}",
                    f"- last message time: {last_message_time.isoformat() if last_message_time else 'none'}",
                ]
            )
        ]


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    allowed: set[int] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        allowed.add(int(item))
    return allowed


def parse_allowed_chat_ids(raw: str | None) -> set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def parse_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    first, _, rest = stripped.partition(" ")
    command = first.split("@", 1)[0].lower()
    return command, rest.strip()


def has_company_memory(db: Session, company_id: UUID) -> bool:
    count = db.scalar(select(func.count()).select_from(Fact).where(Fact.company_id == company_id)) or 0
    return count > 0


def count_episode_status(db: Session, company_id: UUID, status: str) -> int:
    return db.scalar(select(func.count()).select_from(Episode).where(Episode.company_id == company_id, Episode.processed_status == status)) or 0


def count_relevance(db: Session, company_id: UUID, classification: str) -> int:
    return sum(
        1
        for episode in db.scalars(select(Episode).where(Episode.company_id == company_id))
        if (episode.raw_payload or {}).get("relevance_classification") == classification
    )


def period_window(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=7)
    return start, now


def normalize_sections(text: str) -> str:
    return text.replace("Historical/outdated facts:", "Historical/outdated:")


def format_agent_output(answer: str, retrieved: dict) -> str:
    facts = retrieved.get("facts") or []
    active = [fact for fact in facts if fact.get("status") == "active"]
    historical = [fact for fact in facts if fact.get("status") != "active" or fact.get("invalid_at")]
    lines = ["Answer:", answer.strip() or "- No answer was generated.", "", "Current facts:"]
    lines.extend(format_fact_lines(active))
    lines.extend(["", "Historical/outdated:"])
    lines.extend(format_fact_lines(historical))
    lines.extend(["", "Evidence:"])
    lines.extend(format_source_lines(retrieved.get("sources") or []))
    return "\n".join(lines)


def format_fact_lines(facts: list[dict]) -> list[str]:
    if not facts:
        return ["- none"]
    return [f"- [{fact.get('fact_type', 'fact')}] {fact.get('fact_text', '')}" for fact in facts[:8]]


def format_source_lines(sources: list[dict]) -> list[str]:
    if not sources:
        return ["- none"]
    seen = set()
    lines = []
    for source in sources:
        key = (source.get("chat_title"), source.get("actor"), source.get("message_id"), source.get("event_time"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"- {source.get('chat_title') or 'unknown chat'}, {source.get('actor') or 'unknown actor'}, "
            f"msg {source.get('message_id') or 'unknown'}, {source.get('event_time') or 'unknown time'}"
        )
    return lines or ["- none"]


def split_telegram_message(text: str, limit: int = SAFE_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


async def run_polling_bot(company_id: UUID, settings: Settings, db_session_factory: Callable[[], Session]) -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required to run the Telegram owner bot.")
    allowed_user_ids = parse_allowed_user_ids(settings.telegram_allowed_user_ids)
    if not allowed_user_ids:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is required to run the Telegram owner bot.")
    allowed_chat_ids = parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)

    try:
        from aiogram import Bot, Dispatcher, types
    except ImportError as exc:
        raise RuntimeError('Install the bot extra first: pip install -e ".[bot]"') from exc

    owner_bot = TelegramOwnerBot(company_id, db_session_factory, allowed_user_ids, allowed_chat_ids=allowed_chat_ids)
    telegram_bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()

    @dispatcher.message()
    async def handle(message: types.Message) -> None:
        if message.chat.type in {"group", "supergroup"}:
            owner_bot.handle_group_message(aiogram_message_to_live_payload(message))
            return
        user_id = message.from_user.id if message.from_user else None
        responses = await owner_bot.handle_message(user_id, message.text)
        for response in responses:
            await message.answer(response[:TELEGRAM_MESSAGE_LIMIT])

    logger.info("Starting Telegram owner bot for company_id=%s", company_id)
    await dispatcher.start_polling(telegram_bot)


def aiogram_message_to_live_payload(message) -> dict:
    raw_payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else {}
    sender = message.from_user
    actor_name = None
    if sender:
        actor_name = getattr(sender, "full_name", None) or getattr(sender, "username", None) or str(sender.id)
    return {
        "chat_id": str(message.chat.id),
        "chat_title": getattr(message.chat, "title", None),
        "message_id": str(message.message_id),
        "actor_name": actor_name,
        "actor_external_id": str(sender.id) if sender else None,
        "event_time": message.date,
        "content_type": "text" if message.text else "caption" if message.caption else "non_text",
        "content": message.text or message.caption or "",
        "reply_to_message_id": str(message.reply_to_message.message_id) if message.reply_to_message else None,
        "raw_payload": raw_payload,
    }
