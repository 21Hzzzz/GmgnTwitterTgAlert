#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="gmgn-twitter-monitor"
SERVICE_NAME="${APP_NAME}.service"
SERVICE_USER="gmgn-monitor"
APP_DIR="/opt/${APP_NAME}"
RELEASES_DIR="${APP_DIR}/releases"
CURRENT_LINK="${APP_DIR}/current"
BROWSER_DIR="${APP_DIR}/browsers"
PYTHON_DIR="${APP_DIR}/python"
ENV_DIR="/etc/${APP_NAME}"
ENV_FILE="${ENV_DIR}/gmgn.env"
STATE_DIR="/var/lib/${APP_NAME}"
LOGIN_MARKER="${STATE_DIR}/.login-complete"
LOGIN_REQUIRED_MARKER="${STATE_DIR}/.login-required"
READY_SCREENSHOT="${STATE_DIR}/monitor_running.png"
BACKUP_ROOT="/root/${APP_NAME}-backups"
REPO_URL="${GMGN_REPO_URL:-https://github.com/21Hzzzz/GmgnTwitterTgAlert.git}"
REPO_REF="${GMGN_REF:-main}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
ACTION="${1:-install}"
PURGE=0
STAGING_DIR=""

if [[ $# -gt 0 ]]; then
  shift
fi
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;36m[GMGN]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[GMGN]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[GMGN]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[GMGN] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  rm -f -- "${ENV_DIR}/login.env" 2>/dev/null || true
}
trap cleanup EXIT

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "请使用 root 用户运行安装器。"
}

load_os() {
  [[ -r /etc/os-release ]] || die "无法识别操作系统。"
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    *) die "不支持 ${ID:-unknown} ${VERSION_ID:-unknown}；仅支持 Ubuntu 22.04/24.04/26.04、Debian 12/13。" ;;
  esac
  case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) die "不支持的 CPU 架构: $(uname -m)" ;;
  esac
  OS_ID="$ID"
  OS_CODENAME="${VERSION_CODENAME:-}"
  [[ -n "$OS_CODENAME" ]] || die "无法读取系统代号 VERSION_CODENAME。"
}

prompt() {
  local message="$1" default="${2:-}" value
  [[ "$NONINTERACTIVE" != "1" ]] || die "无交互模式缺少配置: $message"
  if [[ -n "$default" ]]; then
    read -r -p "$message [$default]: " value </dev/tty
    printf '%s' "${value:-$default}"
  else
    read -r -p "$message: " value </dev/tty
    printf '%s' "$value"
  fi
}

confirm() {
  local message="$1" default="${2:-N}" answer
  if [[ "$NONINTERACTIVE" == "1" ]]; then
    [[ "$default" == "Y" ]]
    return
  fi
  read -r -p "$message [y/N]: " answer </dev/tty
  [[ "${answer:-$default}" =~ ^[Yy]$ ]]
}

single_line() {
  printf '%s' "$1" | tr -d '\r\n'
}

read_env_value() {
  local key="$1" file="${2:-$ENV_FILE}" value
  [[ -r "$file" ]] || return 1
  value="$(sed -n "s/^${key}=//p" "$file" | tail -n 1)"
  value="${value#\"}"; value="${value%\"}"
  value="${value#\'}"; value="${value%\'}"
  [[ -n "$value" ]] || return 1
  printf '%s' "$value"
}

sanitize_existing_config() {
  [[ -s "$ENV_FILE" ]] || return 0
  local tmp
  tmp="$(mktemp "${ENV_DIR}/gmgn.env.clean.XXXXXX")"
  grep -Ev '^(FEISHU_|WEBHOOK_|WS_|INSTAGRAM_WEBHOOK_|SUMMARY_FEISHU_)' "$ENV_FILE" >"$tmp" || true
  grep -v '^PROXY_SERVER=' "$tmp" >"${tmp}.next" || true
  printf 'PROXY_SERVER=%s\n' "$PROXY_VALUE" >>"${tmp}.next"
  chown root:"$SERVICE_USER" "${tmp}.next"
  chmod 0640 "${tmp}.next"
  mv -f "${tmp}.next" "$ENV_FILE"
  rm -f "$tmp"
}

install_base_packages() {
  log "安装系统基础依赖..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl git gnupg jq lsb-release util-linux
}

ensure_user_and_dirs() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  install -d -o root -g root -m 0755 "$APP_DIR" "$RELEASES_DIR" "$BROWSER_DIR" "$PYTHON_DIR"
  install -d -o root -g "$SERVICE_USER" -m 0750 "$ENV_DIR"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR"
}

