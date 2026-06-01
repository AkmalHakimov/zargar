from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.openai_provider import build_llm_provider
from app.llm.prompts import SOP_SYSTEM
from app.memory.context_retriever import ContextRetriever


class SOPAgent:
    async def run(self, db: Session, company_id: UUID, topic: str) -> dict:
        retrieved = ContextRetriever().search(db, company_id, query=topic, time_mode="current", limit=30)
        llm = build_llm_provider(get_settings())
        sop = await llm.text_completion(SOP_SYSTEM, f"Topic: {topic}\n\n{retrieved['context']}")
        return {"draft_sop": sop, "retrieved_context": retrieved}

