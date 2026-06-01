import json

from app.llm.base import LLMProvider
from app.llm.prompts import FACT_EXTRACTION_SYSTEM
from app.llm.schemas import FactExtractionResult
from app.llm.validation import validated_json_completion
from app.memory.episode_service import format_context_window
from app.models import Entity, Episode


class FactExtractor:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def extract(self, episode: Episode, previous: list[Episode], entities: list[Entity]) -> list[dict]:
        context = format_context_window(previous)
        resolved = "\n".join(f"- {entity.name} ({entity.entity_type}): {entity.summary or ''}" for entity in entities)
        user = (
            "Company context: Telegram-native business. Use only available context.\n"
            f"Previous messages:\n{context}\n"
            f"Actor: {episode.actor_name}\n"
            f"Timestamp: {episode.event_time.isoformat()}\n"
            f"Current message id: {episode.message_id}\n"
            f"Current message:\n{episode.content}\n"
            f"Resolved entities:\n{resolved}"
        )
        result = await validated_json_completion(self.llm, FactExtractionResult, FACT_EXTRACTION_SYSTEM, user)
        return validate_facts(result.model_dump())


def validate_facts(data: dict | str) -> list[dict]:
    if isinstance(data, str):
        data = json.loads(data)
    facts = data.get("facts", [])
    clean = []
    for fact in facts:
        source = str(fact.get("source_entity", "")).strip()
        target = str(fact.get("target_entity", "")).strip()
        relation = str(fact.get("relation_type", "")).strip().upper()
        text = str(fact.get("fact_text", "")).strip()
        if source and target and relation and text:
            clean.append({
                "source_entity": source,
                "target_entity": target,
                "relation_type": relation,
                "fact_text": text,
                "fact_type": normalize_fact_type(str(fact.get("fact_type", ""))),
                "confidence": float(fact.get("confidence", 0.5)),
                "supporting_message_ids": [str(item) for item in fact.get("supporting_message_ids", [])],
            })
    return clean


def normalize_fact_type(value: str) -> str:
    allowed = {
        "policy",
        "decision",
        "complaint",
        "task",
        "bottleneck",
        "workflow",
        "responsibility",
        "payment_issue",
        "customer_objection",
    }
    normalized = value.strip().lower()
    return normalized if normalized in allowed else "workflow"