ensure_uv() {
  if [[ ! -x /usr/local/bin/uv ]]; then
    log "安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  fi
  /usr/local/bin/uv --version
}

proxy_works() {
  local proxy="$1"
  [[ "$proxy" =~ ^socks5h?://[^[:space:]]+:[0-9]+$ ]] || return 1
  curl -fsS --max-time 20 --proxy "$proxy" https://www.cloudflare.com/cdn-cgi/trace >/dev/null
}

install_warp() {
  log "从 Cloudflare 官方仓库安装 WARP..."
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
    | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  printf 'deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ %s main\n' "$OS_CODENAME" \
    > /etc/apt/sources.list.d/cloudflare-client.list
  apt-get update
  apt-get install -y cloudflare-warp
  systemctl enable --now warp-svc.service
  warp-cli --accept-tos registration show >/dev/null 2>&1 \
    || warp-cli --accept-tos registration new
  warp-cli --accept-tos mode proxy
  warp-cli --accept-tos proxy port 40000
  warp-cli --accept-tos connect
  sleep 4
  proxy_works "socks5://127.0.0.1:40000" \
    || die "WARP 已安装但 SOCKS5 代理验证失败。请运行 warp-cli status 和 warp-diag 排查。"
  PROXY_VALUE="socks5://127.0.0.1:40000"
}

configure_proxy() {
  local candidate="${PROXY_SERVER:-}"
  if [[ -z "$candidate" ]]; then
    candidate="$(read_env_value PROXY_SERVER 2>/dev/null || true)"
  fi
  if [[ -n "$candidate" ]] && proxy_works "$candidate"; then
    ok "代理可用: $candidate"
    PROXY_VALUE="$candidate"
    return
  fi

  if [[ "$NONINTERACTIVE" == "1" ]]; then
    [[ "${INSTALL_WARP:-0}" == "1" ]] \
      || die "无交互安装必须提供可用 PROXY_SERVER，或设置 INSTALL_WARP=1。"
    install_warp
    return
  fi

  warn "未检测到可用的 SOCKS5 代理。"
  printf '  1. 自动安装并配置 Cloudflare WARP\n  2. 使用已有 SOCKS5 代理\n  3. 退出\n' >/dev/tty
  local choice
  read -r -p "请选择 [1-3]: " choice </dev/tty
  case "$choice" in
    1) install_warp ;;
    2)
      candidate="$(prompt 'SOCKS5 地址（例如 socks5://127.0.0.1:1080）')"
      proxy_works "$candidate" || die "代理无法连通。"
      PROXY_VALUE="$candidate"
      ;;
    *) die "已取消安装。" ;;
  esac
}

telegram_api() {
  local token="$1" method="$2"
  shift 2
  curl -fsS --max-time 20 "https://api.telegram.org/bot${token}/${method}" "$@"
}

validate_telegram() {
  local token="$1" response chat_id
  response="$(telegram_api "$token" getMe)" || die "无法访问 Telegram Bot API。"
  [[ "$(jq -r '.ok // false' <<<"$response")" == "true" ]] \
    || die "Telegram Bot Token 无效。"
  for chat_id in "${CHAT_IDS[@]}"; do
    response="$(telegram_api "$token" getChat --data-urlencode "chat_id=${chat_id}")" \
      || die "Telegram 群组不可访问: $chat_id"
    [[ "$(jq -r '.ok // false' <<<"$response")" == "true" ]] \
      || die "Bot 无法访问群组 $chat_id；请先将 Bot 加入群组。"
  done
  ok "Telegram Bot 和 ${#CHAT_IDS[@]} 个群组验证通过。"
}

