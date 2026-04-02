from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import CurrentCompanyContext, get_current_company, get_current_user
from ..models.category import AccountSubject, ClassificationRule
from ..models.user import User

router = APIRouter()


# ─────────────────────────── Schemas ────────────────────────────

class RuleCreateRequest(BaseModel):
    rule_name: str = Field(min_length=1, max_length=255)
    rule_type: str = Field(pattern=r"^(keyword|merchant_exact|amount_range|regex)$")
    rule_condition: dict
    target_subject_id: int
    priority: int = Field(ge=1, le=9999, default=100)
    score: float = Field(ge=0.0, le=1.0, default=1.0)


class RuleUpdateRequest(BaseModel):
    rule_name: str | None = Field(None, min_length=1, max_length=255)
    rule_type: str | None = Field(None, pattern=r"^(keyword|merchant_exact|amount_range|regex)$")
    rule_condition: dict | None = None
    target_subject_id: int | None = None
    priority: int | None = Field(None, ge=1, le=9999)
    score: float | None = Field(None, ge=0.0, le=1.0)
    is_active: bool | None = None


class RulePriorityItem(BaseModel):
    id: int
    priority: int = Field(ge=1, le=9999)


# ─────────────────────────── Account Subjects ────────────────────


@router.get("/subjects")
async def list_subjects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    stmt = (
        select(AccountSubject)
        .where(
            AccountSubject.is_active.is_(True),
            AccountSubject.accounting_firm_id == current_company.accounting_firm_id,
            AccountSubject.client_company_id == current_company.company_id,
        )
        .order_by(AccountSubject.subject_code.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": s.id,
            "subject_code": s.subject_code,
            "subject_name": s.subject_name,
            "subject_type": s.subject_type,
            "tax_treatment": s.tax_treatment,
            "default_tax_rate": float(s.default_tax_rate) if s.default_tax_rate is not None else None,
        }
        for s in rows
    ]


# ─────────────────────────── Classification Rules ────────────────


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    stmt = (
        select(ClassificationRule)
        .where(
            ClassificationRule.accounting_firm_id == current_company.accounting_firm_id,
            ClassificationRule.client_company_id == current_company.company_id,
        )
        .order_by(ClassificationRule.priority.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "rule_name": r.rule_name,
            "rule_type": r.rule_type,
            "rule_condition": r.rule_condition,
            "target_subject_id": r.target_subject_id,
            "priority": r.priority,
            "score": float(r.score),
            "is_active": r.is_active,
        }
        for r in rows
    ]


@router.post("/rules", status_code=201)
async def create_rule(
    body: RuleCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    subject = await db.get(AccountSubject, body.target_subject_id)
    if not subject:
        raise HTTPException(status_code=400, detail="target_subject_id does not exist")
    if subject.client_company_id != current_company.company_id:
        raise HTTPException(status_code=400, detail="target_subject_id does not belong to current company")

    rule = ClassificationRule(
        accounting_firm_id=current_company.accounting_firm_id,
        client_company_id=current_company.company_id,
        rule_name=body.rule_name,
        rule_type=body.rule_type,
        rule_condition=body.rule_condition,
        target_subject_id=body.target_subject_id,
        priority=body.priority,
        score=body.score,
        is_active=True,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"id": rule.id, "rule_name": rule.rule_name, "priority": rule.priority}


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: RuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    rule = await db.get(ClassificationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.rule_name is not None:
        rule.rule_name = body.rule_name
    if body.rule_type is not None:
        rule.rule_type = body.rule_type
    if body.rule_condition is not None:
        rule.rule_condition = body.rule_condition
    if body.target_subject_id is not None:
        subject = await db.get(AccountSubject, body.target_subject_id)
        if not subject:
            raise HTTPException(status_code=400, detail="target_subject_id does not exist")
        if subject.client_company_id != current_company.company_id:
            raise HTTPException(status_code=400, detail="target_subject_id does not belong to current company")
        rule.target_subject_id = body.target_subject_id
    if body.priority is not None:
        rule.priority = body.priority
    if body.score is not None:
        rule.score = body.score
    if body.is_active is not None:
        rule.is_active = body.is_active

    await db.commit()
    await db.refresh(rule)
    return {
        "id": rule.id,
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    rule = await db.get(ClassificationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()


@router.patch("/rules/reorder")
async def reorder_rules(
    items: list[RulePriorityItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    """Bulk-update priorities for drag-and-drop reordering. Accepts [{id, priority}]."""
    if len(items) > 200:
        raise HTTPException(status_code=400, detail="Too many items in batch")
    for item in items:
        await db.execute(
            update(ClassificationRule)
            .where(ClassificationRule.id == item.id)
            .where(ClassificationRule.client_company_id == current_company.company_id)
            .values(priority=item.priority)
        )
    await db.commit()
    return {"updated": len(items)}

