import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import CurrentCompanyContext, get_current_company, get_current_user
from ..models.category import AccountSubject, ClassificationResult
from ..models.receipt import CorrectionTemplate, Document, ReceiptExtraction, ReceiptTaxLine, ReviewAuditLog, ReviewTask
from ..models.user import ClientCompany, User
from ..schemas.receipt import ReviewResolveRequest
from ..services.tax_rules import get_company_tax_config
from ..services.review_service import ReviewService

router = APIRouter()


@router.get("/tasks")
async def list_review_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    service = ReviewService(db)
    tasks = await service.list_pending(current_company.company_id)

    # Batch-load documents and extractions for the task list
    doc_ids = [t.document_id for t in tasks]
    documents_map: dict[int, Document] = {}
    extractions_map: dict[int, ReceiptExtraction] = {}
    if doc_ids:
        docs = (
            await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        ).scalars().all()
        documents_map = {d.id: d for d in docs}
        exts = (
            await db.execute(
                select(ReceiptExtraction).where(ReceiptExtraction.document_id.in_(doc_ids))
            )
        ).scalars().all()
        extractions_map = {e.document_id: e for e in exts}

    result = []
    for t in tasks:
        doc = documents_map.get(t.document_id)
        ext = extractions_map.get(t.document_id)
        has_critical = any(
            c in (t.reason_codes or [])
            for c in ("low_ocr_confidence", "unknown_tax_rate", "missing_merchant", "missing_total_amount")
        )
        result.append({
            "id": t.id,
            "document_id": t.document_id,
            "task_status": t.task_status,
            "reason_codes": t.reason_codes,
            "assigned_to": t.assigned_to,
            "original_filename": doc.original_filename if doc else None,
            "merchant_name": ext.merchant_name if ext else None,
            "total_amount": float(ext.total_amount) if ext and ext.total_amount is not None else None,
            "has_critical": has_critical,
        })
    # Sort: critical issues first, then by id asc
    result.sort(key=lambda x: (not x["has_critical"], x["id"]))
    return result