write_configuration() {
  local token deepseek summary_enable summary_times summary_tz
  local group chat_id handles track add_more route_lines="" summary_groups="" all_chat_id=""
  CHAT_IDS=()
  GROUP_NAMES=()

  if [[ "$NONINTERACTIVE" == "1" ]]; then
    token="${TG_BOT_TOKEN:-}"
    chat_id="${TG_CHANNEL_ID_ALL:-}"
    [[ -n "$token" && -n "$chat_id" ]] \
      || die "无交互安装需要 TG_BOT_TOKEN 和 TG_CHANNEL_ID_ALL。"
    group="ALL"
    all_chat_id="$chat_id"
    CHAT_IDS+=("$chat_id")
    GROUP_NAMES+=("$group")
    deepseek="${DEEPSEEK_API_KEY:-}"
    summary_enable="${SUMMARY_ENABLE:-False}"
    summary_times="${SUMMARY_TIMES:-07:30,20:00}"
    summary_tz="${SUMMARY_TIMEZONE:-Asia/Shanghai}"
  else
    token="$(prompt 'Telegram Bot Token')"
    while true; do
      local default_group="ALL"
      if [[ "${#GROUP_NAMES[@]}" -gt 0 ]]; then
        default_group="GROUP$(( ${#CHAT_IDS[@]} + 1 ))"
      fi
      group="$(prompt '路由组名称（字母/数字/下划线）' "$default_group")"
      group="$(single_line "${group^^}")"
      [[ "$group" =~ ^[A-Z0-9_]+$ ]] || die "路由组名称格式无效。"
      [[ " ${GROUP_NAMES[*]} " != *" $group "* ]] || die "路由组名称重复: $group"
      chat_id="$(single_line "$(prompt 'Telegram 群组 ID（通常以 -100 开头）')")"
      [[ -n "$chat_id" ]] || die "群组 ID 不能为空。"
      CHAT_IDS+=("$chat_id")
      GROUP_NAMES+=("$group")
      if [[ "$group" == "ALL" ]]; then
        all_chat_id="$chat_id"
      else
        handles="$(single_line "$(prompt '监控 handles，逗号分隔（不含 @）')")"
        track="$(single_line "$(prompt '赛道过滤关键词，逗号分隔，可留空')")"
        [[ -n "$handles" ]] || die "自定义路由组的 handles 不能为空。"
        printf -v route_lines '%sTG_ROUTING_%s=%s\nTG_ENABLE_%s=True\nTG_CHANNEL_ID_%s=%s\nTG_TRACK_FILTER_%s=%s\n' \
          "$route_lines" "$group" "$handles" "$group" "$group" "$chat_id" "$group" "$track"
      fi
      read -r -p "继续添加路由组？ [y/N]: " add_more </dev/tty
      [[ "$add_more" =~ ^[Yy]$ ]] || break
    done
    deepseek="$(single_line "$(prompt 'DeepSeek API Key，可留空')")"
    if confirm "启用 Telegram 定时摘要？"; then
      summary_enable="True"
      summary_times="$(single_line "$(prompt '每日摘要时间，逗号分隔' '07:30,20:00')")"
      summary_tz="$(single_line "$(prompt '摘要时区' 'Asia/Shanghai')")"
    else
      summary_enable="False"
      summary_times="07:30,20:00"
      summary_tz="Asia/Shanghai"
    fi
  fi

  token="$(single_line "$token")"
  [[ -n "$token" ]] || die "Telegram Bot Token 不能为空。"
  validate_telegram "$token"
  summary_groups="$(IFS=,; printf '%s' "${GROUP_NAMES[*]}")"

  local tmp
  tmp="$(mktemp "${ENV_DIR}/gmgn.env.XXXXXX")"
  {
    printf 'TG_BOT_TOKEN=%s\n' "$token"
    if [[ -n "$all_chat_id" ]]; then
      printf 'TG_ENABLE_DEFAULT=True\nTG_CHANNEL_ID=%s\nTG_CHANNEL_ID_ALL=%s\n' "$all_chat_id" "$all_chat_id"
    else
      printf 'TG_ENABLE_DEFAULT=False\nTG_CHANNEL_ID=\n'
    fi
    printf '%s' "$route_lines"
    printf 'TG_FILTER_HANDLES=\n'
    printf 'BINANCE_SQUARE_HANDLES=cz,heyi\n'
    printf 'INSTAGRAM_TRANSLATION_ENABLE=False\n'
    printf 'PROXY_SERVER=%s\n' "$PROXY_VALUE"
    printf 'MONITOR_URL=https://gmgn.ai/follow?target=xTracker&chain=bsc\n'
    printf 'WATCHDOG_TIMEOUT=120\nWATCHDOG_POLL_INTERVAL=5\n'
    printf 'GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS=chain_stat\n'
    printf 'GMGN_WS_FRAME_STATS_INTERVAL=600\nGMGN_HEARTBEAT_LOG_INTERVAL=600\nDIAG_HANDLES=\n'
    printf 'DEEPSEEK_API_KEY=%s\nAI_ANALYZE_HANDLES=\n' "$deepseek"
    printf 'SUMMARY_ENABLE=%s\nSUMMARY_TIMEZONE=%s\nSUMMARY_TIMES=%s\n' "$summary_enable" "$summary_tz" "$summary_times"
    printf 'SUMMARY_GROUPS=%s\n' "$summary_groups"
    for group in "${GROUP_NAMES[@]}"; do
      printf 'SUMMARY_LABEL_%s=%s\n' "$group" "$group"
    done
    printf 'SUMMARY_MAX_TWEETS=120\nSUMMARY_AI_TIMEOUT_SECONDS=180\nSUMMARY_TWEET_TEXT_LIMIT=500\n'
  } >"$tmp"
  chown root:"$SERVICE_USER" "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$ENV_FILE"
  ok "配置已写入 $ENV_FILE"
}

