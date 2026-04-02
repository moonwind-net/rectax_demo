#!/usr/bin/env bash

set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-rectax-db}"
DB_NAME="${DB_NAME:-receipt_tax_system}"
DB_USER="${DB_USER:-receipt_user}"
TARGET_MODE=""
TARGET_VALUE=""
FORCE_DELETE="false"

usage() {
  echo "Usage:" >&2
  echo "  $0 [--force] --id <document_id>" >&2
  echo "  $0 [--force] --name <original_filename>" >&2
  echo "  $0 [--force] <original_filename>" >&2
}

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

build_where_clause() {
  if [ "$TARGET_MODE" = "id" ]; then
    printf 'id = %s' "$TARGET_VALUE"
  else
    printf "original_filename = '%s'" "$(sql_escape "$TARGET_VALUE")"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force)
      FORCE_DELETE="true"
      shift
      ;;
    --id)
      TARGET_MODE="id"
      TARGET_VALUE="${2:-}"
      shift 2
      ;;
    --name)
      TARGET_MODE="name"
      TARGET_VALUE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [ -z "$TARGET_MODE" ]; then
        TARGET_MODE="name"
        TARGET_VALUE="$1"
        shift
      else
        usage
        exit 1
      fi
      ;;
  esac
done

if [ -z "$TARGET_MODE" ] || [ -z "$TARGET_VALUE" ]; then
  usage
  exit 1
fi

if [ "$TARGET_MODE" = "id" ] && ! printf '%s' "$TARGET_VALUE" | grep -Eq '^[0-9]+$'; then
  echo "Document ID must be numeric." >&2
  exit 1
fi

WHERE_CLAUSE="$(build_where_clause)"

echo "=== target ==="
echo "$TARGET_MODE: $TARGET_VALUE"
echo

echo "=== matching documents ==="
docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c \
  "SELECT id, original_filename, storage_path, document_status, created_at FROM documents WHERE $WHERE_CLAUSE ORDER BY id;"
echo

if [ "$FORCE_DELETE" != "true" ]; then
  read -r -p "Delete these document rows and all dependent records? [y/N] " CONFIRM
  case "$CONFIRM" in
    y|Y|yes|YES) ;;
    *)
      echo "Cancelled."
      exit 0
      ;;
  esac
fi

docker exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" <<SQL
BEGIN;

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
), target_extractions AS (
  SELECT id FROM receipt_extractions WHERE document_id IN (SELECT id FROM target_documents)
)
DELETE FROM receipt_tax_lines
WHERE receipt_extraction_id IN (SELECT id FROM target_extractions);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM document_flags
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM review_tasks
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM classification_results
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM processing_tasks
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM receipt_extractions
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM ocr_runs
WHERE document_id IN (SELECT id FROM target_documents);

WITH target_documents AS (
  SELECT id FROM documents WHERE $WHERE_CLAUSE
)
DELETE FROM review_audit_logs
WHERE document_id IN (SELECT id FROM target_documents);

DELETE FROM documents
WHERE $WHERE_CLAUSE;

COMMIT;
SQL

echo
echo "=== remaining documents ==="
docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c \
  "SELECT id, original_filename, storage_path, document_status, created_at FROM documents WHERE $WHERE_CLAUSE ORDER BY id;"