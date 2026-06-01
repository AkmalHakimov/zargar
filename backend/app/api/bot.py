from uuid import UUID

from fastapi import APIRouter

router = APIRouter(prefix="/companies/{company_id}/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(company_id: UUID, payload: dict):
    return {"status": "accepted", "company_id": str(company_id), "note": "Live bot ingestion skeleton; export processing is the first MVP path."}

