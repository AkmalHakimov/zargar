from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import ProcessResponse
from app.workers.process_backfill import process_backfill
from app.workers.process_episode import process_episode_by_id

router = APIRouter(prefix="/companies/{company_id}", tags=["processing"])


@router.post("/process/backfill", response_model=ProcessResponse)
async def process_company_backfill(company_id: UUID, db: Session = Depends(get_db)):
    processed, skipped = await process_backfill(db, company_id)
    return ProcessResponse(processed=processed, skipped=skipped)


@router.post("/episodes/{episode_id}/process", response_model=ProcessResponse)
async def process_one_episode(company_id: UUID, episode_id: UUID, db: Session = Depends(get_db)):
    await process_episode_by_id(db, company_id, episode_id)
    db.commit()
    return ProcessResponse(processed=1)

