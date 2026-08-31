#!/usr/bin/env bash
# Interactive installer for GmgnTwitterClaw.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/21Hzzzz/GmgnTwitterTgAlert.git}"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

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
  read -r -p "安装目录 [$DEFAULT_DIR]: " install_dir </dev/tty
  INSTALL_DIR="${install_dir:-$DEFAULT_DIR}"
  if [[ -e "$INSTALL_DIR" ]]; then
    if [[ -d "$INSTALL_DIR/.git" && -f "$INSTALL_DIR/requirements.txt" ]]; then
      echo "检测到已有项目，正在更新安装器..."
      git -C "$INSTALL_DIR" pull --ff-only origin main
      exec bash "$INSTALL_DIR/install.sh"
    fi
    echo "安装目录已存在且不是有效项目：$INSTALL_DIR"
    echo "请更换安装目录后重试。"
    exit 1
  fi
  git clone "$REPO_URL" "$INSTALL_DIR"
  exec bash "$INSTALL_DIR/install.sh"
fi

PROJECT_DIR="$SCRIPT_DIR"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="gmgn-twitter-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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
  read -r -p "$label [$default]: " answer </dev/tty
  printf '%s' "${answer:-$default}"
}

install_service() {
  command -v systemctl >/dev/null || {
    echo "未检测到 systemd，无法创建后台服务。"
    return 1
  }

  local run_user
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
ExecStart=${VENV_DIR}/bin/python -m gmgn_twitter_monitor
Restart=always
RestartSec=10
RuntimeMaxSec=43200
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
  run_privileged systemctl daemon-reload
  run_privileged systemctl enable --now "$SERVICE_NAME"
}

command -v python3 >/dev/null || install_prerequisites
python3 -c 'import ensurepip, venv' >/dev/null 2>&1 || install_prerequisites

echo "== GmgnTwitterClaw 交互式安装 =="
echo "项目目录：$PROJECT_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

read -r -p "安装 Chromium 所需的系统依赖？需要 sudo 权限 [Y/n]: " install_system_deps </dev/tty
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
read -r -p "Telegram Bot Token（必填）: " tg_bot_token </dev/tty
while [[ -z "$tg_bot_token" ]]; do
  read -r -p "Token 不能为空，请重新输入: " tg_bot_token </dev/tty
done
tg_channel_id="$(prompt_default "Telegram 频道/群组 ID" "-1001234567890")"

set_env_value "TG_BOT_TOKEN" "$tg_bot_token"
set_env_value "TG_ENABLE_DEFAULT" "True"
set_env_value "TG_CHANNEL_ID" "$tg_channel_id"

echo
read -r -p "SOCKS5/HTTP 代理地址（留空直连）: " proxy_server </dev/tty
set_env_value "PROXY_SERVER" "$proxy_server"

read -r -p "现在进行 GMGN 首次授权？[y/N]: " configure_auth </dev/tty
if [[ "${configure_auth:-N}" =~ ^[Yy]$ ]]; then
  read -r -p "粘贴 GMGN 授权链接: " auth_url </dev/tty
  while [[ -z "$auth_url" ]]; do
    read -r -p "授权链接不能为空，请重新输入: " auth_url </dev/tty
  done
  set_env_value "FIRST_RUN_LOGIN" "True"
  set_env_value "AUTH_URL" "$auth_url"
fi

echo
echo "安装完成。配置文件：$ENV_FILE"
install_service
echo "后台服务已启动，并已设置为开机自启。"
echo "查看日志：journalctl -u $SERVICE_NAME -f"
