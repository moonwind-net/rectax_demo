#!/usr/bin/env bash
set -euo pipefail

# Throttled folder uploader for RecTax ingestion endpoint.
# Adapts to server-side rate limiting by honoring Retry-After and pausing when
# /health is unhealthy.

usage() {
  cat <<'USAGE'
Usage:
  ./throttled-folder-upload.sh --dir <folder> [options]

Required:
  --dir <folder>                 Source folder containing files.

Options:
  --base-url <url>               Backend base URL. Default: http://127.0.0.1:8000
  --api-prefix <prefix>          API prefix. Default: /api/v1
  --email <email>                Login email. Default: admin@example.com
  --password <password>          Login password. Default: admin123456
  --batch-size <n>               Max files per request. Default: 3
  --delay-seconds <n>            Sleep time between successful requests. Default: 2
  --max-files <n>                Upload at most N files (0 means all). Default: 0
  --max-batch-mb <n>             Max total MB per request. Default: 100
  --max-retries <n>              Retries per batch on transient failure. Default: 3
  --retry-base-seconds <n>       Initial retry backoff in seconds. Default: 3
  --health-poll-seconds <n>      Poll interval when /health is not OK. Default: 5
  --non-recursive                Only scan top-level folder.
  --dry-run                      Print plan without uploading.
  --help                         Show this help.

Environment variable alternatives:
  BASE_URL, API_PREFIX, LOGIN_EMAIL, LOGIN_PASSWORD, INPUT_DIR,
  BATCH_SIZE, DELAY_SECONDS, MAX_FILES, MAX_BATCH_MB,
  MAX_RETRIES, RETRY_BASE_SECONDS, HEALTH_POLL_SECONDS,
  RECURSIVE_SCAN, DRY_RUN
USAGE
}

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"
LOGIN_EMAIL="${LOGIN_EMAIL:-admin@example.com}"
LOGIN_PASSWORD="${LOGIN_PASSWORD:-admin123456}"
INPUT_DIR="${INPUT_DIR:-}"
BATCH_SIZE="${BATCH_SIZE:-3}"
DELAY_SECONDS="${DELAY_SECONDS:-2}"
MAX_FILES="${MAX_FILES:-0}"
MAX_BATCH_MB="${MAX_BATCH_MB:-100}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_BASE_SECONDS="${RETRY_BASE_SECONDS:-3}"
HEALTH_POLL_SECONDS="${HEALTH_POLL_SECONDS:-5}"
RECURSIVE_SCAN="${RECURSIVE_SCAN:-1}"
DRY_RUN="${DRY_RUN:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      INPUT_DIR="$2"
      shift 2
      ;;
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --api-prefix)
      API_PREFIX="$2"
      shift 2
      ;;
    --email)
      LOGIN_EMAIL="$2"
      shift 2
      ;;
    --password)
      LOGIN_PASSWORD="$2"
      shift 2
      ;;
    --batch-size)
      BATCH_SIZE="$2"
      shift 2
      ;;
    --delay-seconds)
      DELAY_SECONDS="$2"
      shift 2
      ;;
    --max-files)
      MAX_FILES="$2"
      shift 2
      ;;
    --max-batch-mb)
      MAX_BATCH_MB="$2"
      shift 2
      ;;
    --max-retries)
      MAX_RETRIES="$2"
      shift 2
      ;;
    --retry-base-seconds)
      RETRY_BASE_SECONDS="$2"
      shift 2
      ;;
    --health-poll-seconds)
      HEALTH_POLL_SECONDS="$2"
      shift 2
      ;;
    --non-recursive)
      RECURSIVE_SCAN=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

for pair in \
  "BATCH_SIZE:$BATCH_SIZE:positive" \
  "DELAY_SECONDS:$DELAY_SECONDS:nonnegative" \
  "MAX_FILES:$MAX_FILES:nonnegative" \
  "MAX_BATCH_MB:$MAX_BATCH_MB:positive" \
  "MAX_RETRIES:$MAX_RETRIES:nonnegative" \
  "RETRY_BASE_SECONDS:$RETRY_BASE_SECONDS:positive" \
  "HEALTH_POLL_SECONDS:$HEALTH_POLL_SECONDS:positive"; do
  IFS=: read -r name value kind <<< "$pair"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "$name must be an integer" >&2
    exit 1
  fi
  if [[ "$kind" == "positive" && "$value" -le 0 ]]; then
    echo "$name must be > 0" >&2
    exit 1
  fi
  if [[ "$kind" == "nonnegative" && "$value" -lt 0 ]]; then
    echo "$name must be >= 0" >&2
    exit 1
  fi
done

if [[ -z "$INPUT_DIR" ]]; then
  echo "Missing required --dir" >&2
  usage
  exit 1
fi

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Input folder not found: $INPUT_DIR" >&2
  exit 1
fi

if (( BATCH_SIZE > 20 )); then
  echo "--batch-size exceeds current server limit (20). Please lower it." >&2
  exit 1
fi

MAX_BATCH_BYTES=$(( MAX_BATCH_MB * 1024 * 1024 ))
LOGIN_URL="${BASE_URL}${API_PREFIX}/auth/login"
UPLOAD_URL="${BASE_URL}${API_PREFIX}/ingestion/upload"
HEALTH_URL="${BASE_URL}/health"

sleep_seconds() {
  local seconds="$1"
  if (( seconds > 0 )); then
    sleep "$seconds"
  fi
}

wait_for_healthy() {
  while true; do
    local body
    body=$(curl -sS "$HEALTH_URL" || true)
    if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      return 0
    fi
    echo "Backend health is not OK. Rechecking in ${HEALTH_POLL_SECONDS}s..."
    sleep_seconds "$HEALTH_POLL_SECONDS"
  done
}

