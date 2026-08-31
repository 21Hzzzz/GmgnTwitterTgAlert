#!/usr/bin/env bash
# Update GmgnTwitterClaw and its runtime dependencies.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
DEFAULT_DIR="$HOME/GmgnTwitterTgAlert"
SERVICE_NAME="gmgn-twitter-monitor"

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
  echo "未找到项目：$PROJECT_DIR"
  echo "请先执行 install.sh 安装项目。"
  exit 1
fi

VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "未找到虚拟环境，请先在项目目录运行：bash install.sh"
  exit 1
fi

echo "正在更新项目：$PROJECT_DIR"
git -C "$PROJECT_DIR" pull --ff-only origin main
"$VENV_PYTHON" -m pip install -q -r "$PROJECT_DIR/requirements.txt"
"$PROJECT_DIR/.venv/bin/playwright" install chromium
bash "$PROJECT_DIR/service.sh"
echo "更新完成，后台服务已重启。"
