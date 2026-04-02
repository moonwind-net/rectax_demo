from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import get_current_user
from ..models.user import ClientCompany, User
from ..schemas.receipt import CompanyTaxRuleResponse, CompanyTaxRuleUpdate
from ..services.tax_rules import get_company_tax_config

router = APIRouter()


@router.get("/companies", response_model=list[CompanyTaxRuleResponse])
async def list_company_tax_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    companies = (
        await db.execute(select(ClientCompany).order_by(ClientCompany.id.asc()))
    ).scalars().all()
    return [
        CompanyTaxRuleResponse(
            id=company.id,
            accounting_firm_id=company.accounting_firm_id,
            code=company.code,
            name=company.name,
            registration_number=company.registration_number,
            is_active=company.is_active,
            jpy_rounding_mode=get_company_tax_config(company).jpy_rounding_mode,
            tax_rounding_level=get_company_tax_config(company).tax_rounding_level,
        )
        for company in companies
    ]


@router.put("/companies/{company_id}/tax-rules", response_model=CompanyTaxRuleResponse)
async def update_company_tax_rules(
    company_id: int,
    payload: CompanyTaxRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = await db.get(ClientCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Client company not found")

    company.jpy_rounding_mode = payload.jpy_rounding_mode
    company.tax_rounding_level = payload.tax_rounding_level
    await db.commit()
    await db.refresh(company)

    config = get_company_tax_config(company)
    return CompanyTaxRuleResponse(
        id=company.id,
        accounting_firm_id=company.accounting_firm_id,
        code=company.code,
        name=company.name,
        registration_number=company.registration_number,
        is_active=company.is_active,
        jpy_rounding_mode=config.jpy_rounding_mode,
        tax_rounding_level=config.tax_rounding_level,
    )