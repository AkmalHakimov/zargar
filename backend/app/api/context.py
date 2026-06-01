from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.memory.context_retriever import ContextRetriever
from app.schemas import ContextSearchRequest, ContextSearchResponse

router = APIRouter(prefix="/companies/{company_id}/context", tags=["context"])


@router.post("/search", response_model=ContextSearchResponse)
def search_context(company_id: UUID, payload: ContextSearchRequest, db: Session = Depends(get_db)):
    return ContextRetriever().search(
        db,
        company_id,
        query=payload.query,
        time_mode=payload.time_mode,
        start_date=payload.start_date,
        end_date=payload.end_date,
        limit=payload.limit,
    )

