#!/usr/bin/env bash

set -u

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000/health}"
#OCR_V3_URL="${OCR_V3_URL:-http://127.0.0.1:8011/health}"
OCR_V4_URL="${OCR_V4_URL:-http://127.0.0.1:8012/health}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:8080}"

FAIL=0

check_json() {
  local name="$1"
  local url="$2"

  if curl -fsS "$url" >/dev/null; then
    echo "[OK]   $name $url"
  else
    echo "[FAIL] $name $url"
    FAIL=1
  fi
}

check_head() {
  local name="$1"
  local url="$2"

  if curl -fsSI "$url" >/dev/null; then
    echo "[OK]   $name $url"
  else
    echo "[FAIL] $name $url"
    FAIL=1
  fi
}

echo "=== RecTax Health Check ==="
check_json backend "$BACKEND_URL"
#check_json paddle-ocr-v3 "$OCR_V3_URL"
check_json paddle-ocr-v4 "$OCR_V4_URL"
check_head frontend "$FRONTEND_URL"

exit "$FAIL"
