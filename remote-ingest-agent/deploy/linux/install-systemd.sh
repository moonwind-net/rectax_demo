#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="rectax-ingest-agent"
AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="python3"
CONFIG_FILE="config.yaml"
ENV_FILE=".env"
RUN_USER="${SUDO_USER:-$(whoami)}"

usage() {
  cat <<'USAGE'
Usage:
  ./install-systemd.sh [options]

Options:
  --service-name <name>   systemd service name (default: rectax-ingest-agent)
  --agent-dir <path>      path to agent dir (default: auto-detect)
  --python <path>         python binary (default: python3)
  --config <file>         config file under agent dir (default: config.yaml)
  --env-file <file>       env file under agent dir (default: .env)
  --run-user <user>       service user (default: current sudo user)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --agent-dir) AGENT_DIR="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --config) CONFIG_FILE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --run-user) RUN_USER="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

if [[ ! -d "$AGENT_DIR" ]]; then
  echo "Agent directory not found: $AGENT_DIR" >&2
  exit 1
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "Run user does not exist: $RUN_USER" >&2
  exit 1
fi

cd "$AGENT_DIR"

$PYTHON_BIN -m venv .venv
"$AGENT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$AGENT_DIR/.venv/bin/python" -m pip install -r requirements.txt

mkdir -p "$AGENT_DIR/state"
chown -R "$RUN_USER":"$RUN_USER" "$AGENT_DIR"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=RecTax Remote Ingest Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${AGENT_DIR}
ExecStart=${AGENT_DIR}/.venv/bin/python ${AGENT_DIR}/agent.py --config ${AGENT_DIR}/${CONFIG_FILE} --env-file ${AGENT_DIR}/${ENV_FILE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
systemctl status "${SERVICE_NAME}.service" --no-pager

echo "Installed and started: ${SERVICE_NAME}.service"
