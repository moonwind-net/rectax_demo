import io
import os
import re
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
import pypdfium2 as pdfium

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - runtime optional dependency
    cv2 = None

app = FastAPI(title="Paddle OCR API", version="1.0.0")

# Japanese receipts are the target scenario.
_ocr_engine: Any | None = None
_ocr_mode: str = "cpu"
_ocr_init_error: str | None = None
_ocr_requested_version: str = "default"


def _build_engine(paddle_ocr_cls: Any, use_gpu: bool, ocr_version: str | None) -> Any:
    kwargs = {
        "use_angle_cls": True,
        "lang": "japan",
        "show_log": False,
        "use_gpu": use_gpu,
    }
    if ocr_version:
        kwargs["ocr_version"] = ocr_version

    try:
        return paddle_ocr_cls(**kwargs)
    except TypeError:
        # Backward compatibility for PaddleOCR builds without ocr_version argument.
        if "ocr_version" in kwargs:
            kwargs.pop("ocr_version")
            return paddle_ocr_cls(**kwargs)
        raise


def _init_engine() -> tuple[Any, str]:
    """Initialize OCR engine with GPU-first strategy and CPU fallback."""
    try:
        from paddleocr import PaddleOCR  # Lazy import to avoid process crash on missing runtime deps.
    except Exception as exc:
        raise RuntimeError(f"Failed to import PaddleOCR: {exc}") from exc

    prefer_gpu = os.getenv("PADDLE_OCR_PREFER_GPU", "true").strip().lower() in {"1", "true", "yes", "on"}
    requested_version = os.getenv("PADDLE_OCR_OCR_VERSION", "").strip() or None

    if prefer_gpu:
        try:
            engine = _build_engine(PaddleOCR, use_gpu=True, ocr_version=requested_version)
            return engine, "gpu"
        except Exception:
            pass

    # Fallback path is always CPU to keep service available.
    engine = _build_engine(PaddleOCR, use_gpu=False, ocr_version=requested_version)
    return engine, "cpu"


@app.on_event("startup")
async def startup_event() -> None:
    global _ocr_engine, _ocr_mode, _ocr_init_error, _ocr_requested_version
    _ocr_requested_version = os.getenv("PADDLE_OCR_OCR_VERSION", "").strip() or "default"
    try:
        _ocr_engine, _ocr_mode = _init_engine()
        _ocr_init_error = None
    except Exception as exc:
        # Keep container alive in degraded mode so the rest of the stack can start.
        _ocr_engine = None
        _ocr_mode = "unavailable"
        _ocr_init_error = str(exc)

AMOUNT_TOKEN_RE = re.compile(r"[0-9０-９][0-9０-９,，.．。]*")
DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")
AMOUNT_KEYWORDS = (
    "ご利用金額",
    "利用金額",
    "お支払金額",
    "お買上金額",
    "ご請求額",
    "税込合計",
    "合計",
    "金額",
)
STRICT_TOTAL_KEYWORDS = (
    "合計",
    "合計金額",
    "お買上金額",
    "ご利用金額",
    "お支払金額",
    "お支払い額",
    "ご請求金額",
    "お会計",
    "お支払",
    "ご請求",
    "お買上",
    "合計(税込)",
    "合計（税込）",
)
EXCLUDED_AMOUNT_LINE_KEYWORDS = ("登録番号", "電話", "TEL", "問い合わせ", "お問合せ")

# Additional Japanese amount indicators
JAPANESE_AMOUNT_SUFFIXES = ("円", "¥", "￥")
JAPANESE_CURRENCY_PATTERNS = (r"¥[\d,]+", r"￥[\d,]+", r"[\d,]+円")

# Quantity/count indicators that should be excluded from amount parsing
QUANTITY_KEYWORDS = ("個", "枚", "セット", "本", "箱", "袋", "kg", "g", "ml", "L")
REGISTRATION_LABEL_KEYWORDS = ("登録番号",)
REGISTRATION_WHITELIST = set("T0123456789")
# Accept explicit percent symbols and noisy OCR variants such as "8税"/"10税".
# Also handles formats like "対象", "軽減税率"
TAX_RATE_RE = re.compile(r"(8|10)\s*(?:[%％]|(?=税))|軽減税率\s*(8)|10%|8%")

MERCHANT_BLOCKLIST_KEYWORDS = (
    "毎度ありがとうございます",
    "ありがとうございます",
    "お客様控え",
    "お客様控",
    "控え",
    "領収証",
    "領収書",
    "レシート",
    "営業時間",
    "登録番号",
    "電話",
    "TEL",
    "お釣り",
    "現金",
)
MERCHANT_HINT_KEYWORDS = (
    "ストア",
    "スーパー",
    "マート",
    "ドラッグ",
    "薬局",
    "商店",
    "百貨店",
    "店",
)
ADDRESS_HINT_KEYWORDS = (
    "都",
    "道",
    "府",
    "県",
    "市",
    "区",
    "町",
    "村",
    "丁目",
    "番地",
    "号",
)
# === TAX AMOUNT KEYWORDS ===
TAX_AMOUNT_KEYWORDS = (
    "消費税",
    "消費税額",
    "内消費税",
    "外消費税",
    "税額",
    "税金額",
    "税",
)

# === TAX RATE KEYWORDS ===
TAX_RATE_KEYWORDS = (
    "税率",
    "対象",
    "軽減税率",
)

# === PAYMENT RELATED KEYWORDS ===
PAYMENT_TENDER_KEYWORDS = ("現金", "預り", "お預かり", "お預り", "お預け", "お支払", "ご請求")
PAYMENT_CHANGE_KEYWORDS = ("お釣り", "釣銭", "つり", "おつり")
PAYMENT_METHOD_KEYWORDS = (
    "現金",
    "クレジット",
    "カード",
    "電子マネー",
    "ポイント",
    "QR",
    "PayPay",
    "Suica",
    "Pasmo",
)

# === TAX INCLUSIVE INDICATORS ===
TAX_INCLUSIVE_KEYWORDS = (
    "合計(税込)",
    "合計（税込）",
    "税込合計",
    "税込み合計",
    "税込",
)

MIN_RECEIPT_CONTOUR_AREA_RATIO = 0.18
MAX_ROLE_CANDIDATES = 8
MIN_CHANGE_AMOUNT_FOR_ROLE = 30.0
SERIAL_HINT_RE = re.compile(r"(NO|N0|#|レジ|伝票|取引|MO|ID)[^\n]*\d", re.IGNORECASE)
TOTAL_SIGNAL_KEYWORDS = (
    "合計",
    "金額",
    "お支払",
    "ご利用",
    "税込",
)
DEFAULT_RECEIPT_CROP_MAX_CANDIDATES = 5
DEFAULT_RECEIPT_CROP_MIN_SCORE_GAIN = 8.0
DEFAULT_RECEIPT_CROP_MIN_ABS_SCORE = 40.0

_FULLWIDTH_TRANSLATION = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "，": ",", "．": ".", "。": ".", "￥": "¥",
})

ROUNDING_MODE_MAP = {
    "floor": ROUND_FLOOR,
    "round": ROUND_HALF_UP,
    "ceil": ROUND_CEILING,
}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok" if _ocr_engine is not None else "degraded",
        "service": "paddle-ocr",
        "mode": _ocr_mode,
        "ocr_version": _ocr_requested_version,
        "error": _ocr_init_error,
    }


