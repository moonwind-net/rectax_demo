from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import CurrentCompanyContext, get_current_company, get_current_user
from ..models.category import ExportFile, ExportTemplate
from ..models.user import User
from ..schemas.receipt import ExportCreateRequest, ExportCreateResponse
from ..services.export_service import ExportService

router = APIRouter()


# ─────────────────────────── Template Schemas ───────────────────

class ColumnMappingItem(BaseModel):
    header: str = Field(min_length=1, max_length=100)
    field: str = Field(min_length=1, max_length=100)


class TemplateCreateRequest(BaseModel):
    template_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")
    template_name: str = Field(min_length=1, max_length=255)
    format_type: str = Field(default="csv", pattern=r"^(csv)$")
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = Field(default="UTF-8", pattern=r"^(UTF-8|Shift_JIS|CP932)$")
    columns: list[ColumnMappingItem] = Field(min_length=1)
    is_default: bool = False


class TemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(None, min_length=1, max_length=255)
    delimiter: str | None = Field(None, min_length=1, max_length=1)
    encoding: str | None = Field(None, pattern=r"^(UTF-8|Shift_JIS|CP932)$")
    columns: list[ColumnMappingItem] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


# ─────────────────────────── Template Endpoints ─────────────────

@router.get("/templates")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    stmt = (
        select(ExportTemplate)
        .where(ExportTemplate.accounting_firm_id == current_company.accounting_firm_id)
        .where(ExportTemplate.is_active.is_(True))
        .order_by(ExportTemplate.is_default.desc(), ExportTemplate.template_code.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": t.id,
            "template_code": t.template_code,
            "template_name": t.template_name,
            "encoding": t.encoding,
            "delimiter": t.delimiter,
            "columns": t.mapping_json.get("columns", []),
            "is_default": t.is_default,
        }
        for t in rows
    ]


@router.get("/templates/{template_id}")
async def get_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    tmpl = await db.get(ExportTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return {
        "id": tmpl.id,
        "template_code": tmpl.template_code,
        "template_name": tmpl.template_name,
        "format_type": tmpl.format_type,
        "encoding": tmpl.encoding,
        "delimiter": tmpl.delimiter,
        "columns": tmpl.mapping_json.get("columns", []),
        "is_default": tmpl.is_default,
        "is_active": tmpl.is_active,
    }


@router.post("/templates", status_code=201)
async def create_template(
    body: TemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    # Ensure template_code is unique within firm
    existing_stmt = select(ExportTemplate).where(
        ExportTemplate.accounting_firm_id == current_company.accounting_firm_id,
        ExportTemplate.template_code == body.template_code,
    )
    if (await db.execute(existing_stmt)).scalars().first():
        raise HTTPException(status_code=400, detail="template_code already exists for this firm")

    if body.is_default:
        await _clear_default_flag(db, current_company.accounting_firm_id)

    tmpl = ExportTemplate(
        accounting_firm_id=current_company.accounting_firm_id,
        template_code=body.template_code,
        template_name=body.template_name,
        format_type=body.format_type,
        delimiter=body.delimiter,
        encoding=body.encoding,
        mapping_json={"columns": [c.model_dump() for c in body.columns]},
        is_default=body.is_default,
        is_active=True,
    )
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    return {"id": tmpl.id, "template_code": tmpl.template_code, "template_name": tmpl.template_name}


@router.put("/templates/{template_id}")
async def update_template(
    template_id: int,
    body: TemplateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    tmpl = await db.get(ExportTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")

    if body.template_name is not None:
        tmpl.template_name = body.template_name
    if body.delimiter is not None:
        tmpl.delimiter = body.delimiter
    if body.encoding is not None:
        tmpl.encoding = body.encoding
    if body.columns is not None:
        tmpl.mapping_json = {"columns": [c.model_dump() for c in body.columns]}
    if body.is_active is not None:
        tmpl.is_active = body.is_active
    if body.is_default is True:
        await _clear_default_flag(db, current_company.accounting_firm_id)
        tmpl.is_default = True
    elif body.is_default is False:
        tmpl.is_default = False

    await db.commit()
    await db.refresh(tmpl)
    return {"id": tmpl.id, "template_name": tmpl.template_name, "is_default": tmpl.is_default}


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tmpl = await db.get(ExportTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(tmpl)
    await db.commit()


async def _clear_default_flag(db: AsyncSession, accounting_firm_id: int) -> None:
    from sqlalchemy import update
    await db.execute(
        update(ExportTemplate)
        .where(ExportTemplate.accounting_firm_id == accounting_firm_id)
        .values(is_default=False)
    )


# ─────────────────────────── Export Job Endpoints ───────────────

@router.post("", response_model=ExportCreateResponse)
async def create_export(
    request: ExportCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    service = ExportService(db, current_company.accounting_firm_id, current_company.company_id)
    try:
        job, export_file = await service.create_export(request.template_id, request.only_approved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ExportCreateResponse(
        export_job_id=job.id,
        export_file_id=export_file.id,
        file_path=export_file.file_path,
    )


@router.get("/{export_file_id}/download")
async def download_export(
    export_file_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export_file = await db.get(ExportFile, export_file_id)
    if not export_file:
        raise HTTPException(status_code=404, detail="Export file not found")
    return FileResponse(export_file.file_path, filename="receipts_export.csv")

