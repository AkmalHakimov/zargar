from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyCreate(BaseModel):
    name: str
    industry: str | None = None


class CompanyRead(OrmModel):
    id: UUID
    name: str
    industry: str | None
    created_at: datetime


class TelegramImportResponse(BaseModel):
    source_id: UUID
    imported: int
    skipped: int


class ProcessResponse(BaseModel):
    processed: int
    skipped: int = 0


class ContextSearchRequest(BaseModel):
    query: str
    time_mode: str = "current"
    start_date: datetime | None = None
    end_date: datetime | None = None
    limit: int = 20


class ContextSearchResponse(BaseModel):
    context: str
    facts: list[dict]
    entities: list[dict]
    communities: list[dict]
    sources: list[dict]


class AgentRunRequest(BaseModel):
    query: str = ""
    start_date: datetime | None = None
    end_date: datetime | None = None


class AgentRunResponse(BaseModel):
    output: dict
    retrieved_context: dict