@app.post("/ocr/")
async def ocr(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    is_pdf = content_type == "application/pdf" or filename.endswith(".pdf")

    try:
        if is_pdf:
            images_np = _pdf_bytes_to_images(raw)
        else:
            images_np = [_image_bytes_to_array(raw)]
    except Exception as exc:
        kind = "PDF" if is_pdf else "image"
        raise HTTPException(status_code=400, detail=f"Invalid {kind}: {exc}") from exc

    try:
        all_lines, all_confidences, all_entries, ocr_images = _run_ocr_on_images(images_np)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PaddleOCR failed: {exc}") from exc

    original_payload = _to_receipt_payload(all_lines, all_confidences, all_entries, ocr_images or images_np)
    trigger_reasons = _enhancement_trigger_reasons(original_payload)

    final_payload = original_payload
    selected_variant = "original"
    enhanced_candidates_audit: list[dict[str, Any]] = []

    if trigger_reasons:
        for variant_name, variant_images in _build_enhanced_retry_variants(images_np):
            try:
                v_lines, v_confs, v_entries, v_ocr_images = _run_ocr_on_images(variant_images)
                variant_payload = _to_receipt_payload(v_lines, v_confs, v_entries, v_ocr_images or variant_images)
            except Exception as exc:
                enhanced_candidates_audit.append({"variant": variant_name, "error": str(exc)})
                continue

            enhanced_candidates_audit.append(
                {
                    "variant": variant_name,
                    "score": round(_payload_reliability_score(variant_payload), 3),
                    "confidence": variant_payload.get("confidence"),
                    "total_amount": variant_payload.get("total_amount"),
                    "tax_amount": variant_payload.get("tax_amount"),
                    "has_total_keywords": _payload_has_total_keywords(variant_payload),
                    "amount_role_candidate_count": len(variant_payload.get("amount_role_candidates") or []),
                }
            )

            if _is_significantly_more_reliable(final_payload, variant_payload):
                final_payload = variant_payload
                selected_variant = variant_name

    final_payload["ocr_retry"] = {
        "triggered": bool(trigger_reasons),
        "trigger_reasons": trigger_reasons,
        "selected_variant": selected_variant,
        "candidate_summaries": enhanced_candidates_audit,
    }
    final_payload["ocr_audit"] = {
        "original": _audit_snapshot(original_payload, "original"),
        "final": _audit_snapshot(final_payload, selected_variant),
    }

    # Post-process safety net: detect a single '1' mistakenly inserted at an
    # early position in the recognized total (common OCR error e.g. 2280->21280)
    # and prefer the smaller plausible candidate if it appears in the text tokens.
    try:
        tot = int(round(float(final_payload.get("total_amount") or 0)))
        s = str(tot)
        if tot > 1000 and len(s) >= 4:
            # gather all numeric candidates observed in the final raw text
            observed: set[int] = set()
            raw_text = str(final_payload.get("raw_text_normalized") or final_payload.get("raw_text") or "")
            for line in raw_text.splitlines():
                for a in _extract_amount_candidates(line):
                    try:
                        observed.add(int(round(float(a))))
                    except Exception:
                        continue

            for remove_idx in range(0, min(3, len(s))):
                if s[remove_idx] != "1":
                    continue
                alt_s = s[:remove_idx] + s[remove_idx + 1 :]
                try:
                    alt = int(alt_s)
                except Exception:
                    continue
                if not (50 <= alt <= 100000):
                    continue
                if alt in observed:
                    final_payload.setdefault("corrections", {})["inserted_one_fix"] = {"from": tot, "to": alt}
                    final_payload["total_amount"] = float(alt)
                    # update audit snapshot
                    final_payload["ocr_audit"]["final"]["total_amount"] = final_payload["total_amount"]
                    break
    except Exception:
        pass

    return JSONResponse(content=final_payload, status_code=200)


def _image_bytes_to_array(raw: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    return np.array(image)


def _pdf_bytes_to_images(raw: bytes) -> list[np.ndarray]:
    pdf = pdfium.PdfDocument(raw)
    if len(pdf) == 0:
        raise ValueError("PDF has no pages")

    images: list[np.ndarray] = []
    scale = 2.0  # 144 DPI equivalent for better OCR readability than 72 DPI default.
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil().convert("RGB")
        images.append(np.array(pil_image))

    return images


def _preprocess_image_for_ocr(image_np: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return image_np

    try:
        image_bgr = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
        rotated = _auto_rotate_receipt(image_bgr)

        # Stage-1: try multiple receipt-region candidates and select via OCR score.
        # If no strong winner, fallback to existing perspective-correction path.
        best_crop = _select_best_receipt_region(rotated)
        deskewed = best_crop if best_crop is not None else _apply_receipt_perspective_correction(rotated)

        enhanced = _enhance_receipt_for_ocr(deskewed)
        return cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB)
    except Exception:
        # Keep OCR available even when preprocessing fails on edge cases.
        return image_np


def _extract_receipt_region_candidates(image_bgr: np.ndarray) -> list[tuple[str, np.ndarray, float]]:
    if cv2 is None:
        return [("original", image_bgr, 1.0)]

    image_area = float(max(image_bgr.shape[0] * image_bgr.shape[1], 1))
    candidates: list[tuple[str, np.ndarray, float]] = [("original", image_bgr, 1.0)]

    quads = _find_receipt_quadrilateral_candidates(image_bgr)
    for idx, (quad, area_ratio) in enumerate(quads):
        warped = _four_point_transform(image_bgr, quad)
        if warped.size == 0:
            continue
        h, w = warped.shape[:2]
        if h < 120 or w < 120:
            continue
        candidates.append((f"quad_{idx}", warped, float(area_ratio)))

    # Fallback candidate for long receipt photos with lots of background.
    h, w = image_bgr.shape[:2]
    c1, c2 = int(h * 0.20), int(h * 0.98)
    if c2 > c1 + 120:
        center_crop = image_bgr[c1:c2, max(int(w * 0.03), 0):min(int(w * 0.97), w)]
        crop_area_ratio = (center_crop.shape[0] * center_crop.shape[1]) / image_area
        candidates.append(("center_tall_crop", center_crop, float(crop_area_ratio)))

    # Keep only top N candidates to control latency.
    max_candidates = _env_int(
        "PADDLE_OCR_RECEIPT_CROP_MAX_CANDIDATES",
        DEFAULT_RECEIPT_CROP_MAX_CANDIDATES,
        min_value=2,
        max_value=10,
    )
    return candidates[:max_candidates]


def _candidate_receipt_score(candidate_bgr: np.ndarray, area_ratio: float) -> float:
    if _ocr_engine is None:
        return -1e9

    try:
        candidate_rgb = cv2.cvtColor(candidate_bgr, cv2.COLOR_BGR2RGB)
        result = _ocr_engine.ocr(candidate_rgb, cls=True)
        entries = _extract_line_entries(result, page_index=0)
    except Exception:
        return -1e9

    score = _score_ocr_entries(entries)
    lines = [str(e.get("text") or "") for e in entries]
    joined = "\n".join(lines)

    # Receipt-specific quality hints.
    if any(k in joined for k in ("合計", "税込", "税", "領収", "登録番号", "円", "¥", "￥")):
        score += 8.0

    h, w = candidate_bgr.shape[:2]
    if h > w:
        score += 2.0
    if area_ratio < 0.10:
        score -= 8.0
    if area_ratio > 0.95:
        score -= 1.0

    return score


def _select_best_receipt_region(image_bgr: np.ndarray) -> np.ndarray | None:
    if cv2 is None or _ocr_engine is None:
        return None

    candidates = _extract_receipt_region_candidates(image_bgr)
    if len(candidates) <= 1:
        return None

    scored: list[tuple[float, str, np.ndarray]] = []
    for name, cand, area_ratio in candidates:
        scored.append((_candidate_receipt_score(cand, area_ratio), name, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_name, best_image = scored[0]

    original_score = next((s for s, n, _ in scored if n == "original"), -1e9)
    min_gain = _env_float(
        "PADDLE_OCR_RECEIPT_CROP_MIN_SCORE_GAIN",
        DEFAULT_RECEIPT_CROP_MIN_SCORE_GAIN,
        min_value=0.0,
    )
    min_abs = _env_float(
        "PADDLE_OCR_RECEIPT_CROP_MIN_ABS_SCORE",
        DEFAULT_RECEIPT_CROP_MIN_ABS_SCORE,
        min_value=0.0,
    )

    if best_name == "original":
        return None
    if best_score < min_abs:
        return None
    if best_score < original_score + min_gain:
        return None

    return best_image


def _auto_rotate_receipt(image_bgr: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return image_bgr

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    if len(coords) < 50:
        rotated = image_bgr
    else:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) < 0.7:
            rotated = image_bgr
        else:
            h, w = image_bgr.shape[:2]
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                image_bgr,
                matrix,
                (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255),
            )

    # Receipts are usually portrait. If still landscape, rotate once.
    h2, w2 = rotated.shape[:2]
    if w2 > h2 * 1.2:
        return cv2.rotate(rotated, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return rotated


def _apply_receipt_perspective_correction(image_bgr: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return image_bgr

    quad = _find_receipt_quadrilateral(image_bgr)
    if quad is None:
        return image_bgr

    return _four_point_transform(image_bgr, quad)


def _find_receipt_quadrilateral(image_bgr: np.ndarray) -> np.ndarray | None:
    candidates = _find_receipt_quadrilateral_candidates(image_bgr)
    if not candidates:
        return None
    return candidates[0][0]


def _find_receipt_quadrilateral_candidates(image_bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    if cv2 is None:
        return []

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    image_area = float(image_bgr.shape[0] * image_bgr.shape[1])
    min_area = image_area * MIN_RECEIPT_CONTOUR_AREA_RATIO
    candidates: list[tuple[np.ndarray, float]] = []

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            candidates.append((_order_points_clockwise(quad), float(area / image_area)))
            continue

        # Fallback: use minimum area rectangle for near-rectangular receipts.
        rect = cv2.minAreaRect(contour)
        box = cv2.boxPoints(rect)
        box = np.array(box, dtype=np.float32)
        if cv2.contourArea(box) >= min_area:
            candidates.append((_order_points_clockwise(box), float(cv2.contourArea(box) / image_area)))

    # Deduplicate by coarse geometry to avoid repeated near-identical candidates.
    deduped: list[tuple[np.ndarray, float]] = []
    seen: set[tuple[int, int]] = set()
    for quad, ratio in candidates:
        key = (int(np.mean(quad[:, 0]) // 20), int(np.mean(quad[:, 1]) // 20))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((quad, ratio))

    return deduped


def _order_points_clockwise(points: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1)

    rect[0] = points[np.argmin(sums)]
    rect[2] = points[np.argmax(sums)]
    rect[1] = points[np.argmin(diffs)]
    rect[3] = points[np.argmax(diffs)]
    return rect


def _four_point_transform(image_bgr: np.ndarray, points: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return image_bgr

    rect = _order_points_clockwise(points)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b), 1)

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b), 1)

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype=np.float32,
    )

    matrix = cv2.getPerspectiveTransform(rect, destination)
    warped = cv2.warpPerspective(
        image_bgr,
        matrix,
        (max_width, max_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )

    if warped.shape[1] > warped.shape[0] * 1.2:
        warped = cv2.rotate(warped, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return warped


def _enhance_receipt_for_ocr(image_bgr: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return image_bgr

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)
    denoised = cv2.bilateralFilter(contrast, 5, 40, 40)
    binary = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    blended = cv2.addWeighted(denoised, 0.7, binary, 0.3, 0)
    return cv2.cvtColor(blended, cv2.COLOR_GRAY2BGR)


def _extract_line_entries(result: Any, page_index: int = 0) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    for block in result or []:
        for item in block or []:
            if len(item) >= 2 and item[1]:
                txt = str(item[1][0]).strip()
                conf = float(item[1][1])
                if txt:
                    points = item[0] if item and item[0] else []
                    xs = [int(point[0]) for point in points] if points else [0, 0]
                    ys = [int(point[1]) for point in points] if points else [0, 0]
                    entries.append(
                        {
                            "text": txt,
                            "confidence": conf,
                            "page_index": page_index,
                            "bbox": (
                                max(min(xs), 0),
                                max(min(ys), 0),
                                max(xs),
                                max(ys),
                            ),
                        }
                    )

    return entries


def _extract_lines(result: Any) -> tuple[list[str], list[float]]:
    entries = _extract_line_entries(result)
    return [entry["text"] for entry in entries], [entry["confidence"] for entry in entries]


def _score_ocr_entries(entries: list[dict[str, Any]]) -> float:
    if not entries:
        return -1.0

    lines = [str(entry["text"]) for entry in entries if entry.get("text")]
    avg_conf = sum(float(entry.get("confidence", 0.0)) for entry in entries) / max(len(entries), 1)
    joined = "\n".join(lines)

    score = len(lines) * 2.0 + avg_conf * 100.0
    if any("税" in line for line in lines):
        score += 10.0
    if any("合計" in line or "小計" in line for line in lines):
        score += 8.0
    if any("¥" in line or "￥" in line or "円" in line for line in lines):
        score += 6.0
    if DATE_RE.search(joined):
        score += 5.0

    return score


def _run_ocr_on_images(images_np: list[np.ndarray]) -> tuple[list[str], list[float], list[dict[str, Any]], list[np.ndarray]]:
    if _ocr_engine is None:
        raise RuntimeError(f"OCR engine not initialized: {_ocr_init_error or 'unknown error'}")

    prepared_images = [_preprocess_image_for_ocr(image_np) for image_np in images_np]
    all_lines: list[str] = []
    all_confidences: list[float] = []
    all_entries: list[dict[str, Any]] = []
    selected_images: list[np.ndarray] = []

    for idx, preprocessed_image in enumerate(prepared_images, start=1):
        original_image = images_np[idx - 1]

        preprocessed_result = _ocr_engine.ocr(preprocessed_image, cls=True)
        preprocessed_entries = _extract_line_entries(preprocessed_result, page_index=idx - 1)
        preprocessed_score = _score_ocr_entries(preprocessed_entries)

        original_result = _ocr_engine.ocr(original_image, cls=True)
        original_entries = _extract_line_entries(original_result, page_index=idx - 1)
        original_score = _score_ocr_entries(original_entries)

        if preprocessed_score > original_score:
            entries = preprocessed_entries
            image_np = preprocessed_image
        else:
            entries = original_entries
            image_np = original_image

        lines = [entry["text"] for entry in entries]
        confidences = [entry["confidence"] for entry in entries]
        if len(images_np) > 1 and lines:
            all_lines.append(f"[page {idx}]")
        all_lines.extend(lines)
        all_confidences.extend(confidences)
        all_entries.extend(entries)
        selected_images.append(image_np)

    return all_lines, all_confidences, all_entries, selected_images


def _build_enhanced_retry_variants(images_np: list[np.ndarray]) -> list[tuple[str, list[np.ndarray]]]:
    # Conservative scope to avoid regressions: retry variants for single-image receipts only.
    if len(images_np) != 1:
        return []

    image_np = images_np[0]
    h, w = image_np.shape[:2]
    pil = Image.fromarray(image_np).convert("RGB")

    variants: list[tuple[str, list[np.ndarray]]] = []
    variants.append(("up3", [np.array(pil.resize((w * 3, h * 3), Image.Resampling.LANCZOS))]))

    crop_center = pil.crop((0, int(h * 0.30), w, int(h * 0.95)))
    variants.append(
        (
            "crop_center65_up2",
            [np.array(crop_center.resize((crop_center.width * 2, crop_center.height * 2), Image.Resampling.LANCZOS))],
        )
    )

    if cv2 is not None:
        gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
        thr = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
        variants.append(("clahe_adapt", [cv2.cvtColor(thr, cv2.COLOR_GRAY2RGB)]))

    return variants


def _payload_has_total_keywords(payload: dict[str, Any]) -> bool:
    text = str(payload.get("raw_text_normalized") or payload.get("raw_text") or "")
    return any(keyword in text for keyword in TOTAL_SIGNAL_KEYWORDS)


def _env_float(name: str, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default

    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_int(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default

    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _payload_has_tax_logic_issue(payload: dict[str, Any]) -> bool:
    min_total = _env_float("PADDLE_OCR_ENHANCE_RETRY_MIN_TOTAL", 50.0, min_value=0.0)
    total = float(payload.get("total_amount") or 0)
    tax = float(payload.get("tax_amount") or 0)
    if total <= 0:
        return True
    if tax < 0 or tax > total:
        return True
    if total < min_total:
        return True
    return False


def _enhancement_trigger_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    confidence_threshold = _env_float(
        "PADDLE_OCR_ENHANCE_RETRY_CONFIDENCE_THRESHOLD",
        0.65,
        min_value=0.0,
        max_value=1.0,
    )

    confidence = float(payload.get("confidence") or 0)
    if confidence < confidence_threshold:
        reasons.append("low_confidence")

    if _payload_has_tax_logic_issue(payload):
        reasons.append("amount_logic_issue")

    amount_role_candidates = payload.get("amount_role_candidates") or []
    if not _payload_has_total_keywords(payload) and len(amount_role_candidates) == 0:
        reasons.append("missing_total_keywords_and_candidates")

    return reasons


def _payload_reliability_score(payload: dict[str, Any]) -> float:
    min_total = _env_float("PADDLE_OCR_ENHANCE_RETRY_MIN_TOTAL", 50.0, min_value=0.0)
    confidence = float(payload.get("confidence") or 0)
    total = float(payload.get("total_amount") or 0)
    tax = float(payload.get("tax_amount") or 0)
    candidates = payload.get("amount_role_candidates") or []

    score = confidence * 100.0

    if _payload_has_total_keywords(payload):
        score += 24.0
    if min_total <= total <= 100000:
        score += 14.0
    elif total <= 0:
        score -= 20.0
    elif total < min_total:
        score -= 8.0

    if 0 <= tax <= total:
        score += 8.0
    else:
        score -= 16.0

    score += min(len(candidates), 6) * 2.0
    return score


def _is_significantly_more_reliable(original_payload: dict[str, Any], enhanced_payload: dict[str, Any]) -> bool:
    min_score_gain = _env_float("PADDLE_OCR_ENHANCE_RETRY_MIN_SCORE_GAIN", 12.0, min_value=0.0)
    min_confidence_gain = _env_float(
        "PADDLE_OCR_ENHANCE_RETRY_MIN_CONFIDENCE_GAIN",
        0.06,
        min_value=0.0,
        max_value=1.0,
    )

    original_score = _payload_reliability_score(original_payload)
    enhanced_score = _payload_reliability_score(enhanced_payload)

    score_gain = enhanced_score - original_score
    confidence_gain = float(enhanced_payload.get("confidence") or 0) - float(original_payload.get("confidence") or 0)
    total_keyword_gain = _payload_has_total_keywords(enhanced_payload) and not _payload_has_total_keywords(original_payload)
    tax_logic_gain = (not _payload_has_tax_logic_issue(enhanced_payload)) and _payload_has_tax_logic_issue(original_payload)

    if score_gain < min_score_gain:
        return False
    return bool(confidence_gain >= min_confidence_gain or total_keyword_gain or tax_logic_gain)


def _audit_snapshot(payload: dict[str, Any], variant: str) -> dict[str, Any]:
    return {
        "variant": variant,
        "confidence": payload.get("confidence"),
        "total_amount": payload.get("total_amount"),
        "tax_amount": payload.get("tax_amount"),
        "tax_rate_label": payload.get("tax_rate_label"),
        "has_total_keywords": _payload_has_total_keywords(payload),
        "amount_role_candidate_count": len(payload.get("amount_role_candidates") or []),
        "reliability_score": round(_payload_reliability_score(payload), 3),
    }


def _normalize_amount_token(token: str) -> str:
    normalized = token.translate(_FULLWIDTH_TRANSLATION)
    normalized = normalized.replace(" ", "").replace("\u3000", "")
    return normalized


def _normalize_matching_text(text: str) -> str:
    return text.translate(_FULLWIDTH_TRANSLATION).replace(" ", "").replace("\u3000", "")


def _registration_candidates_debug_enabled() -> bool:
    return os.getenv("PADDLE_OCR_DEBUG_REGISTRATION_CANDIDATES", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _registration_candidates_low_confidence_threshold() -> float:
    raw = os.getenv("PADDLE_OCR_DEBUG_REGISTRATION_CANDIDATES_MAX_CONFIDENCE", "0.6").strip()
    try:
        threshold = float(raw)
    except ValueError:
        return 0.6
    return max(0.0, min(1.0, threshold))


def _looks_like_amount_line(line: str) -> bool:
    normalized = _normalize_matching_text(line)
    if any(keyword in normalized for keyword in AMOUNT_KEYWORDS):
        return True

    fuzzy_hits = sum(fragment in normalized for fragment in ("利用", "支払", "請求", "買上", "金", "額", "貞"))
    return fuzzy_hits >= 2


def _line_should_be_excluded_for_amounts(line: str) -> bool:
    normalized = _normalize_matching_text(line)
    
    # Exclude lines with specific keywords
    if any(keyword in normalized for keyword in EXCLUDED_AMOUNT_LINE_KEYWORDS):
        return True
    
    # Exclude lines with quantity keywords (product lines, not totals)
    if any(keyword in normalized for keyword in QUANTITY_KEYWORDS):
        return True
    
    # Exclude consumer number patterns (T followed by 13-14 digits)
    if re.search(r"T?\d{13,14}", normalized):
        return True
    
    # Exclude lines that look like phone numbers or dates
    if re.search(r"^\d{2,4}[.-]?\d{2,4}[.-]?\d{4}$", normalized):
        return True
    if DATE_RE.search(normalized):
        return True
    if re.search(r"(19|20)\d{2}年\d{1,2}月\d{1,2}日", normalized):
        return True
    
    # Exclude lines with time patterns (HH:MM, HH時MM分)
    if re.search(r"\d{1,2}[:時]\d{1,2}", normalized):
        return True

    # Exclude typical address lines so block/house numbers are not treated as totals.
    if re.search(r"(都|道|府|県).{0,18}(市|区|町|村)", normalized):
        return True
    if re.search(r"\d{1,4}[-−ー]\d{1,4}[-−ー]\d{1,4}", normalized):
        return True
    if re.search(r"\d{1,4}号", normalized):
        return True
    
    return False


def _parse_jpy_amount_candidates(token: str, line: str) -> list[float]:
    token = _normalize_amount_token(token)
    token = re.sub(r"[^0-9,\.]", "", token)
    if not token or not any(ch.isdigit() for ch in token):
        return []

    digits_only = re.sub(r"[^0-9]", "", token)
    if not digits_only:
        return []

    normalized_line = _normalize_matching_text(line)

    if _line_should_be_excluded_for_amounts(line):
        return []
    # Avoid treating a year token from date/time lines as money.
    if re.fullmatch(r"(?:19|20)\d{2}", digits_only) and (
        DATE_RE.search(normalized_line)
        or re.search(r"(19|20)\d{2}年", normalized_line)
        or re.search(r"\d{1,2}[:時]\d{1,2}", normalized_line)
    ):
        return []
    if len(digits_only) >= 13:
        return []
    if re.search(r"\d{2,4}-\d{2,4}-\d{3,4}", normalized_line):
        return []

    
    token = _normalize_amount_token(token)
    token = re.sub(r"[^0-9,\.]", "", token)
    if not token or not any(ch.isdigit() for ch in token):
        return []

    digits_only = re.sub(r"[^0-9]", "", token)
    if not digits_only:
        return []
    if len(digits_only) >= 13:
        return []
    if _line_should_be_excluded_for_amounts(line):
        return []

    candidates: set[int] = set()

    # Case A: single '.' used as thousands separator (e.g. 16.455 -> 16455)
    if "," not in token and token.count(".") == 1:
        left, right = token.split(".")
        if left.isdigit() and right.isdigit():
            if len(right) == 3:
                candidates.add(int(left + right))
                if _looks_like_amount_line(line) and len(left) in (3, 4) and left.startswith("1"):
                    # sometimes OCR inserts a leading '1' into the left block
                    candidates.add(int(left[1:] + right))
            elif len(right) <= 2:
                # decimal cents-ish -> round to nearest yen
                try:
                    decimal_value = float(f"{left}.{right}")
                    candidates.add(int(round(decimal_value)))
                except Exception:
                    pass

    # Normalize multi-dot / comma cases
    token_nodots = token.replace(".", "") if token.count(".") > 1 else token
    token_no_commas = token_nodots.replace(",", "")

    parsed_int = None
    if token_no_commas.isdigit():
        try:
            parsed_int = int(token_no_commas)
            candidates.add(parsed_int)
        except Exception:
            parsed_int = None

    # Scaled fallbacks for obvious OCR-decimal/drop issues
    if parsed_int is not None:
        if parsed_int >= 100000 and parsed_int % 100 == 0:
            candidates.add(parsed_int // 100)
        if parsed_int >= 1000000 and parsed_int % 1000 == 0:
            candidates.add(parsed_int // 1000)

        s = str(parsed_int)
        # Heuristic: remove a single '1' at early positions (0..2)
        for remove_idx in range(0, min(3, len(s))):
            if s[remove_idx] == "1":
                try:
                    shorter = int(s[:remove_idx] + s[remove_idx + 1 :])
                    candidates.add(shorter)
                except Exception:
                    pass

        # If it starts with double '1' (e.g. 111678), try removing the first '1'
        if len(s) > 4 and s.startswith("11"):
            try:
                candidates.add(int(s[1:]))
            except Exception:
                pass

    # If token had commas, also try the common thousands interpretation
    if "," in token:
        try:
            alt = int(token.replace(",", ""))
            candidates.add(alt)
            if alt >= 100000 and alt % 100 == 0:
                candidates.add(alt // 100)
        except Exception:
            pass

    # Plausibility filter: keep reasonable receipt amounts
    final = sorted({v for v in candidates if isinstance(v, int) and 50 <= v <= 100000})

    return [float(v) for v in final]


def _extract_amount_candidates(line: str) -> list[float]:
    """Compatibility wrapper: extract numeric amount candidates from a line.

    Uses the token parser `_parse_jpy_amount_candidates` to produce a
    de-duplicated, sorted list of plausible yen amounts.
    """
    if _line_should_be_excluded_for_amounts(line):
        return []

    candidates: list[float] = []
    for token in AMOUNT_TOKEN_RE.findall(line):
        try:
            candidates.extend(_parse_jpy_amount_candidates(token, line))
        except Exception:
            # Be defensive: ignore parse errors for a single token
            continue

    # Deduplicate and keep plausible integer-yen values
    seen: set[int] = set()
    final: list[float] = []
    for v in candidates:
        try:
            iv = int(round(float(v)))
        except Exception:
            continue
        if iv in seen:
            continue
        seen.add(iv)
        if 0 < iv <= 500000:
            final.append(float(iv))

    return sorted(final)
def _score_amount_candidate(line: str, amount: float) -> int:
    score = 0
    normalized = _normalize_matching_text(line)

    # Strong indicator: line contains amount keywords
    if _looks_like_amount_line(line):
        score += 100

    # Bonus for explicit amount keywords (order matters)
    for index, keyword in enumerate(AMOUNT_KEYWORDS):
        if keyword in normalized:
            score += max(0, 80 - index * 2)

    # Japanese amount indicators
    for suffix in JAPANESE_AMOUNT_SUFFIXES:
        if suffix in line:
            score += 30
            break

    # Penalty for lines containing quantity keywords (product lines)
    if any(keyword in normalized for keyword in QUANTITY_KEYWORDS):
        score -= 100

    # Amount range scoring
    if 50 <= amount <= 100000:  # Reasonable receipt range for retail receipts
        score += 50
    elif 100000 < amount <= 500000:  # Department store / large purchase
        score += 20
    elif amount > 1000000:  # Suspiciously large
        score -= 200
    elif amount < 50:  # Too small to be total
        score -= 50

    # Prefer round numbers (multiples of 10/100)
    if amount % 1 == 0:
        score += 5
    if amount % 10 == 0:
        score += 3
    if amount % 100 == 0:
        score += 2

    # Penalty for unrealistic decimal amounts (most Japanese amounts are integers)
    if amount % 1 != 0:
        score -= 20

    # Bonus for lines starting with currency symbol
    if normalized.startswith("¥") or normalized.startswith("￥"):
        score += 40

    # Bonus if line contains only currency-related content
    if re.match(r"^[¥￥\d,\.]+$", normalized):
        score += 20

    # Penalize serial-like amount tokens such as ¥011633 that often represent
    # receipt/order numbers instead of payable totals.
    if re.search(r"[¥￥]0\d{4,}", normalized):
        score -= 70
    if "#" in line or "＃" in line:
        score -= 30

    return score


def _build_cleaned_line_layout_meta(lines: list[str], line_entries: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    """Map cleaned lines to OCR layout metadata.

    Each item contains height/page/y-center/y-max for later scoring.
    """
    meta: list[dict[str, float | int]] = []
    entry_idx = 0

    for raw_line in lines:
        if not raw_line.strip():
            continue

        if re.match(r"^\[page\s+\d+\]$", raw_line.strip(), flags=re.IGNORECASE):
            meta.append({"height": 0.0, "page_index": -1, "y_center": 0.0, "y_max": 0.0})
            continue

        if entry_idx < len(line_entries):
            entry = line_entries[entry_idx]
            bbox = entry.get("bbox")
            page_index = int(entry.get("page_index", 0))
            if isinstance(bbox, tuple) and len(bbox) == 4:
                y1 = float(bbox[1])
                y2 = float(bbox[3])
                meta.append(
                    {
                        "height": max(y2 - y1, 0.0),
                        "page_index": page_index,
                        "y_center": (y1 + y2) / 2.0,
                        "y_max": y2,
                    }
                )
            else:
                meta.append({"height": 0.0, "page_index": page_index, "y_center": 0.0, "y_max": 0.0})
            entry_idx += 1
        else:
            meta.append({"height": 0.0, "page_index": -1, "y_center": 0.0, "y_max": 0.0})

    return meta


def _extract_total_amount(lines: list[str], line_layout_meta: list[dict[str, float | int]] | None = None) -> float:
    """Extract total amount with improved heuristics and validation."""
    scored_candidates: list[tuple[int, float, int]] = []  # (score, amount, line_index)

    normalized_lines = [_normalize_matching_text(line) for line in lines]
    line_count = max(len(lines), 1)
    layout_meta = line_layout_meta or []
    page_heights: dict[int, list[float]] = {}
    page_y_centers: dict[int, list[float]] = {}

    for item in layout_meta:
        page_idx = int(item.get("page_index", -1))
        h = float(item.get("height", 0.0))
        yc = float(item.get("y_center", 0.0))
        if page_idx < 0:
            continue
        if h > 0:
            page_heights.setdefault(page_idx, []).append(h)
        if yc > 0:
            page_y_centers.setdefault(page_idx, []).append(yc)

    page_height_p75: dict[int, float] = {
        page_idx: float(np.percentile(values, 75))
        for page_idx, values in page_heights.items()
        if values
    }
    page_bottom_gate: dict[int, float] = {
        page_idx: float(np.percentile(values, 60))
        for page_idx, values in page_y_centers.items()
        if values
    }

    normalized_total_keywords = tuple(_normalize_matching_text(k) for k in STRICT_TOTAL_KEYWORDS)

    def _has_total_keyword_near(idx: int) -> bool:
        left = max(0, idx - 1)
        right = min(len(normalized_lines) - 1, idx + 1)
        for i in range(left, right + 1):
            ln = normalized_lines[i]
            if any(key in ln for key in normalized_total_keywords):
                return True
        return False
    
    for line_idx, line in enumerate(lines):
        # Merge context from the previous line so patterns like:
        #   ご利用金額
        #   116.455
        # can still be interpreted as 16,455.
        context_line = line
        if line_idx > 0 and _looks_like_amount_line(lines[line_idx - 1]):
            context_line = f"{lines[line_idx - 1]} {line}"

        if _line_should_be_excluded_for_amounts(context_line):
            continue

        candidates: list[float] = []
        for match in AMOUNT_TOKEN_RE.findall(line):
            candidates.extend(_parse_jpy_amount_candidates(match, context_line))

        # If a strict total keyword is nearby, prefer numeric candidates adjacent
        # to that keyword as the total immediately to avoid misselecting large IDs.
        if _has_total_keyword_near(line_idx) and candidates:
            plausible = [a for a in candidates if 50 <= a <= 100000]
            if plausible:
                # Prefer the largest plausible value (most receipts print totals larger)
                return float(sorted(plausible, reverse=True)[0])

        for amount in candidates:
            score = _score_amount_candidate(context_line, amount)

            # Strong total labels are authoritative signals for payable amount.
            if _has_total_keyword_near(line_idx):
                score += 220
            else:
                # Without nearby total-evidence, heavily downrank to avoid
                # selecting unrelated numbers such as years, IDs or addresses.
                score -= 140

            # Totals are usually printed near the bottom of receipts.
            # Add gradual positional bias toward lower lines.
            position_ratio = line_idx / max(line_count - 1, 1)
            score += int(position_ratio * 45)

            # Joint gate for big-font bonus:
            # 1) same-page height in upper quantile
            # 2) line must be in page bottom 40% region
            if line_idx < len(layout_meta):
                item = layout_meta[line_idx]
                page_idx = int(item.get("page_index", -1))
                current_height = float(item.get("height", 0.0))
                current_y_center = float(item.get("y_center", 0.0))
                p75 = page_height_p75.get(page_idx, 0.0)
                bottom_gate = page_bottom_gate.get(page_idx, 0.0)

                if current_height > 0 and p75 > 0 and current_height >= p75 and current_y_center >= bottom_gate:
                    ratio = current_height / max(p75, 1.0)
                    if ratio >= 1.6:
                        score += 50
                    elif ratio >= 1.3:
                        score += 35
                    else:
                        score += 22

            scored_candidates.append((score, amount, line_idx))

    if not scored_candidates:
        # If no scored candidates found, but a strict total keyword (e.g. 合計)
        # appears nearby a numeric token, prefer that numeric token as total.
        try:
            for idx, line in enumerate(lines):
                nl = _normalize_matching_text(line)
                if any(k in nl for k in normalized_total_keywords):
                    window = [line]
                    if idx + 1 < len(lines):
                        window.append(lines[idx + 1])
                    if idx + 2 < len(lines):
                        window.append(lines[idx + 2])

                    candidates: list[float] = []
                    for wl in window:
                        candidates.extend(_extract_amount_candidates(wl))

                    plausible = [a for a in candidates if 0 < a <= 500000]
                    if plausible:
                        # Prefer the largest plausible nearby value.
                        return float(sorted(plausible, reverse=True)[0])
        except Exception:
            pass

        return 0.0

    # Heuristic: prefer subtotal+tax when an explicit tax label/section exists.
    try:
        for idx, nl in enumerate(normalized_lines):
            if "税額" not in nl and "消費税" not in nl and "%" not in nl:
                continue

            start = max(0, idx - 4)
            end = min(len(lines), idx + 4)
            window = lines[start:end]

            # Collect numeric tokens in the window
            amounts_in_window: list[int] = []
            for wl in window:
                for a in _extract_amount_candidates(wl):
                    try:
                        amounts_in_window.append(int(round(float(a))))
                    except Exception:
                        continue

            if not amounts_in_window:
                continue

            # Identify tax candidates close to the tax label (idx..idx+2)
            tax_candidates: list[int] = []
            for j in range(idx, min(idx + 3, len(lines))):
                for a in _extract_amount_candidates(lines[j]):
                    try:
                        tax_candidates.append(int(round(float(a))))
                    except Exception:
                        continue

            if not tax_candidates:
                # fallback: consider small numbers in window as possible tax
                tax_candidates = [a for a in amounts_in_window if 0 < a <= 5000]

            for tax in sorted(set(tax_candidates)):
                # Look backwards for a plausible subtotal near the tax label
                subtotal_candidates: list[int] = []
                for j in range(max(0, idx - 4), idx):
                    for a in _extract_amount_candidates(lines[j]):
                        try:
                            ia = int(round(float(a)))
                        except Exception:
                            continue
                        if ia > 0 and ia != tax and ia <= 5000:
                            subtotal_candidates.append(ia)

                if not subtotal_candidates:
                    subtotal_candidates = [a for a in amounts_in_window if a != tax and a <= 5000]

                for sub in sorted(set(subtotal_candidates)):
                    total = sub + tax
                    if 0 < total <= 5000:
                        # Prefer when subtotal label is nearby or the computed total appears explicitly
                        if total in amounts_in_window or any("小計" in _normalize_matching_text(l) for l in window):
                            return float(total)
    except Exception:
        # Defensive: on any error, continue with the standard scoring below.
        pass

    # Sort by score (desc), then amount (desc) to prefer higher-scoring, larger amounts
    scored_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    
    top_score, top_amount, _ = scored_candidates[0]

    # If both tendered cash and change can be identified, derive payable total first.
    # This avoids selecting change (お釣り) as total.
    payment_total, payment_tendered, payment_change = _extract_total_from_payment_summary(lines)
    if payment_total > 0:
        if abs(top_amount - payment_change) <= 1:
            return payment_total
        if top_amount >= payment_total * 1.5:
            return payment_total
        if 50 <= payment_total <= 100000 and top_score < 170:
            return payment_total

    # In ambiguous cases, avoid blindly preferring larger values.
    # Receipt totals are usually under 100,000 JPY, and OCR often inserts an
    # extra leading digit (e.g., 16,455 -> 116,455). Prefer the lower, plausible
    # candidate when scores are very close.
    if len(scored_candidates) > 1:
        second_score, second_amount, _ = scored_candidates[1]
        if abs(top_score - second_score) <= 15:
            if top_amount > 100000 and 100 <= second_amount <= 100000:
                return second_amount
            if second_amount > 100000 and 100 <= top_amount <= 100000:
                return top_amount

        # Guard against OCR-inserted leading '1' inflation:
        # e.g. true 1,816 but candidate 11,816 appears due to noisy prefix.
            top_int = int(round(top_amount))
            for alt_score, alt_amount, _ in scored_candidates[1:6]:
                alt_int = int(round(alt_amount))
                if alt_int <= 0:
                    continue

                top_text = str(top_int)
                alt_text = str(alt_int)

                # Detect a single '1' inserted at an early position (index 0..2),
                # e.g. true 2280 -> OCR produced 21280 (extra '1' at index 1).
                has_single_one_inflation = False
                if len(top_text) == len(alt_text) + 1:
                    for remove_idx in range(0, min(3, len(top_text))):
                        if top_text[remove_idx] == "1" and top_text[:remove_idx] + top_text[remove_idx + 1 :] == alt_text:
                            has_single_one_inflation = True
                            break

                if not has_single_one_inflation:
                    continue

                # Require meaningful OCR support for the smaller value to avoid
                # over-correcting legitimate 5-digit totals.
                small_hits = sum(1 for _, amount, _ in scored_candidates if int(round(amount)) == alt_int)
                # Relaxed thresholds: allow a single supporting hit and a slightly
                # larger score gap to catch common OCR-inserted '1' errors like
                # 21280 -> 2280 while avoiding over-correction.
                if small_hits >= 1 and (top_score - alt_score) <= 45 and 50 <= alt_amount <= 100000:
                    return alt_amount

        # Final fallback: if max amount is an obvious outlier, treat it as suspicious
        # and prefer the next plausible candidate.
        if top_amount > 0 and second_amount > 0:
            suspicious_ratio = top_amount / max(second_amount, 1)
            if top_amount > 100000 and suspicious_ratio >= 3.0 and 100 <= second_amount <= 100000:
                return second_amount

    return top_amount


def _extract_total_from_payment_summary(lines: list[str]) -> tuple[float, float, float]:
    """Infer payable total as tendered amount minus change amount.

    Returns: (inferred_total, tendered, change)
    """
    payment_indexes: list[int] = []
    change_indexes: list[int] = []
    for idx, line in enumerate(lines):
        normalized = _normalize_matching_text(line)
        if any(keyword in normalized for keyword in PAYMENT_TENDER_KEYWORDS):
            payment_indexes.append(idx)
        if any(keyword in normalized for keyword in PAYMENT_CHANGE_KEYWORDS):
            change_indexes.append(idx)

    if not payment_indexes or not change_indexes:
        return 0.0, 0.0, 0.0

    block_start = max(min(payment_indexes + change_indexes) - 6, 0)
    block_end = min(max(payment_indexes + change_indexes) + 2, len(lines) - 1)

    amount_entries: list[tuple[int, float]] = []
    for idx in range(block_start, block_end + 1):
        for amount in _extract_amount_candidates(lines[idx]):
            if amount >= 50:
                amount_entries.append((idx, amount))

    if len(amount_entries) < 2:
        return 0.0, 0.0, 0.0

    tendered = max(amount for _, amount in amount_entries)

    change_pool = [(idx, amount) for idx, amount in amount_entries if amount < tendered]
    if not change_pool:
        return 0.0, tendered, 0.0

    def _change_rank(item: tuple[int, float]) -> tuple[int, float]:
        idx, amount = item
        nearest_change_distance = min(abs(idx - change_idx) for change_idx in change_indexes)
        return (nearest_change_distance, -amount)

    _, change = sorted(change_pool, key=_change_rank)[0]

    if tendered <= change:
        return 0.0, tendered, change

    inferred_total = tendered - change
    if inferred_total < 50 or inferred_total > 100000:
        return 0.0, tendered, change

    return inferred_total, tendered, change


def _extract_tax_amount(lines: list[str], precomputed_total_amount: float | None = None) -> float:
    """Extract tax amount from lines with support for 消費税 keywords and mixed tax rates."""

    total_amount = precomputed_total_amount if precomputed_total_amount is not None else _extract_total_amount(lines)
    tax_candidates: list[tuple[int, float]] = []  # (confidence_score, amount)
    seen_tax_rates: list[int] = []

    for idx, line in enumerate(lines):
        if "税" not in line and "%" not in line and "％" not in line:
            continue

        # Exclude lines with excluded keywords
        if _line_should_be_excluded_for_amounts(line):
            continue

        normalized = _normalize_matching_text(line)
        
        # Detect tax rates (8%, 10%, 軽減税率, etc.)
        rate_match = TAX_RATE_RE.search(normalized)
        if rate_match:
            try:
                rate_val = int(rate_match.group(1) or rate_match.group(2))
                seen_tax_rates.append(rate_val)
            except (ValueError, AttributeError):
                pass

        # Pull numeric candidates from this line and a small look-ahead window,
        # because OCR often places amount on the next line after the tax label.
        window_lines = [line]
        if idx + 1 < len(lines):
            window_lines.append(lines[idx + 1])
        if idx + 2 < len(lines):
            window_lines.append(lines[idx + 2])

        candidates: list[float] = []
        for candidate_line in window_lines:
            candidates.extend(_extract_amount_candidates(candidate_line))

        # Also capture small standalone integers (e.g. "10") that the
        # general amount parser filters out; these often represent small tax
        # amounts located on the next line after a tax label.
        try:
            for candidate_line in window_lines:
                for m in re.findall(r'(?<!\d)(\d{1,2})(?!\d)', candidate_line):
                    try:
                        iv = int(m)
                        if iv > 0 and iv not in candidates:
                            candidates.append(float(iv))
                    except Exception:
                        continue
        except Exception:
            pass

        if not candidates:
            continue

        # If small numeric candidates (e.g., 10) appear near explicit tax labels,
        # treat them as tax amounts (common on receipts where tax is a small yen value).
        small_candidates = [value for value in candidates if 0 < value < 50]
        if small_candidates and any(kw in normalized for kw in TAX_AMOUNT_KEYWORDS):
            for amount in small_candidates:
                tax_candidates.append((200, amount))
            continue

        # Prefer monetary values (>=50) over tax-rate tokens (8, 10%)
        monetary = [value for value in candidates if value >= 50]

        if monetary:
            for amount in monetary:
                # Prioritize lines with explicit tax keywords
                score = 70
                if any(kw in normalized for kw in TAX_AMOUNT_KEYWORDS):
                    score = 120  # 消費税, 税額 etc. are strong signals
                elif "税" in line:
                    score = 100
                elif any(kw in line for kw in ("金", "額")):
                    score = 85

                # A tax amount larger than total is very likely a misread taxable base.
                if total_amount > 0 and amount > total_amount:
                    score -= 120

                # Typical tax amount is usually <= 30% of total for JP receipts.
                if total_amount > 0 and amount > total_amount * 0.3:
                    score -= 60

                # Very large values are usually not tax amount fields.
                if amount > 100000:
                    score -= 80

                tax_candidates.append((score, amount))
        else:
            # If only small numbers, check if they look like percentages (8, 10, etc.)
            for val in candidates:
                if 5 <= val <= 15:  # Likely a percentage
                    # Don't use this, it's probably a tax rate not an amount
                    continue

    if not tax_candidates:
        if total_amount <= 0 or not seen_tax_rates:
            return 0.0

        # Fallback: estimate tax from total when only rate is visible.
        # For tax-included receipts: tax = total * rate / (100 + rate)
        # If multiple rates, use the higher rate (typically 10% for general goods)
        rate = max(seen_tax_rates)
        estimated = _inclusive_tax_from_total(total_amount, rate)
        return float(max(estimated, 0))

    # Sort by confidence and take the best one
    tax_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_amount = tax_candidates[0]

    # If the selected tax amount is larger than the total, it's very likely
    # an OCR misread (e.g., an unrelated large ID). In that case prefer an
    # estimate from any detected tax rates; if none are detected, return 0.
    if total_amount > 0 and best_amount > total_amount:
        if seen_tax_rates:
            rate = max(seen_tax_rates)
            estimated = _inclusive_tax_from_total(total_amount, rate)
            return float(max(estimated, 0))
        return 0.0

    if best_score < 20 and total_amount > 0 and seen_tax_rates:
        rate = max(seen_tax_rates)
        estimated = _inclusive_tax_from_total(total_amount, rate)
        return float(max(estimated, 0))

    return best_amount


def _normalize_rounding_mode(mode: str | None) -> str:
    return mode if mode in ROUNDING_MODE_MAP else "floor"


def _round_to_yen(value: float | int | str | Decimal | None, mode: str | None) -> int:
    if value in (None, ""):
        return 0
    normalized_mode = _normalize_rounding_mode(mode)
    rounding = ROUNDING_MODE_MAP[normalized_mode]
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=rounding))


def _inclusive_tax_from_total(total_amount: float, rate_percent: int) -> int:
    if total_amount <= 0 or rate_percent < 0:
        return 0
    raw_tax = Decimal(str(total_amount)) * Decimal(str(rate_percent)) / (Decimal("100") + Decimal(str(rate_percent)))
    rounding_mode = os.getenv("JPY_ROUNDING_MODE", "floor").strip().lower()
    return _round_to_yen(raw_tax, rounding_mode)


def _exclusive_tax_from_subtotal(subtotal_amount: float, rate_percent: int) -> int:
    if subtotal_amount <= 0 or rate_percent < 0:
        return 0
    raw_tax = Decimal(str(subtotal_amount)) * Decimal(str(rate_percent)) / Decimal("100")
    rounding_mode = os.getenv("JPY_ROUNDING_MODE", "floor").strip().lower()
    return _round_to_yen(raw_tax, rounding_mode)


def _extract_change_amount(lines: list[str]) -> float:
    """Extract change amount (お釣り / 釣銭) from receipt."""
    for idx, line in enumerate(lines):
        normalized = _normalize_matching_text(line)
        if any(keyword in normalized for keyword in PAYMENT_CHANGE_KEYWORDS):
            # Look at this line and the next few lines for amounts
            window_lines = [line]
            if idx + 1 < len(lines):
                window_lines.append(lines[idx + 1])
            if idx + 2 < len(lines):
                window_lines.append(lines[idx + 2])

            for candidate_line in window_lines:
                candidates = _extract_amount_candidates(candidate_line)
                if candidates:
                    # Return the first substantial amount found
                    for amount in candidates:
                        if amount >= 50:
                            return amount
    return 0.0


def _extract_payment_info(lines: list[str]) -> dict[str, Any]:
    """Extract payment-related information: method, tendered amount, change, etc."""
    payment_info = {
        "method": "unknown",
        "tendered_amount": 0.0,
        "change_amount": 0.0,
    }

    # Extract change amount if present
    change_amount = _extract_change_amount(lines)
    if change_amount > 0:
        payment_info["change_amount"] = change_amount

    # Detect payment method
    for line in lines:
        normalized = _normalize_matching_text(line)
        
        for method_kw in PAYMENT_METHOD_KEYWORDS:
            if _normalize_matching_text(method_kw) in normalized:
                if "\u73fe\u91d1" in method_kw:
                    payment_info["method"] = "cash"
                elif "\u30af\u30ec\u30b8\u30c3\u30c8" in method_kw or "\u30ab\u30fc\u30c9" in method_kw:
                    payment_info["method"] = "card"
                elif "\u96fb\u5b50" in method_kw:
                    payment_info["method"] = "electronic"
                elif "QR" in method_kw or "PayPay" in method_kw:
                    payment_info["method"] = "qr_code"
                elif "Suica" in method_kw or "Pasmo" in method_kw:
                    payment_info["method"] = "ic_card"
                break

    # If change amount exists and no explicit tender detected, infer from change + total
    if change_amount > 0 and payment_info["method"] == "cash":
        total = _extract_total_amount(lines)
        if total > 0:
            # Likely tendered amounts in Japanese receipts: 1000, 2000, 5000, 10000
            possible_tenders = [1000, 2000, 5000, 10000, 20000, 50000]
            for tender in possible_tenders:
                if tender - change_amount > total - 5 and tender - change_amount < total + 5:
                    payment_info["tendered_amount"] = float(tender)
                    break

    return payment_info


def _detect_tax_rates(lines: list[str]) -> list[int]:
    """Detect tax rates from receipt lines, including from keywords like 消費税."""
    seen_rates: set[int] = set()
    
    for line in lines:
        normalized = _normalize_matching_text(line)
        
        # Check explicit patterns with % or ％
        for pattern in [r'10%', r'10％', r'8%', r'8％']:
            if pattern.replace('％', '').replace('%', '') in normalized:
                rate = int(pattern.replace('％', '').replace('%', ''))
                seen_rates.add(rate)
        
        # Check explicit "number + 税" pattern
        match = TAX_RATE_RE.search(normalized)
        if match:
            # Extract all groups and find non-empty ones
            for group_idx, group in enumerate(match.groups()):
                if group and group.isdigit():
                    rate = int(group)
                    if rate in (8, 10):
                        seen_rates.add(rate)
        
        # Heuristic: if line contains 消費税 or 内消費税, assume one or both rates are involved
        if any(kw in line for kw in ('消費税', '内消費税', '外消費税', '税額')):
            # Default to common JP rates: 10% for general, 8% for reduced items
            # If text also mentions specific rates, those will be caught above
            if not seen_rates:
                # Look for patterns like "10対象" or "8対象" nearby
                if any(p in normalized for p in ('10', '10対', '10%')):
                    seen_rates.add(10)
                elif any(p in normalized for p in ('8', '8対', '8%')):
                    seen_rates.add(8)
                else:
                    # Default assumption: if we see 消費税 but no rate, assume standard 10%
                    seen_rates.add(10)
    
    return sorted(seen_rates)


def _collect_amount_entries(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[int, int]] = set()

    for idx, line in enumerate(lines):
        normalized = _normalize_matching_text(line)
        if _line_should_be_excluded_for_amounts(line):
            continue

        for amount in _extract_amount_candidates(line):
            normalized_amount = int(amount)
            if normalized_amount < 1 or normalized_amount > 500000:
                continue
            dedupe_key = (idx, normalized_amount)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            entries.append(
                {
                    "line_index": idx,
                    "amount": float(normalized_amount),
                    "line": line,
                    "normalized": normalized,
                }
            )

    return entries


def _amount_exists(entries: list[dict[str, Any]], target: float, tolerance: float = 1.0) -> bool:
    return any(abs(entry["amount"] - target) <= tolerance for entry in entries)


def _looks_like_serial_line(line: str) -> bool:
    normalized = _normalize_matching_text(line)
    if not normalized:
        return False
    if SERIAL_HINT_RE.search(normalized) is None:
        return False
    # If explicit monetary markers exist, treat as monetary line instead of serial.
    if any(marker in line for marker in ("¥", "￥", "円")):
        return False
    return True


def _build_amount_role_candidates(lines: list[str]) -> list[dict[str, Any]]:
    entries = _collect_amount_entries(lines)
    rates = _detect_tax_rates(lines)
    if len(entries) < 2 or not rates:
        return []

    payment_indexes = [idx for idx, line in enumerate(lines) if any(keyword in _normalize_matching_text(line) for keyword in PAYMENT_TENDER_KEYWORDS)]
    change_indexes = [idx for idx, line in enumerate(lines) if any(keyword in _normalize_matching_text(line) for keyword in PAYMENT_CHANGE_KEYWORDS)]

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int, int, int]] = set()

    for larger in entries:
        for smaller in entries:
            if larger["line_index"] == smaller["line_index"] and larger["amount"] == smaller["amount"]:
                continue
            if larger["amount"] <= smaller["amount"]:
                continue

            candidate_total = larger["amount"] - smaller["amount"]
            if candidate_total < 50 or candidate_total > 100000:
                continue

            for rate in rates:
                candidate_tax = float(_inclusive_tax_from_total(candidate_total, rate))
                candidate_subtotal = max(candidate_total - candidate_tax, 0.0)

                nearest_payment_distance = (
                    min(abs(int(larger["line_index"]) - idx) for idx in payment_indexes)
                    if payment_indexes
                    else 999
                )
                nearest_change_distance = (
                    min(abs(int(smaller["line_index"]) - idx) for idx in change_indexes)
                    if change_indexes
                    else 999
                )

                # Hard constraints to reduce false positives.
                if smaller["amount"] < MIN_CHANGE_AMOUNT_FOR_ROLE:
                    normalized_small = smaller["normalized"]
                    has_explicit_change_keyword = any(
                        keyword in normalized_small for keyword in PAYMENT_CHANGE_KEYWORDS
                    )
                    if not (nearest_change_distance == 0 and has_explicit_change_keyword):
                        continue
                if _looks_like_serial_line(str(larger["line"])) and nearest_payment_distance > 2:
                    continue
                if _looks_like_serial_line(str(smaller["line"])) and nearest_change_distance > 2:
                    continue
                if candidate_total >= larger["amount"]:
                    continue
                if candidate_total > 1000 and smaller["amount"] <= 5:
                    continue

                score = 0
                score_breakdown: dict[str, int] = {}
                normalized_large = larger["normalized"]
                normalized_small = smaller["normalized"]

                if any(keyword in normalized_large for keyword in PAYMENT_TENDER_KEYWORDS):
                    score += 100
                    score_breakdown["tendered_keyword"] = 100
                if any(keyword in normalized_small for keyword in PAYMENT_CHANGE_KEYWORDS):
                    score += 100
                    score_breakdown["change_keyword"] = 100

                if payment_indexes:
                    if nearest_payment_distance <= 2:
                        score += 80
                        score_breakdown["tendered_near_payment_line"] = 80
                    elif nearest_payment_distance <= 6:
                        score += 45
                        score_breakdown["tendered_near_payment_line"] = 45

                if change_indexes:
                    if nearest_change_distance <= 2:
                        score += 80
                        score_breakdown["change_near_change_line"] = 80
                    elif nearest_change_distance <= 6:
                        score += 45
                        score_breakdown["change_near_change_line"] = 45

                if _amount_exists(entries, candidate_subtotal):
                    score += 90
                    score_breakdown["subtotal_observed"] = 90
                if _amount_exists(entries, candidate_tax):
                    score += 60
                    score_breakdown["tax_observed"] = 60
                if _amount_exists(entries, candidate_total):
                    score += 40
                    score_breakdown["total_observed"] = 40

                exclusive_tax = float(_exclusive_tax_from_subtotal(candidate_subtotal, rate))
                if abs(exclusive_tax - candidate_tax) <= 1:
                    score += 15
                    score_breakdown["tax_formula_consistency"] = 15

                if score < 120:
                    continue

                key = (
                    int(larger["amount"]),
                    int(smaller["amount"]),
                    int(candidate_total),
                    int(candidate_tax),
                    int(candidate_subtotal),
                    int(rate),
                )
                if key in seen:
                    continue
                seen.add(key)

                candidates.append(
                    {
                        "tendered": float(larger["amount"]),
                        "change": float(smaller["amount"]),
                        "total": float(candidate_total),
                        "tax": float(candidate_tax),
                        "subtotal": float(candidate_subtotal),
                        "tax_rate": float(rate),
                        "tax_rate_label": f"{rate}%",
                        "score": int(score),
                        "score_breakdown": score_breakdown,
                        "line_refs": {
                            "tendered_line": int(larger["line_index"]),
                            "change_line": int(smaller["line_index"]),
                        },
                    }
                )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:MAX_ROLE_CANDIDATES]


def _infer_amounts_by_relationships(lines: list[str]) -> dict[str, Any] | None:
    candidates = _build_amount_role_candidates(lines)
    if not candidates:
        return None

    best = candidates[0]
    return {
        "total_amount": best["total"],
        "tax_amount": best["tax"],
        "subtotal_excl_tax": best["subtotal"],
        "tax_rate_label": best["tax_rate_label"],
        "score": best["score"],
    }


def _clean_ocr_text(text: str) -> str:
    """Clean and normalize OCR-recognized text."""
    # Fix common full-width character issues
    text = text.translate(_FULLWIDTH_TRANSLATION)

    # Fix common OCR confusions for Japanese receipts
    replacements = {
        "オソライソ": "お支払い",  # 支払い
        "コ": "",  # Isolated kana
        "こ木リ用金客貞": "ご利用金額",
        "こ保省上": "ご保管上",
        "けイ": "PIN",
        "布": "",  # Noise
        "＃": "¥",  # Wrong symbol
        "+": "",  # Time separator confusion
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text.strip()


def _normalize_registration_whitelist_text(text: str) -> str:
    normalized = text.translate(_FULLWIDTH_TRANSLATION).upper()
    replacements = {
        " ": "",
        "\u3000": "",
        "-": "",
        "−": "",
        "ー": "",
        "ｰ": "",
        ".": "",
        ",": "",
        ":": "",
        ";": "",
        "'": "",
        '"': "",
        "(": "",
        ")": "",
        "[": "",
        "]": "",
        "I": "1",
        "L": "1",
        "|": "1",
        "O": "0",
        "Q": "0",
        "S": "5",
        "B": "8",
    }
    for bad, good in replacements.items():
        normalized = normalized.replace(bad, good)
    return "".join(ch for ch in normalized if ch in REGISTRATION_WHITELIST)


def _parse_registration_candidate(candidate: str) -> str | None:
    """Return normalized T-prefixed registration number from a nearby OCR candidate.

    Japanese qualified invoice numbers (適格請求書発行事業者登録番号) are always
    exactly 13 digits, prefixed with 'T' on the receipt. OCR frequently
    misreads 'T' as '1', producing a 14-digit string whose first digit is '1'.
    This parser applies a strict whitelist of T0123456789 before normalizing.
    """
    normalized = _normalize_registration_whitelist_text(candidate)
    if re.fullmatch(r"T\d{13}", normalized):
        return normalized
    if re.fullmatch(r"\d{13}", normalized):
        return f"T{normalized}"
    if re.fullmatch(r"1\d{13}", normalized):
        return f"T{normalized[1:]}"
    return None


def _extract_registration_number_from_lines(lines: list[str]) -> str | None:
    """Extract registration number from main OCR lines near the 登録番号 label.

    The number is 13 digits, always prefixed with 'T' on the physical receipt.
    OCR commonly:
      - Reads 'T' as '1', producing 14 contiguous digits
      - Places the number on the line *after* the '登録番号' label
    """
    for idx, line in enumerate(lines):
        normalized = _normalize_matching_text(line)

        # Pattern A: literal T + 13 digits on the same line  e.g. "T1234567890123"
        match = re.search(r"T\d{13}", line)
        if match:
            return match.group(0)

        # Pattern B: '登録番号' label — number may be on this line or the next
        if any(keyword in normalized for keyword in REGISTRATION_LABEL_KEYWORDS):
            # Try same line first
            match = re.search(r"([T1]?\d{13,14})", _normalize_registration_whitelist_text(line))
            if match:
                result = _parse_registration_candidate(match.group(1))
                if result:
                    return result
            # Try next line (most common layout on Japanese receipts)
            if idx + 1 < len(lines):
                next_line = _normalize_registration_whitelist_text(lines[idx + 1])
                match = re.search(r"([T1]?\d{13,14})", next_line)
                if match:
                    result = _parse_registration_candidate(match.group(1))
                    if result:
                        return result

    return None


def _build_registration_roi_candidates(image_np: np.ndarray) -> list[np.ndarray]:
    image = Image.fromarray(image_np).convert("L")
    image = ImageOps.autocontrast(image)
    upscaled = image.resize((max(image.width * 3, 1), max(image.height * 3, 1)))

    candidates: list[np.ndarray] = [np.array(upscaled.convert("RGB"))]
    for threshold in (135, 155, 175, 195):
        thresholded = upscaled.point(lambda px, t=threshold: 255 if px > t else 0, mode="1")
        candidates.append(np.array(thresholded.convert("RGB")))

    inverted = ImageOps.invert(upscaled)
    for threshold in (135, 165, 195):
        thresholded = inverted.point(lambda px, t=threshold: 255 if px > t else 0, mode="1")
        candidates.append(np.array(thresholded.convert("RGB")))

    return candidates


def _extract_registration_number_from_roi(
    line_entries: list[dict[str, Any]],
    images_np: list[np.ndarray],
) -> tuple[str | None, str | None, list[str]]:
    candidates: list[str] = []

    if _ocr_engine is None:
        return None, None, candidates

    def _record_candidate(raw_candidate: str) -> None:
        parsed = _parse_registration_candidate(raw_candidate)
        normalized = _normalize_registration_whitelist_text(raw_candidate)
        marker = "hit" if parsed else "miss"
        details = f"{marker}|raw={raw_candidate}|normalized={normalized}|parsed={parsed or '-'}"
        if details not in candidates:
            candidates.append(details)

    def _build_precise_registration_roi(
        entries: list[dict[str, Any]],
        label_index: int,
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int] | None:
        label_entry = entries[label_index]
        lx1, ly1, lx2, ly2 = label_entry["bbox"]
        page_index = int(label_entry["page_index"])

        label_height = max(ly2 - ly1, 1)
        label_center_y = (ly1 + ly2) / 2

        next_entries: list[dict[str, Any]] = []
        for entry in entries[label_index + 1:]:
            if int(entry["page_index"]) != page_index:
                continue
            ex1, ey1, ex2, ey2 = entry["bbox"]
            entry_center_y = (ey1 + ey2) / 2

            # Keep near lines only: just below label and within two-line vertical window.
            if entry_center_y < label_center_y - label_height * 0.3:
                continue
            if entry_center_y > label_center_y + label_height * 3.2:
                continue
            next_entries.append(entry)
            if len(next_entries) >= 2:
                break

        roi_entries = [label_entry, *next_entries]
        if not roi_entries:
            return None

        xs1 = [entry["bbox"][0] for entry in roi_entries]
        ys1 = [entry["bbox"][1] for entry in roi_entries]
        xs2 = [entry["bbox"][2] for entry in roi_entries]
        ys2 = [entry["bbox"][3] for entry in roi_entries]

        line_height = max(max(ys2) - min(ys1), label_height)
        pad_x = max(int(line_height * 0.8), 12)
        pad_y = max(int(label_height * 0.45), 8)

        roi_left = max(min(xs1) - pad_x, 0)
        roi_right = min(max(xs2) + pad_x, image_width)
        roi_top = max(min(ys1) - pad_y, 0)
        roi_bottom = min(max(ys2) + pad_y, image_height)

        if roi_right <= roi_left or roi_bottom <= roi_top:
            return None

        return roi_left, roi_top, roi_right, roi_bottom

    for idx, entry in enumerate(line_entries):
        normalized = _normalize_matching_text(entry["text"])
        if not any(keyword in normalized for keyword in REGISTRATION_LABEL_KEYWORDS):
            continue

        page_index = int(entry["page_index"])
        if page_index >= len(images_np):
            continue

        image_np = images_np[page_index]
        height, width = image_np.shape[:2]
        roi_bounds = _build_precise_registration_roi(line_entries, idx, width, height)
        if roi_bounds is None:
            continue
        roi_left, roi_top, roi_right, roi_bottom = roi_bounds

        roi = image_np[roi_top:roi_bottom, roi_left:roi_right]
        if roi.size == 0:
            continue

        candidate_images = [roi, *_build_registration_roi_candidates(roi)]
        for candidate_image in candidate_images:
            roi_result = _ocr_engine.ocr(candidate_image, cls=True)
            roi_entries = _extract_line_entries(roi_result)
            roi_texts = [entry["text"] for entry in roi_entries]

            for roi_text in roi_texts:
                _record_candidate(roi_text)
                parsed = _parse_registration_candidate(roi_text)
                if parsed:
                    return parsed, "roi", candidates

            combined = _normalize_registration_whitelist_text("".join(roi_texts))
            _record_candidate(combined)
            parsed = _parse_registration_candidate(combined)
            if parsed:
                return parsed, "roi", candidates

    return None, None, candidates


def _extract_registration_number(
    lines: list[str],
    line_entries: list[dict[str, Any]],
    images_np: list[np.ndarray],
) -> tuple[str | None, str | None, list[str]]:
    roi_candidate, roi_source, roi_candidates = _extract_registration_number_from_roi(line_entries, images_np)
    if roi_candidate:
        return roi_candidate, roi_source, roi_candidates

    line_candidate = _extract_registration_number_from_lines(lines)
    if line_candidate:
        return line_candidate, "line", roi_candidates

    return None, None, roi_candidates


def _extract_telephone(lines: list[str]) -> str | None:
    """Extract telephone number from lines.

    Rules:
    - Require at least one explicit delimiter (-) to avoid matching
      substrings of long digit sequences (registration numbers, etc.)
    - Skip lines whose total digit count is >=13 (likely a registration number)
    - Skip lines labeled as 登録番号
    """
    # Build set of lines known to contain registration numbers to skip
    reg_line_indices: set[int] = set()
    for idx, line in enumerate(lines):
        if "登録番号" in line:
            reg_line_indices.add(idx)
            reg_line_indices.add(idx + 1)  # Also skip the following numeric line
        digit_count = sum(1 for ch in line if ch.isdigit())
        if digit_count >= 13:
            reg_line_indices.add(idx)

    for idx, line in enumerate(lines):
        if idx in reg_line_indices:
            continue

        # Must contain at least one hyphen to be a phone number
        if "-" not in line and "−" not in line:  # noqa: RUF001 (fullwidth hyphen)
            continue

        # Japanese phone patterns (with explicit hyphens required)
        match = re.search(r"(0\d{1,3})-(\d{2,4})-(\d{4})", line)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    return None


def _extract_payment_method(lines: list[str]) -> str | None:
    """Extract payment method from lines, supporting both Japanese and English keywords."""
    payment_keywords = {
        # Japanese keywords
        "現金": "Cash",
        "カード": "Credit Card",
        "クレジット": "Credit Card",
        "クレカ": "Credit Card",
        "電子マネー": "E-money",
        "Suica": "IC Card",
        "WAON": "IC Card",
        "nanaco": "IC Card",
        "オンライン": "Online",
        "Web": "Online",
        "PayPay": "PayPay",
        "LINE Pay": "LINE Pay",
        # English keywords
        "cash": "Cash",
        "credit": "Credit Card",
        "card": "Credit Card",
        "e-money": "E-money",
        "electronic": "E-money",
        "online": "Online",
    }
    
    normalized_text = "\n".join(lines).lower()
    full_text = "\n".join(lines)  # Preserve original for Japanese keyword matching
    
    # First try Japanese keywords (case-sensitive)
    for jp_keyword, method in payment_keywords.items():
        if any(ord(ch) > 127 for ch in jp_keyword):  # Is Japanese
            if jp_keyword in full_text:
                return method
    
    # Then try English keywords or lowercased matching
    for keyword, method in payment_keywords.items():
        if all(ord(ch) <= 127 for ch in keyword):  # Is English/ASCII
            if keyword.lower() in normalized_text:
                return method
    
    return None


def _extract_store_info(lines: list[str]) -> str | None:
    """Extract store name/branch info."""
    # Usually after first merchant line or marked by keywords
    store_keywords = ("店", "支店", "本店", "営業所")
    
    for idx, line in enumerate(lines[1:7], start=1):  # Check first few lines
        if any(kw in line for kw in store_keywords):
            return line.strip()
    
    return None


def _line_is_unlikely_merchant(line: str) -> bool:
    normalized = _normalize_matching_text(line)
    if not normalized:
        return True

    if any(keyword in normalized for keyword in MERCHANT_BLOCKLIST_KEYWORDS):
        return True

    if re.search(r"\d{2,4}[:時]\d{2}", normalized):
        return True
    if re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", normalized):
        return True
    if re.search(r"^\d+$", normalized):
        return True

    digit_count = sum(1 for ch in normalized if ch.isdigit())
    if digit_count >= max(4, len(normalized) // 2):
        return True

    return False


def _looks_like_address_line(normalized: str) -> bool:
    if not normalized:
        return False

    if re.search(r"(都|道|府|県).{0,18}(市|区|町|村)", normalized):
        return True
    if re.search(r"\d{1,4}[-−ー]\d{1,4}[-−ー]\d{1,4}", normalized):
        return True

    hint_hits = sum(1 for kw in ADDRESS_HINT_KEYWORDS if kw in normalized)
    digit_count = sum(1 for ch in normalized if ch.isdigit())
    if hint_hits >= 2 and digit_count >= 2:
        return True

    return False


def _score_merchant_candidate(line: str, position: int) -> int:
    normalized = _normalize_matching_text(line)
    score = 0

    if _line_is_unlikely_merchant(line):
        return -999

    score += max(40 - position * 4, 0)

    if any(keyword in normalized for keyword in MERCHANT_HINT_KEYWORDS):
        score += 120

    if 2 <= len(normalized) <= 24:
        score += 30
    elif len(normalized) > 36:
        score -= 40

    # Prefer Japanese letter-heavy strings over numeric-heavy technical lines.
    jp_chars = sum(1 for ch in normalized if ord(ch) > 127)
    score += min(jp_chars * 4, 60)

    if re.search(r"TEL|電話|登録番号", normalized):
        score -= 120

    if _looks_like_address_line(normalized):
        score -= 140

    if any(keyword in normalized for keyword in ("お客様控え", "控え", "領収証", "領収書", "レシート")):
        score -= 220

    return score


def _extract_merchant_name(lines: list[str]) -> str:
    if not lines:
        return "Unknown Merchant"

    search_scope = lines[:10]
    scored: list[tuple[int, str]] = []
    for idx, line in enumerate(search_scope):
        score = _score_merchant_candidate(line, idx)
        scored.append((score, line.strip()))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_line = scored[0]
    if best_score <= -200:
        return lines[0].strip() or "Unknown Merchant"
    return best_line or "Unknown Merchant"


def _calibrate_confidence(
    avg_confidence: float,
    total_amount: float,
    tax_amount: float,
    merchant: str,
    raw_text: str,
) -> float:
    """Calibrate confidence score based on data quality signals."""
    calibrated = avg_confidence
    
    # Penalty for missing merchant
    if not merchant or merchant == "Unknown Merchant" or len(merchant) < 2:
        calibrated *= 0.7
    
    # Penalty for zero amounts
    if total_amount == 0:
        calibrated *= 0.5
    
    # Penalty if total < subtotal
    if total_amount < (total_amount - tax_amount):
        calibrated *= 0.6
    
    # Penalty for suspiciously high amounts (> 1M yen)
    if total_amount > 1000000:
        calibrated *= 0.8
    
    # Penalty for low average confidence
    if calibrated < 0.6:
        calibrated *= 0.9
    
    # Bonus for reasonable amounts
    if 100 <= total_amount <= 100000:
        calibrated = min(calibrated * 1.1, 0.95)
    
    # Penalty for suspicious raw_text patterns (too many noise characters)
    noise_chars = sum(1 for ch in raw_text if ord(ch) > 127 and ch not in "日月火水木金土年月日時分秒")
    text_len = len(raw_text) or 1
    noise_ratio = noise_chars / text_len
    if noise_ratio > 0.3:  # More than 30% high unicode chars
        calibrated *= (1 - noise_ratio)
    
    return max(0.3, min(calibrated, 0.99))


def _build_normalized_text_lines(
    cleaned_lines: list[str],
    registration_number: str | None,
) -> list[str]:
    normalized_lines = list(cleaned_lines)
    if not registration_number:
        return normalized_lines

    # If registration label exists, force nearby candidate line to the corrected value.
    for idx, line in enumerate(normalized_lines):
        if any(keyword in line for keyword in REGISTRATION_LABEL_KEYWORDS):
            # Same-line case: replace inline token if present.
            normalized_lines[idx] = re.sub(r"T?\d{13,14}", registration_number, normalized_lines[idx], count=1)
            # Next-line case is the most common on receipts.
            if idx + 1 < len(normalized_lines) and re.search(r"T?\d{13,14}", normalized_lines[idx + 1]):
                normalized_lines[idx + 1] = registration_number
            break

    return normalized_lines


def _to_receipt_payload(
    lines: list[str],
    confidences: list[float],
    line_entries: list[dict[str, Any]],
    images_np: list[np.ndarray],
) -> dict:
    raw_text_original = "\n".join(lines)

    # Clean up raw text with improved normalization
    cleaned_lines = [_clean_ocr_text(line) for line in lines if line.strip()]
    cleaned_line_layout_meta = _build_cleaned_line_layout_meta(lines, line_entries)
    raw_text = "\n".join(cleaned_lines)

    merchant = _extract_merchant_name(cleaned_lines)
    
    # Extract store information if available
    store_info = _extract_store_info(cleaned_lines)
    if store_info and store_info != merchant:
        merchant = f"{merchant} {store_info}".strip()

    # Extract date (prioritize YYYY-MM-DD or YYYY年M月D日 format)
    date_val = date.today().isoformat()
    for ln in cleaned_lines:
        # Standard format: YYYY-MM-DD or YYYY/MM/DD
        m = DATE_RE.search(ln)
        if m:
            y, mo, d = m.groups()
            date_val = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            break
        # Japanese format: YYYY年M月D日
        m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", ln)
        if m:
            y, mo, d = m.groups()
            date_val = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            break

    # Extract structured fields
    registration_number, registration_number_source, registration_number_candidates = _extract_registration_number(
        cleaned_lines,
        line_entries,
        images_np,
    )
    normalized_lines = _build_normalized_text_lines(cleaned_lines, registration_number)
    raw_text_normalized = "\n".join(normalized_lines)
    telephone = _extract_telephone(cleaned_lines)
    payment_method = _extract_payment_method(cleaned_lines)

    # Extract amounts with improved heuristics
    total_amount = _extract_total_amount(cleaned_lines, cleaned_line_layout_meta)
    tax_amount = _extract_tax_amount(cleaned_lines, total_amount)
    subtotal = max(total_amount - tax_amount, 0.0)

    # Detect tax rate label from OCR lines
    tax_rate_label = "unknown"
    seen_rates = _detect_tax_rates(cleaned_lines)
    if seen_rates:
        if len(seen_rates) > 1:
            tax_rate_label = "mixed"
        else:
            tax_rate_label = f"{seen_rates[0]}%"

    # Extract payment-related fields
    change_amount = _extract_change_amount(cleaned_lines)
    payment_info = _extract_payment_info(cleaned_lines)

    amount_role_candidates = _build_amount_role_candidates(cleaned_lines)
    amount_role_selected = amount_role_candidates[0] if amount_role_candidates else None
    inferred_amounts = _infer_amounts_by_relationships(cleaned_lines)
    if inferred_amounts and (total_amount <= 0 or tax_amount <= 0):
        if total_amount <= 0:
            total_amount = float(inferred_amounts["total_amount"])
        if tax_amount <= 0:
            tax_amount = float(inferred_amounts["tax_amount"])
        subtotal = max(total_amount - tax_amount, 0.0)
    if inferred_amounts and tax_rate_label == "unknown":
        tax_rate_label = str(inferred_amounts["tax_rate_label"])

    # Calculate base confidence
    base_confidence = sum(confidences) / len(confidences) if confidences else 0.5
    
    # Apply calibration
    confidence = _calibrate_confidence(
        base_confidence,
        total_amount,
        tax_amount,
        merchant,
        raw_text,
    )

    payload = {
        "merchant_name": merchant,
        "transaction_date": date_val,
        "registration_number": registration_number,
        "registration_number_source": registration_number_source,
        "subtotal_excl_tax": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "tax_rate_label": tax_rate_label,
        "payment_method": payment_method,
        "payment_info": payment_info,
        "change_amount": change_amount,
        "telephone": telephone,
        "store_info": store_info,
        "raw_text_original": raw_text_original,
        "raw_text_normalized": raw_text_normalized,
        "raw_text": raw_text,
        "confidence": round(float(confidence), 4),
        "amount_role_selected": amount_role_selected,
        "amount_role_candidates": amount_role_candidates,
    }

    # Strict-keyword override: if a strict total keyword (e.g. 合計) appears and
    # a nearby numeric token exists, prefer that numeric token as the total and
    # recompute tax = total - subtotal when subtotal is present.
    try:
        for idx, line in enumerate(cleaned_lines):
            nl = _normalize_matching_text(line)
            if any(k in nl for k in normalized_total_keywords):
                window = [line]
                if idx + 1 < len(cleaned_lines):
                    window.append(cleaned_lines[idx + 1])
                if idx + 2 < len(cleaned_lines):
                    window.append(cleaned_lines[idx + 2])

                candidates: list[float] = []
                for wl in window:
                    candidates.extend(_extract_amount_candidates(wl))

                plausible = [int(round(float(a))) for a in candidates if a > 0 and a <= 500000]
                if plausible:
                    chosen = sorted(plausible, reverse=True)[0]
                    payload["total_amount"] = float(chosen)
                    # Adjust tax if subtotal exists
                    try:
                        sub = int(round(float(payload.get("subtotal_excl_tax") or 0)))
                        if sub > 0:
                            new_tax = chosen - sub
                            if new_tax >= 0 and new_tax <= 500000:
                                payload["tax_amount"] = float(new_tax)
                    except Exception:
                        pass
                    break
    except Exception:
        pass

    # Consistency enforcement: if we detected both a subtotal and a tax amount
    # but the extracted total differs, prefer subtotal + tax when those values
    # look plausible. This avoids cases where OCR variants pick unrelated large
    # identifiers as the total while subtotal+tax is the real payable amount.
    try:
        s = int(round(float(payload.get("subtotal_excl_tax") or 0)))
        t = int(round(float(payload.get("tax_amount") or 0)))
        tot = int(round(float(payload.get("total_amount") or 0)))
        if s > 0 and t >= 0:
            computed = s + t
            # If difference is significant relative to total, prefer computed.
            if abs(computed - tot) > 1 and computed > 0 and computed <= 500000:
                payload["total_amount"] = float(computed)
                payload["subtotal_excl_tax"] = float(s)
                payload["tax_amount"] = float(t)
    except Exception:
        pass

    # If there is an explicit '小計' line, prefer subtotal from that label
    # and recompute total = subtotal + tax when tax is available and small.
    try:
        detected_sub = None
        for idx, line in enumerate(cleaned_lines):
            if "小計" in _normalize_matching_text(line):
                window = [line]
                if idx + 1 < len(cleaned_lines):
                    window.append(cleaned_lines[idx + 1])
                if idx + 2 < len(cleaned_lines):
                    window.append(cleaned_lines[idx + 2])
                for wl in window:
                    for a in _extract_amount_candidates(wl):
                        try:
                            iv = int(round(float(a)))
                        except Exception:
                            continue
                        if iv > 0 and iv <= 50000:
                            detected_sub = iv
                            break
                    if detected_sub is not None:
                        break
            if detected_sub is not None:
                break

        if detected_sub is not None:
            tax_val = int(round(float(payload.get("tax_amount") or 0)))
            if tax_val >= 0 and tax_val <= 50000:
                payload["subtotal_excl_tax"] = float(detected_sub)
                payload["total_amount"] = float(detected_sub + tax_val)
                payload["tax_amount"] = float(tax_val)
    except Exception:
        pass

    if (
        _registration_candidates_debug_enabled()
        and registration_number_source == "roi"
        and float(payload["confidence"]) <= _registration_candidates_low_confidence_threshold()
        and registration_number_candidates
    ):
        payload["registration_number_candidates"] = registration_number_candidates

    return payload
