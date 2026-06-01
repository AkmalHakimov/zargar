from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Company
from app.schemas import CompanyCreate, CompanyRead

router = APIRouter(prefix="/companies", tags=["companies"])


@router.post("", response_model=CompanyRead)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    company = Company(name=payload.name, industry=payload.industry)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company