find_cmd=(find "$INPUT_DIR")
if [[ "$RECURSIVE_SCAN" == "1" ]]; then
  find_cmd+=( -type f )
else
  find_cmd+=( -maxdepth 1 -type f )
fi

find_cmd+=( \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.pdf' -o -iname '*.tif' -o -iname '*.tiff' -o -iname '*.webp' \) -print0 )

mapfile -d '' FILES < <("${find_cmd[@]}")

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No supported files found in $INPUT_DIR"
  exit 0
fi

if [[ "$MAX_FILES" -gt 0 && ${#FILES[@]} -gt "$MAX_FILES" ]]; then
  FILES=("${FILES[@]:0:$MAX_FILES}")
fi

declare -a BATCH_STARTS=()
declare -a BATCH_COUNTS=()

current_start=0
current_count=0
current_bytes=0
for ((idx=0; idx<${#FILES[@]}; idx++)); do
  file_path="${FILES[idx]}"
  file_size=$(wc -c < "$file_path")

  if (( file_size > MAX_BATCH_BYTES )); then
    echo "File exceeds max batch size limit (${MAX_BATCH_MB}MB): $file_path" >&2
    exit 1
  fi

  if (( current_count > 0 )) && (( current_count >= BATCH_SIZE || current_bytes + file_size > MAX_BATCH_BYTES )); then
    BATCH_STARTS+=("$current_start")
    BATCH_COUNTS+=("$current_count")
    current_start=$idx
    current_count=0
    current_bytes=0
  fi

  if (( current_count == 0 )); then
    current_start=$idx
  fi

  current_count=$((current_count + 1))
  current_bytes=$((current_bytes + file_size))
done

if (( current_count > 0 )); then
  BATCH_STARTS+=("$current_start")
  BATCH_COUNTS+=("$current_count")
fi

TOTAL_FILES=${#FILES[@]}
TOTAL_BATCHES=${#BATCH_STARTS[@]}

echo "Plan: $TOTAL_FILES file(s), $TOTAL_BATCHES batch(es), batch_size<=${BATCH_SIZE}, batch_mb<=${MAX_BATCH_MB}, delay=${DELAY_SECONDS}s"
echo "Examples (first up to 5 files):"
for f in "${FILES[@]:0:5}"; do
  echo "  - $f"
done

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry-run enabled. No upload performed."
  exit 0
fi

LOGIN_PAYLOAD=$(printf '{"email":"%s","password":"%s"}' "$LOGIN_EMAIL" "$LOGIN_PASSWORD")
LOGIN_RESPONSE=$(curl -sS --fail-with-body -X POST "$LOGIN_URL" -H 'Content-Type: application/json' -d "$LOGIN_PAYLOAD")
ACCESS_TOKEN=$(printf '%s' "$LOGIN_RESPONSE" | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

if [[ -z "$ACCESS_TOKEN" ]]; then
  echo "Failed to parse access token from login response" >&2
  echo "Response: $LOGIN_RESPONSE" >&2
  exit 1
fi

uploaded=0
for ((batch_idx=0; batch_idx<TOTAL_BATCHES; batch_idx++)); do
  wait_for_healthy

  start="${BATCH_STARTS[batch_idx]}"
  count="${BATCH_COUNTS[batch_idx]}"
  echo "[Batch $((batch_idx + 1))/$TOTAL_BATCHES] uploading $count file(s)..."

  attempt=0
  while true; do
    header_file=$(mktemp)
    body_file=$(mktemp)

    curl_args=(
      -sS
      -D "$header_file"
      -o "$body_file"
      -X POST
      "$UPLOAD_URL"
      -H "Authorization: Bearer $ACCESS_TOKEN"
    )

    for ((i=0; i<count; i++)); do
      file_path="${FILES[start + i]}"
      curl_args+=( -F "files=@${file_path}" )
    done

    http_code=$(curl "${curl_args[@]}" -w '%{http_code}')
    response_body=$(cat "$body_file")
    retry_after=$( (grep -i '^Retry-After:' "$header_file" || true) | tail -n 1 | awk '{print $2}' | tr -d '\r')
    rm -f "$header_file" "$body_file"

    if [[ "$http_code" =~ ^2 ]]; then
      echo "$response_body"
      uploaded=$((uploaded + count))
      break
    fi

    if [[ "$http_code" == "413" ]]; then
      echo "Server rejected batch as too large: $response_body" >&2
      exit 1
    fi

    if [[ "$http_code" == "400" ]]; then
      echo "Server rejected batch: $response_body" >&2
      exit 1
    fi

    if (( attempt >= MAX_RETRIES )); then
      echo "Batch failed after ${MAX_RETRIES} retries: HTTP $http_code $response_body" >&2
      exit 1
    fi

    backoff=$(( RETRY_BASE_SECONDS * (2 ** attempt) ))
    if [[ -n "$retry_after" && "$retry_after" =~ ^[0-9]+$ && "$retry_after" -gt "$backoff" ]]; then
      wait_time="$retry_after"
    else
      wait_time="$backoff"
    fi

    echo "Transient failure (HTTP $http_code). Retrying in ${wait_time}s..."
    sleep_seconds "$wait_time"
    attempt=$((attempt + 1))
    wait_for_healthy
  done

  if (( batch_idx + 1 < TOTAL_BATCHES )) && (( DELAY_SECONDS > 0 )); then
    echo "Sleeping ${DELAY_SECONDS}s before next batch..."
    sleep_seconds "$DELAY_SECONDS"
  fi
done

echo "Done. Uploaded requests for $uploaded file(s)."
