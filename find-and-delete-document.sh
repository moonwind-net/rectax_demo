#!/usr/bin/env bash

set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-rectax-db}"
DB_NAME="${DB_NAME:-receipt_tax_system}"
DB_USER="${DB_USER:-receipt_user}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DELETE_SCRIPT="$SCRIPT_DIR/delete-document-by-filename.sh"
SEARCH_TERM=""
FORCE_DELETE="false"
SEARCH_MODE="name"

if [ ! -x "$DELETE_SCRIPT" ]; then
  echo "Delete script not executable: $DELETE_SCRIPT" >&2
  echo "Run: chmod +x delete-document-by-filename.sh" >&2
  exit 1
fi

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force)
      FORCE_DELETE="true"
      shift
      ;;
    --id)
      SEARCH_MODE="id"
      SEARCH_TERM="${2:-}"
      shift 2
      ;;
    --name)
      SEARCH_MODE="name"
      SEARCH_TERM="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [-f|--force] [--id <document_id> | --name <filename_keyword> | <filename_keyword>]" >&2
      exit 0
      ;;
    *)
      if [ -z "$SEARCH_TERM" ]; then
        SEARCH_MODE="name"
        SEARCH_TERM="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

if [ "$SEARCH_MODE" = "id" ] && [ -n "$SEARCH_TERM" ] && ! printf '%s' "$SEARCH_TERM" | grep -Eq '^[0-9]+$'; then
  echo "Document ID must be numeric." >&2
  exit 1
fi

if [ "$SEARCH_MODE" = "id" ] && [ -n "$SEARCH_TERM" ]; then
  SEARCH_SQL="WHERE id = $SEARCH_TERM"
  TITLE="=== matching documents by id ==="
elif [ -n "$SEARCH_TERM" ]; then
  SEARCH_SQL="WHERE original_filename ILIKE '%$(sql_escape "$SEARCH_TERM")%'"
  TITLE="=== matching documents by name ==="
else
  SEARCH_SQL="ORDER BY id DESC LIMIT 20"
  TITLE="=== latest 20 documents ==="
fi

echo "$TITLE"
if [ "$SEARCH_MODE" = "id" ] && [ -n "$SEARCH_TERM" ]; then
  docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c \
    "SELECT id, original_filename, storage_path, document_status, created_at FROM documents $SEARCH_SQL;"
elif [ -n "$SEARCH_TERM" ]; then
  docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c \
    "SELECT id, original_filename, storage_path, document_status, created_at FROM documents $SEARCH_SQL ORDER BY id DESC;"
else
  docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c \
    "SELECT id, original_filename, storage_path, document_status, created_at FROM documents $SEARCH_SQL;"
fi
echo

# If caller provided --id together with --force, run delete immediately (no interactive prompt)
if [ "$FORCE_DELETE" = "true" ] && [ "$SEARCH_MODE" = "id" ] && [ -n "$SEARCH_TERM" ]; then
  echo "Auto-deleting id $SEARCH_TERM (force mode)"
  "$DELETE_SCRIPT" --force --id "$SEARCH_TERM"
  exit 0
fi

read -r -p "Enter document ID to delete (blank to cancel): " DOCUMENT_ID

if [ -z "$DOCUMENT_ID" ]; then
  echo "Cancelled."
  exit 0
fi

if ! printf '%s' "$DOCUMENT_ID" | grep -Eq '^[0-9]+$'; then
  echo "Document ID must be numeric." >&2
  exit 1
fi

if [ "$FORCE_DELETE" = "true" ]; then
  "$DELETE_SCRIPT" --force --id "$DOCUMENT_ID"
else
  "$DELETE_SCRIPT" --id "$DOCUMENT_ID"
fi