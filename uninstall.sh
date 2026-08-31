#!/usr/bin/env bash
# Remove this project's files after an explicit terminal confirmation.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
DEFAULT_DIR="$HOME/GmgnTwitterTgAlert"
SERVICE_NAME="gmgn-twitter-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

run_privileged() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
  PROJECT_DIR="$SCRIPT_DIR"
else
  PROJECT_DIR="$DEFAULT_DIR"
fi

if [[ ! -d "$PROJECT_DIR/.git" || ! -f "$PROJECT_DIR/requirements.txt" ]]; then
  echo "未找到有效项目：$PROJECT_DIR"
  exit 1
fi

if [[ "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "$HOME" ]]; then
  echo "拒绝删除不安全的目录：$PROJECT_DIR"
  exit 1
fi

echo "将删除：$PROJECT_DIR"
echo "其中包括 .env、浏览器登录态、SQLite 数据库和虚拟环境。"
read -r -p "确认卸载？输入 DELETE 继续: " confirmation </dev/tty
if [[ "$confirmation" != "DELETE" ]]; then
  echo "已取消。"
  exit 0
fi

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
  run_privileged systemctl disable --now "$SERVICE_NAME"
fi
if [[ -f "$SERVICE_FILE" ]]; then
  run_privileged rm -f "$SERVICE_FILE"
  run_privileged systemctl daemon-reload
fi
# Only terminate processes launched from this exact project directory.
pkill -f "$PROJECT_DIR/.venv/bin/python -m gmgn_twitter_monitor" 2>/dev/null || true
rm -rf "$PROJECT_DIR"
echo "卸载完成。"
