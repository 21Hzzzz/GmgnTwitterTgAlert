#!/usr/bin/env bash
# Create or refresh the systemd service for this installation.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
DEFAULT_DIR="$HOME/GmgnTwitterTgAlert"
PROJECT_DIR="$DEFAULT_DIR"
[[ -f "$SCRIPT_DIR/requirements.txt" ]] && PROJECT_DIR="$SCRIPT_DIR"

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "未找到可用项目或虚拟环境：$PROJECT_DIR"
  exit 1
fi

run_privileged() {
  if [[ "$EUID" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

SERVICE_NAME="gmgn-twitter-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
run_user="$(id -un)"

run_privileged tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=GMGN Telegram Follow Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${run_user}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PROJECT_DIR}/.venv/bin/python -m gmgn_twitter_monitor
Restart=always
RestartSec=10
RuntimeMaxSec=43200
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

run_privileged systemctl daemon-reload
run_privileged systemctl enable "$SERVICE_NAME"
if systemctl is-active --quiet "$SERVICE_NAME"; then
  run_privileged systemctl restart "$SERVICE_NAME"
else
  run_privileged systemctl start "$SERVICE_NAME"
fi
echo "后台服务已启动：$SERVICE_NAME"
