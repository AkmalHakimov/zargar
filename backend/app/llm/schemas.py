from pydantic import BaseModel, ConfigDict, Field


class EntityCandidate(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    summary: str = ""


class EntityExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entities: list[EntityCandidate] = Field(default_factory=list)


class FactCandidate(BaseModel):
    source_entity: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    fact_text: str = Field(min_length=1)
    fact_type: str = "workflow"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    supporting_message_ids: list[str] = Field(default_factory=list)


class FactExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[FactCandidate] = Field(default_factory=list)


class TemporalResolutionResult(BaseModel):
    valid_at: str | None = None
    invalid_at: str | None = None
    temporal_reasoning: str = ""


class FactResolutionResult(BaseModel):
    decision: str
    facts_to_invalidate: list[str] = Field(default_factory=list)
    reason: str = ""
