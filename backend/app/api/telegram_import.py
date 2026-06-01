import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.telegram_export_parser import load_export, parse_messages
from app.memory.episode_service import create_telegram_export_source, import_telegram_messages
from app.schemas import TelegramImportResponse

router = APIRouter(prefix="/companies/{company_id}/sources", tags=["telegram-import"])


@router.post("/telegram-export", response_model=TelegramImportResponse)
async def upload_telegram_export(company_id: UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(await file.read())
        path = Path(tmp.name)
    data = load_export(path)
    messages = parse_messages(data)
    source = create_telegram_export_source(
        db,
        company_id=company_id,
        source_name=data.get("name") or file.filename or "Telegram export",
        config={"filename": file.filename, "message_count": len(messages)},
    )
    imported, skipped = import_telegram_messages(db, company_id, source, messages)
    db.commit()
    path.unlink(missing_ok=True)
    return TelegramImportResponse(source_id=source.id, imported=imported, skipped=skipped)

