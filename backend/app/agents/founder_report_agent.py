from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.openai_provider import build_llm_provider
from app.llm.prompts import FOUNDER_REPORT_SYSTEM
from app.memory.context_retriever import ContextRetriever


class FounderReportAgent:
    async def run(self, db: Session, company_id: UUID, start_date: datetime | None, end_date: datetime | None) -> dict:
        retrieved = ContextRetriever().search(
            db,
            company_id,
            query="decisions tasks complaints risks policy changes bottlenecks",
            time_mode="historical",
            start_date=start_date,
            end_date=end_date,
            limit=50,
        )
        llm = build_llm_provider(get_settings())
        report = await llm.text_completion(FOUNDER_REPORT_SYSTEM, retrieved["context"])
        return {"report": report, "retrieved_context": retrieved}

