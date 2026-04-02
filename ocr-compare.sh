#!/usr/bin/env bash

set -euo pipefail

IMAGE_PATH="${1:-uploads/IMG20260328121705.jpg}"
#OCR_V3_URL="${OCR_V3_URL:-http://127.0.0.1:8011/ocr/}"
OCR_V4_URL="${OCR_V4_URL:-http://127.0.0.1:8012/ocr/}"

if [ ! -f "$IMAGE_PATH" ]; then
  echo "Image not found: $IMAGE_PATH" >&2
  exit 1
fi

echo "=== image ==="
echo "$IMAGE_PATH"
echo

#echo "=== v3 ==="
#curl -sS -X POST -F "file=@${IMAGE_PATH}" "$OCR_V3_URL"
#echo
#echo

echo "=== v4 ==="
curl -sS -X POST -F "file=@${IMAGE_PATH}" "$OCR_V4_URL"
echo