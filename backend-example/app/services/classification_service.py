from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.category import AccountSubject, ClassificationResult, ClassificationRule
from ..models.receipt import Document, ReceiptExtraction


class ClassificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def classify_document(self, document: Document, extraction: ReceiptExtraction) -> ClassificationResult:
        rules_stmt = (
            select(ClassificationRule)
            .where(ClassificationRule.is_active.is_(True))
            .order_by(ClassificationRule.priority.asc())
        )
        rules = (await self.db.execute(rules_stmt)).scalars().all()

        best_rule = None
        best_score = 0.0
        merchant_name = (extraction.merchant_name or "").lower()

        for rule in rules:
            score = self._evaluate_rule(merchant_name, float(extraction.total_amount or 0), rule)
            if score > best_score:
                best_score = score
                best_rule = rule

        if not best_rule:
            subject = await self._fallback_subject(document)
            return await self._save_result(document.id, None, subject.id if subject else None, 0.0, "auto")

        confidence = min(best_score, 1.0)
        result = await self._save_result(document.id, best_rule.id, best_rule.target_subject_id, confidence, "auto")
        return result

    @staticmethod
    def _evaluate_rule(merchant_name: str, total_amount: float, rule: ClassificationRule) -> float:
        condition = rule.rule_condition or {}

        if rule.rule_type == "keyword":
            keyword = str(condition.get("keyword", "")).lower()
            return float(rule.score) if keyword and keyword in merchant_name else 0.0

        if rule.rule_type == "merchant_exact":
            target = str(condition.get("merchant_name", "")).lower()
            return float(rule.score) if target and target == merchant_name else 0.0

        if rule.rule_type == "amount_range":
            min_amount = float(condition.get("min", 0))
            max_amount = float(condition.get("max", 999999999))
            return float(rule.score) if min_amount <= total_amount <= max_amount else 0.0

        if rule.rule_type == "regex":
            pattern = str(condition.get("pattern", ""))
            if pattern and pattern in merchant_name:
                return float(rule.score)

        return 0.0

    async def should_send_review(self, result: ClassificationResult, extraction: ReceiptExtraction) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if float(extraction.extraction_confidence or 0) < settings.ocr_confidence_threshold:
            reasons.append("low_ocr_confidence")
        if float(result.confidence_score or 0) < settings.classification_confidence_threshold:
            reasons.append("low_classification_confidence")
        if not extraction.merchant_name:
            reasons.append("missing_merchant")
        if not extraction.total_amount:
            reasons.append("missing_total_amount")
        if extraction.tax_rate_label == "unknown":
            reasons.append("unknown_tax_rate")
        return (len(reasons) > 0, reasons)

    async def _fallback_subject(self, document: Document) -> AccountSubject | None:
        stmt = (
            select(AccountSubject)
            .where(
                AccountSubject.accounting_firm_id == document.accounting_firm_id,
                AccountSubject.client_company_id == document.client_company_id,
                AccountSubject.is_active.is_(True),
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def _save_result(
        self,
        document_id: int,
        rule_id: int | None,
        subject_id: int | None,
        confidence: float,
        decision_source: str,
    ) -> ClassificationResult:
        result = ClassificationResult(
            document_id=document_id,
            matched_rule_id=rule_id,
            subject_id=subject_id,
            confidence_score=confidence,
            decision_source=decision_source,
            created_by=settings.default_user_id,
        )
        self.db.add(result)
        await self.db.flush()
        return result
