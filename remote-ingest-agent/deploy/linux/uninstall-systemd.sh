#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="rectax-ingest-agent"

if [[ $# -ge 2 && "$1" == "--service-name" ]]; then
  SERVICE_NAME="$2"
fi

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

systemctl stop "${SERVICE_NAME}.service" || true
systemctl disable "${SERVICE_NAME}.service" || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

echo "Uninstalled: ${SERVICE_NAME}.service"