install_release() {
  STAGING_DIR="$(mktemp -d "${APP_DIR}/stage.XXXXXX")"
  log "下载 GitHub 源码 ($REPO_REF)..."
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "${STAGING_DIR}/source"
  local commit release_id release_path
  commit="$(git -C "${STAGING_DIR}/source" rev-parse --short=12 HEAD)"
  release_id="${commit}-$(date +%Y%m%d%H%M%S)"
  release_path="${RELEASES_DIR}/${release_id}"
  mv "${STAGING_DIR}/source" "$release_path"
  STAGING_DIR=""

  log "创建锁定的 Python 3.12 环境..."
  UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" UV_MANAGED_PYTHON=1 \
    /usr/local/bin/uv sync --project "$release_path" --python 3.12 --frozen --no-dev
  log "安装 Chromium 与 Linux 运行依赖..."
  PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
    "$release_path/.venv/bin/playwright" install --with-deps chromium
  PYTHONPATH="$release_path" GMGN_ENV_FILE="$ENV_FILE" GMGN_STATE_DIR="$STATE_DIR" \
    "$release_path/.venv/bin/python" -c \
    "import gmgn_twitter_monitor; import gmgn_twitter_monitor.app; import gmgn_twitter_monitor.distributor; print('imports ok')"
  chown -R root:root "$release_path" "$BROWSER_DIR" "$PYTHON_DIR"
  chmod -R a+rX "$release_path" "$BROWSER_DIR" "$PYTHON_DIR"
  NEW_RELEASE="$release_path"
}

install_service() {
  install -o root -g root -m 0644 "$NEW_RELEASE/gmgn-twitter-monitor.service" "/etc/systemd/system/$SERVICE_NAME"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
}

