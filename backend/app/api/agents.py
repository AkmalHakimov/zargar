from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents import BottleneckAgent, FounderReportAgent, MemoryQAAgent
from app.db import get_db
from app.schemas import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/companies/{company_id}/agents", tags=["agents"])


@router.post("/memory-qa/run", response_model=AgentRunResponse)
async def run_memory_qa(company_id: UUID, payload: AgentRunRequest, db: Session = Depends(get_db)):
    result = await MemoryQAAgent().run(db, company_id, payload.query)
    return AgentRunResponse(output={"answer": result["answer"]}, retrieved_context=result["retrieved_context"])


@router.post("/founder-report/run", response_model=AgentRunResponse)
async def run_founder_report(company_id: UUID, payload: AgentRunRequest, db: Session = Depends(get_db)):
    result = await FounderReportAgent().run(db, company_id, payload.start_date, payload.end_date)
    return AgentRunResponse(output={"report": result["report"]}, retrieved_context=result["retrieved_context"])


@router.post("/bottleneck/run", response_model=AgentRunResponse)
async def run_bottlenecks(company_id: UUID, payload: AgentRunRequest, db: Session = Depends(get_db)):
    result = await BottleneckAgent().run(db, company_id, payload.start_date, payload.end_date)
    return AgentRunResponse(output={"bottlenecks": result["bottlenecks"]}, retrieved_context=result["retrieved_context"])

