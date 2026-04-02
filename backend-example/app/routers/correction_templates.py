import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import get_current_user
from ..models.receipt import CorrectionTemplate
from ..models.user import User
from ..schemas.receipt import (
    CorrectionTemplateCreate,
    CorrectionTemplateResponse,
    CorrectionTemplateUpdate,
)

router = APIRouter()


@router.get("", response_model=list[CorrectionTemplateResponse])
async def list_correction_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(CorrectionTemplate).order_by(
                CorrectionTemplate.priority.desc(), CorrectionTemplate.id.asc()
            )
        )
    ).scalars().all()
    return [CorrectionTemplateResponse.model_validate(r) for r in rows]


@router.post("", response_model=CorrectionTemplateResponse, status_code=201)
async def create_correction_template(
    body: CorrectionTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        await db.execute(
            select(CorrectionTemplate).where(CorrectionTemplate.template_key == body.template_key)
        )
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail=f"template_key '{body.template_key}' already exists")

    if body.merchant_pattern:
        try:
            re.compile(body.merchant_pattern)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"Invalid merchant_pattern regex: {exc}") from exc

    row = CorrectionTemplate(
        template_key=body.template_key,
        label=body.label,
        merchant_pattern=body.merchant_pattern,
        patch_fields=body.patch_fields,
        note_prefix=body.note_prefix,
        priority=body.priority,
        is_active=body.is_active,
        created_by=current_user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CorrectionTemplateResponse.model_validate(row)


@router.put("/{template_id}", response_model=CorrectionTemplateResponse)
async def update_correction_template(
    template_id: int,
    body: CorrectionTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = await db.get(CorrectionTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Correction template not found")

    if body.merchant_pattern is not None:
        try:
            re.compile(body.merchant_pattern)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"Invalid merchant_pattern regex: {exc}") from exc

    if body.label is not None:
        row.label = body.label
    if body.merchant_pattern is not None:
        row.merchant_pattern = body.merchant_pattern
    if body.patch_fields is not None:
        row.patch_fields = body.patch_fields
    if body.note_prefix is not None:
        row.note_prefix = body.note_prefix
    if body.priority is not None:
        row.priority = body.priority
    if body.is_active is not None:
        row.is_active = body.is_active

    await db.commit()
    await db.refresh(row)
    return CorrectionTemplateResponse.model_validate(row)


@router.delete("/{template_id}", status_code=204)
async def delete_correction_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = await db.get(CorrectionTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Correction template not found")
    await db.delete(row)
    await db.commit()