run_login() {
  local auth_url="${GMGN_AUTH_URL:-}"
  if [[ -z "$auth_url" ]]; then
    auth_url="$(prompt 'GMGN 授权链接（https://gmgn.ai/tglogin?...）')"
  fi
  auth_url="$(single_line "$auth_url")"
  if [[ "$auth_url" != https://gmgn.ai/tglogin* ]]; then
    warn "GMGN 授权链接格式不正确。"
    return 1
  fi

  local login_env="${ENV_DIR}/login.env"
  cp "$ENV_FILE" "$login_env"
  printf 'GMGN_AUTH_URL=%s\n' "$auth_url" >>"$login_env"
  chown root:"$SERVICE_USER" "$login_env"
  chmod 0640 "$login_env"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  local login_status=0
  runuser -u "$SERVICE_USER" -- env \
    HOME="$STATE_DIR" \
    PYTHONPATH="$CURRENT_LINK" \
    GMGN_ENV_FILE="$login_env" \
    GMGN_STATE_DIR="$STATE_DIR" \
    PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
    "$CURRENT_LINK/.venv/bin/python" -m gmgn_twitter_monitor --login \
    || login_status=$?
  rm -f "$login_env"
  [[ "$login_status" -eq 0 ]] || return "$login_status"
  if ! find "${STATE_DIR}/browser_data" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    warn "授权流程结束，但未检测到浏览器登录态。"
    return 1
  fi
  touch "$LOGIN_MARKER"
  chown "$SERVICE_USER:$SERVICE_USER" "$LOGIN_MARKER"
  chmod 0600 "$LOGIN_MARKER"
  ok "GMGN 登录态已保存。"
}

health_check() {
  local i stable=0
  for i in {1..45}; do
    sleep 2
    if [[ -f "$LOGIN_REQUIRED_MARKER" ]]; then
      warn "检测到 GMGN 登录态无效。"
      return 1
    fi
    if systemctl is-active --quiet "$SERVICE_NAME" && [[ -s "$READY_SCREENSHOT" ]]; then
      stable=$((stable + 1))
      if [[ "$stable" -ge 3 ]]; then
        ok "服务已进入监听页面并稳定运行。"
        return 0
      fi
    else
      stable=0
    fi
  done
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  return 1
}

restart_service() {
  rm -f -- "$READY_SCREENSHOT" "$LOGIN_REQUIRED_MARKER"
  systemctl restart "$SERVICE_NAME"
}

do_install() {
  load_os
  install_base_packages
  ensure_user_and_dirs
  ensure_uv
  configure_proxy
  if [[ ! -s "$ENV_FILE" ]]; then
    write_configuration
  else
    sanitize_existing_config
    ok "保留现有配置: $ENV_FILE"
  fi

  local previous=""
  if [[ -L "$CURRENT_LINK" ]]; then
    previous="$(readlink -f "$CURRENT_LINK")"
  fi
  install_release
  ln -sfn "$NEW_RELEASE" "$CURRENT_LINK"
  install_service

  if [[ ! -f "$LOGIN_MARKER" ]]; then
    run_login || die "首次 GMGN 授权失败。"
  fi

  restart_service
  if ! health_check; then
    if [[ -f "$LOGIN_REQUIRED_MARKER" ]]; then
      if [[ "$NONINTERACTIVE" == "1" ]]; then
        die "GMGN 登录态无效；新版本已保留，请提供 GMGN_AUTH_URL 后执行 relogin。"
      fi
      warn "现有 GMGN 登录态无效，需要使用新的单次授权链接。"
      run_login || die "GMGN 重新授权失败；新版本已保留，可再次执行 relogin。"
      restart_service
      health_check || die "GMGN 重新授权后服务仍未通过健康检查。"
      ok "安装/升级完成。查看日志: journalctl -u $SERVICE_NAME -f"
      return
    fi
    if [[ -n "$previous" && -d "$previous" ]]; then
      warn "新版本健康检查失败，正在恢复旧版本。"
      ln -sfn "$previous" "$CURRENT_LINK"
      restart_service || true
    fi
    die "安装后的服务健康检查失败。"
  fi
  ok "安装/升级完成。查看日志: journalctl -u $SERVICE_NAME -f"
}

do_reconfigure() {
  load_os
  install_base_packages
  ensure_user_and_dirs
  [[ -x "$CURRENT_LINK/.venv/bin/python" ]] || die "尚未安装，请先运行默认安装命令。"
  configure_proxy
  write_configuration
  restart_service
  health_check || die "重新配置后服务启动失败。"
}

do_relogin() {
  load_os
  ensure_user_and_dirs
  ensure_uv
  [[ -x "$CURRENT_LINK/.venv/bin/python" ]] || die "尚未安装。"
  local previous
  previous="$(readlink -f "$CURRENT_LINK")"
  install_release
  ln -sfn "$NEW_RELEASE" "$CURRENT_LINK"
  install_service
  local backup="${STATE_DIR}/browser_data.before-relogin"
  local marker_backup="${LOGIN_MARKER}.before-relogin"
  rm -rf -- "$backup"
  rm -f -- "$marker_backup"
  if [[ -d "${STATE_DIR}/browser_data" ]]; then
    mv "${STATE_DIR}/browser_data" "$backup"
  fi
  if [[ -f "$LOGIN_MARKER" ]]; then
    mv "$LOGIN_MARKER" "$marker_backup"
  fi
  if run_login; then
    rm -rf -- "$backup"
    rm -f -- "$marker_backup"
    restart_service
    health_check || die "重新授权成功，但服务启动失败。"
  else
    rm -rf -- "${STATE_DIR}/browser_data"
    [[ ! -d "$backup" ]] || mv "$backup" "${STATE_DIR}/browser_data"
    [[ ! -f "$marker_backup" ]] || mv "$marker_backup" "$LOGIN_MARKER"
    ln -sfn "$previous" "$CURRENT_LINK"
    install_service
    restart_service || true
    die "重新授权失败，已恢复原版本和登录态。"
  fi
}

do_uninstall() {
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f -- "/etc/systemd/system/$SERVICE_NAME"
  systemctl daemon-reload
  if [[ "$PURGE" -eq 0 ]]; then
    local backup_dir="${BACKUP_ROOT}/$(date +%Y%m%d-%H%M%S)"
    install -d -o root -g root -m 0700 "$backup_dir"
    [[ ! -d "$ENV_DIR" ]] || cp -a "$ENV_DIR" "$backup_dir/config"
    [[ ! -d "$STATE_DIR" ]] || cp -a "$STATE_DIR" "$backup_dir/state"
    ok "配置、登录态和数据库已备份到 $backup_dir"
  fi
  rm -rf -- "$APP_DIR" "$ENV_DIR" "$STATE_DIR"
  id "$SERVICE_USER" >/dev/null 2>&1 && userdel "$SERVICE_USER" || true
  ok "卸载完成。WARP、uv 和 apt 系统包未删除。"
}

require_root
case "$ACTION" in
  install|update) do_install ;;
  reconfigure) do_reconfigure ;;
  relogin) do_relogin ;;
  uninstall) do_uninstall ;;
  *) die "用法: install.sh [install|update|reconfigure|relogin|uninstall [--purge]]" ;;
esac
