#!/usr/bin/env bash
# Interactive installer for GmgnTwitterClaw.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/21Hzzzz/GmgnTwitterTgAlert.git}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_privileged() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_prerequisites() {
  command -v apt-get >/dev/null || {
    echo "请先安装 Python 3.10+、python3-venv 和 Git。"
    exit 1
  }
  echo "正在安装 Git 和 Python 运行环境..."
  run_privileged apt-get update
  run_privileged apt-get install -y git python3 python3-venv python3-pip
}

# When invoked through curl on a fresh VPS, bootstrap the repository first.
if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
  command -v git >/dev/null && command -v python3 >/dev/null || install_prerequisites
  DEFAULT_DIR="$HOME/GmgnTwitterTgAlert"
  read -r -p "安装目录 [$DEFAULT_DIR]: " install_dir
  INSTALL_DIR="${install_dir:-$DEFAULT_DIR}"
  if [[ -e "$INSTALL_DIR" ]]; then
    echo "安装目录已存在：$INSTALL_DIR"
    echo "请更换目录，或在该目录中运行 install.sh。"
    exit 1
  fi
  git clone "$REPO_URL" "$INSTALL_DIR"
  exec bash "$INSTALL_DIR/install.sh"
fi

PROJECT_DIR="$SCRIPT_DIR"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[\\&|]/\\&/g')"

  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

prompt_default() {
  local label="$1"
  local default="$2"
  local answer
  read -r -p "$label [$default]: " answer
  printf '%s' "${answer:-$default}"
}

command -v python3 >/dev/null || install_prerequisites

echo "== GmgnTwitterClaw 交互式安装 =="
echo "项目目录：$PROJECT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

read -r -p "安装 Chromium 所需的系统依赖？需要 sudo 权限 [Y/n]: " install_system_deps
if [[ "${install_system_deps:-Y}" =~ ^[Yy]$ ]]; then
  run_privileged "$VENV_DIR/bin/playwright" install-deps chromium
fi
"$VENV_DIR/bin/playwright" install chromium

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  echo "已创建 .env。"
else
  echo "检测到已有 .env，将仅更新本次填写的 Telegram/登录配置。"
fi

echo
echo "== Telegram 配置 =="
read -r -p "Telegram Bot Token（必填）: " tg_bot_token
while [[ -z "$tg_bot_token" ]]; do
  read -r -p "Token 不能为空，请重新输入: " tg_bot_token
done
tg_channel_id="$(prompt_default "目标频道 ID" "-1001234567890")"
tg_handles="$(prompt_default "监控 Handle（逗号分隔，不含 @）" "elonmusk")"
tg_group="$(prompt_default "路由组名称" "ALERTS")"
tg_group="$(printf '%s' "$tg_group" | tr '[:lower:]' '[:upper:]')"
if [[ ! "$tg_group" =~ ^[A-Z0-9_]+$ ]]; then
  echo "路由组名称只能包含字母、数字和下划线。"
  exit 1
fi

set_env_value "TG_BOT_TOKEN" "$tg_bot_token"
set_env_value "TG_ENABLE_${tg_group}" "True"
set_env_value "TG_CHANNEL_ID_${tg_group}" "$tg_channel_id"
set_env_value "TG_ROUTING_${tg_group}" "$tg_handles"

echo
read -r -p "SOCKS5/HTTP 代理地址（留空直连）: " proxy_server
set_env_value "PROXY_SERVER" "$proxy_server"

read -r -p "现在进行 GMGN 首次授权？[y/N]: " configure_auth
if [[ "${configure_auth:-N}" =~ ^[Yy]$ ]]; then
  read -r -p "粘贴 GMGN 授权链接: " auth_url
  while [[ -z "$auth_url" ]]; do
    read -r -p "授权链接不能为空，请重新输入: " auth_url
  done
  set_env_value "FIRST_RUN_LOGIN" "True"
  set_env_value "AUTH_URL" "$auth_url"
fi

echo
echo "安装完成。配置文件：$ENV_FILE"
echo "首次授权成功后，请将 FIRST_RUN_LOGIN 改回 False。"
read -r -p "立即启动监控？[y/N]: " start_now
if [[ "${start_now:-N}" =~ ^[Yy]$ ]]; then
  cd "$PROJECT_DIR"
  exec "$VENV_DIR/bin/python" -m gmgn_twitter_monitor
fi

echo "稍后可运行：$VENV_DIR/bin/python -m gmgn_twitter_monitor"
