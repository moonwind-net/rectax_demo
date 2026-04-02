import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def build_variants(base_img: Image.Image) -> list[tuple[str, Image.Image]]:
    variants: list[tuple[str, Image.Image]] = []
    variants.append(("orig", base_img))
    variants.append(("up2", base_img.resize((base_img.width * 2, base_img.height * 2), Image.Resampling.LANCZOS)))
    variants.append(("up3", base_img.resize((base_img.width * 3, base_img.height * 3), Image.Resampling.LANCZOS)))
    variants.append(("contrast1.8", ImageEnhance.Contrast(base_img).enhance(1.8)))
    variants.append(("sharp_contrast", ImageEnhance.Contrast(base_img.filter(ImageFilter.UnsharpMask(radius=1.8, percent=180, threshold=2))).enhance(1.5)))
    variants.append(("autocontrast", ImageOps.autocontrast(base_img)))

    w, h = base_img.size
    crop_bottom = base_img.crop((0, int(h * 0.45), w, h))
    variants.append(("crop_bottom55_up2", crop_bottom.resize((crop_bottom.width * 2, crop_bottom.height * 2), Image.Resampling.LANCZOS)))

    crop_center = base_img.crop((0, int(h * 0.30), w, int(h * 0.95)))
    variants.append(("crop_center65_up2", crop_center.resize((crop_center.width * 2, crop_center.height * 2), Image.Resampling.LANCZOS)))

    arr = np.array(base_img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    thr = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    variants.append(("clahe_adapt", Image.fromarray(thr).convert("RGB")))

    return variants


def run_ocr(img: Image.Image, name: str) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    payload = buf.getvalue()

    r = requests.post(
        "http://127.0.0.1:8000/ocr/",
        files={"file": (f"{name}.jpg", payload, "image/jpeg")},
        timeout=60,
    )
    r.raise_for_status()

    d = r.json()
    text = d.get("raw_text_normalized") or ""
    text_flat = text.replace(",", "").replace("，", "")
    has_1816 = (
        ("1816" in text_flat)
        or ("1,816" in text)
        or ("\u00a51,816" in text)
        or ("\uffe51,816" in text)
    )
    hits = [ln for ln in text.splitlines() if ("合計" in ln) or ("¥" in ln) or ("￥" in ln) or ("円" in ln)]

    return {
        "variant": name,
        "total_amount": d.get("total_amount"),
        "tax_amount": d.get("tax_amount"),
        "tax_rate_label": d.get("tax_rate_label"),
        "confidence": d.get("confidence"),
        "has_1816": has_1816,
        "hit_count": len(hits),
        "hits": hits[:10],
        "amount_role_candidates": len(d.get("amount_role_candidates") or []),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python enhance_rerun_ocr.py /tmp/input.jpg")
        return 2

    image_path = Path(sys.argv[1])
    if not image_path.exists():
        print(f"missing image: {image_path}")
        return 2

    base_img = Image.open(image_path).convert("RGB")
    variants = build_variants(base_img)

    results: list[dict] = []
    for name, img in variants:
        try:
            results.append(run_ocr(img, name))
        except Exception as exc:  # noqa: BLE001
            results.append({"variant": name, "error": str(exc)})

    print(json.dumps(results, ensure_ascii=False, indent=2))

    ok = [x for x in results if "error" not in x]
    ok.sort(key=lambda x: (int(bool(x.get("has_1816"))), float(x.get("confidence") or 0.0)), reverse=True)
    print("--- top variants ---")
    for item in ok[:5]:
        print(
            f"{item['variant']}: has_1816={item['has_1816']} total={item.get('total_amount')} "
            f"conf={item.get('confidence')} hits={item.get('hit_count')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
