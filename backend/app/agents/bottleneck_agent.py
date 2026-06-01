from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.openai_provider import build_llm_provider
from app.llm.prompts import BOTTLENECK_SYSTEM
from app.memory.context_retriever import ContextRetriever


class BottleneckAgent:
    async def run(self, db: Session, company_id: UUID, start_date: datetime | None, end_date: datetime | None) -> dict:
        retrieved = ContextRetriever().search(
            db,
            company_id,
            query="late reply payment approval price objection dropped complaint bottleneck delay",
            time_mode="historical",
            start_date=start_date,
            end_date=end_date,
            limit=50,
        )
        llm = build_llm_provider(get_settings())
        output = await llm.text_completion(BOTTLENECK_SYSTEM, retrieved["context"])
        return {"bottlenecks": output, "retrieved_context": retrieved}