@router.post("/tasks/{task_id}/resolve")
async def resolve_review_task(
    task_id: int,
    request: ReviewResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    task = await db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found")

    document = await db.get(Document, task.document_id)
    if not document or document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Review task not found")

    service = ReviewService(db)
    try:
        task = await service.resolve(
            task_id,
            request.action,
            request.subject_id,
            request.note,
            request.corrected_extraction,
            request.corrected_tax_lines,
            request.use_recommended_subject,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return {
        "id": task.id,
        "document_id": task.document_id,
        "task_status": task.task_status,
        "resolved_subject_id": task.resolved_subject_id,
        "resolution_note": task.resolution_note,
    }


@router.get("/tasks/{task_id}/context")
async def get_review_task_context(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    task = await db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found")

    document = await db.get(Document, task.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Review task not found")
    company = await db.get(ClientCompany, document.client_company_id)

    extraction = (
        await db.execute(select(ReceiptExtraction).where(ReceiptExtraction.document_id == task.document_id))
    ).scalars().first()
    latest_result = (
        await db.execute(
            select(ClassificationResult)
            .where(ClassificationResult.document_id == task.document_id)
            .order_by(ClassificationResult.id.desc())
            .limit(1)
        )
    ).scalars().first()
    subjects = (
        await db.execute(
            select(AccountSubject)
            .where(
                AccountSubject.is_active.is_(True),
                AccountSubject.accounting_firm_id == current_company.accounting_firm_id,
                AccountSubject.client_company_id == current_company.company_id,
            )
            .order_by(AccountSubject.subject_code.asc())
        )
    ).scalars().all()
    audit_logs = (
        await db.execute(
            select(ReviewAuditLog)
            .where(ReviewAuditLog.review_task_id == task_id)
            .order_by(ReviewAuditLog.id.desc())
            .limit(20)
        )
    ).scalars().all()
    tax_lines = []
    if extraction:
        tax_lines = (
            await db.execute(
                select(ReceiptTaxLine)
                .where(ReceiptTaxLine.receipt_extraction_id == extraction.id)
                .order_by(ReceiptTaxLine.id.asc())
            )
        ).scalars().all()

    all_templates = (
        await db.execute(
            select(CorrectionTemplate)
            .where(CorrectionTemplate.is_active.is_(True))
            .order_by(CorrectionTemplate.priority.desc(), CorrectionTemplate.id.asc())
        )
    ).scalars().all()

    merchant_name = extraction.merchant_name if extraction else None

    def _template_matches(tpl: CorrectionTemplate) -> bool:
        if not tpl.merchant_pattern or not merchant_name:
            return False
        try:
            return bool(re.search(tpl.merchant_pattern, merchant_name, re.IGNORECASE))
        except re.error:
            return False

    correction_templates = [
        {
            "id": t.id,
            "template_key": t.template_key,
            "label": t.label,
            "merchant_pattern": t.merchant_pattern,
            "patch_fields": t.patch_fields,
            "note_prefix": t.note_prefix,
            "priority": t.priority,
            "matched": _template_matches(t),
        }
        for t in all_templates
    ]
    # Put matched templates first
    correction_templates.sort(key=lambda x: (not x["matched"], -x["priority"]))

    return {
        "task": {
            "id": task.id,
            "document_id": task.document_id,
            "task_status": task.task_status,
            "reason_codes": task.reason_codes,
            "resolution_note": task.resolution_note,
            "resolved_subject_id": task.resolved_subject_id,
        },
        "document": {
            "id": document.id,
            "original_filename": document.original_filename,
            "document_status": document.document_status,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        },
        "extraction": {
            "merchant_name": extraction.merchant_name if extraction else None,
            "transaction_date": str(extraction.transaction_date) if extraction and extraction.transaction_date else None,
            "registration_number": extraction.registration_number if extraction else None,
            "tax_amount": float(extraction.tax_amount) if extraction and extraction.tax_amount is not None else None,
            "total_amount": float(extraction.total_amount) if extraction and extraction.total_amount is not None else None,
            "tax_rate_label": extraction.tax_rate_label if extraction else None,
            "confidence": float(extraction.extraction_confidence) if extraction and extraction.extraction_confidence is not None else None,
            "amount_role_selected": (
                extraction.normalized_payload.get("amount_role_selected")
                if extraction and isinstance(extraction.normalized_payload, dict)
                else None
            ),
            "amount_role_candidates": (
                (extraction.normalized_payload.get("amount_role_candidates") or [])[:3]
                if extraction and isinstance(extraction.normalized_payload, dict)
                else []
            ),
        },
        "tax_lines": [
            {
                "id": line.id,
                "tax_rate": float(line.tax_rate),
                "taxable_amount": float(line.taxable_amount) if line.taxable_amount is not None else None,
                "tax_amount": float(line.tax_amount) if line.tax_amount is not None else None,
                "is_reduced_tax": line.is_reduced_tax,
            }
            for line in tax_lines
        ],
        "classification": {
            "subject_id": latest_result.subject_id if latest_result else None,
            "confidence": float(latest_result.confidence_score) if latest_result and latest_result.confidence_score is not None else None,
            "decision_source": latest_result.decision_source if latest_result else None,
        },
        "tax_config": {
            "jpy_rounding_mode": get_company_tax_config(company).jpy_rounding_mode,
            "tax_rounding_level": get_company_tax_config(company).tax_rounding_level,
        },
        "subjects": [
            {
                "id": s.id,
                "subject_code": s.subject_code,
                "subject_name": s.subject_name,
            }
            for s in subjects
        ],
        "audit_logs": [
            {
                "id": log.id,
                "action_type": log.action_type,
                "changed_by": log.changed_by,
                "reason_note": log.reason_note,
                "before_json": log.before_json,
                "after_json": log.after_json,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in audit_logs
        ],
        "correction_templates": correction_templates,
    }
