from datetime import datetime

from app.llm.base import LLMProvider
from app.llm.prompts import TEMPORAL_RESOLUTION_SYSTEM
from app.llm.schemas import TemporalResolutionResult
from app.llm.validation import validated_json_completion
from app.memory.episode_service import format_context_window
from app.models import Episode


class TemporalResolver:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def resolve(self, episode: Episode, fact: dict, previous: list[Episode]) -> dict:
        context = format_context_window(previous)
        user = (
            "Company context: Telegram-native business. Use only available context.\n"
            f"Episode timestamp: {episode.event_time.isoformat()}\n"
            f"Current message id: {episode.message_id}\n"
            f"Fact text: {fact['fact_text']}\n"
            f"Message context:\n{context}\n{episode.content}"
        )
        result = await validated_json_completion(self.llm, TemporalResolutionResult, TEMPORAL_RESOLUTION_SYSTEM, user)
        data = result.model_dump()
        return {
            "valid_at": parse_dt(data.get("valid_at")) or episode.event_time,
            "invalid_at": parse_dt(data.get("invalid_at")),
            "temporal_reasoning": data.get("temporal_reasoning"),
        }


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
