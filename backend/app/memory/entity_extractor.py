import json

from app.llm.base import LLMProvider
from app.llm.prompts import ENTITY_EXTRACTION_SYSTEM
from app.llm.schemas import EntityExtractionResult
from app.llm.validation import validated_json_completion
from app.memory.episode_service import format_context_window
from app.models import Episode


class EntityExtractor:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def extract(self, episode: Episode, previous: list[Episode]) -> list[dict]:
        context = format_context_window(previous)
        user = (
            "Company context: Telegram-native business. Use only available context.\n"
            f"Actor: {episode.actor_name}\n"
            f"Timestamp: {episode.event_time.isoformat()}\n"
            f"Previous messages:\n{context}\n"
            f"Current message id: {episode.message_id}\n"
            f"Current message:\n{episode.content}"
        )
        result = await validated_json_completion(self.llm, EntityExtractionResult, ENTITY_EXTRACTION_SYSTEM, user)
        return validate_entities(result.model_dump())


def validate_entities(data: dict | str) -> list[dict]:
    if isinstance(data, str):
        data = json.loads(data)
    entities = data.get("entities", [])
    clean = []
    for entity in entities:
        name = str(entity.get("name", "")).strip()
        entity_type = str(entity.get("type") or entity.get("entity_type") or "unknown").strip().lower()
        if name and entity_type:
            clean.append({
                "name": name,
                "type": entity_type,
                "summary": str(entity.get("summary", "")).strip(),
            })
    return clean
