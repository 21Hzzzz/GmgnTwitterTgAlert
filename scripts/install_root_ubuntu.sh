#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/21Hzzzz/GmgnTwitterTgAlert"
PROJECT_DIR="/root/GmgnTwitterTgAlert"
SERVICE_NAME="gmgn-twitter-monitor.service"
GTA_BIN="/usr/local/bin/gta"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 用户运行此一键部署脚本。"
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "无法检测 Linux 发行版：缺少 /etc/os-release。"
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "此一键部署脚本仅支持 Ubuntu。"
  exit 1
fi

echo "正在安装基础系统依赖..."
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git xvfb

if [[ -d "$PROJECT_DIR" ]]; then
  if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    echo "$PROJECT_DIR 已存在，但它不是 git 仓库。已停止部署。"
    exit 1
  fi

  remote_url="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$remote_url" in
    "$REPO_URL"|"$REPO_URL.git"|"git@github.com:21Hzzzz/GmgnTwitterTgAlert.git")
      ;;
    *)
      echo "$PROJECT_DIR 已存在，但 origin 不是目标仓库 $REPO_URL。"
      echo "当前 origin：${remote_url:-<missing>}"
      exit 1
      ;;
  esac

  dirty="$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=no)"
  if [[ -n "$dirty" ]]; then
    echo "检测到 $PROJECT_DIR 中存在已跟踪文件的本地改动。已停止部署。"
    echo "$dirty"
    exit 1
  fi

  echo "正在更新项目代码..."
  git -C "$PROJECT_DIR" pull --ff-only
else
  echo "正在克隆项目代码..."
  git clone "$REPO_URL" "$PROJECT_DIR"
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
else
  echo "正在安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV_BIN="/root/.local/bin/uv"
fi

if [[ ! -x "$UV_BIN" ]]; then
  echo "未找到可执行的 uv：$UV_BIN"
  exit 1
fi

cd "$PROJECT_DIR"

echo "正在创建/更新 Python 虚拟环境..."
"$UV_BIN" venv
"$UV_BIN" pip install -r requirements.txt

echo "正在安装 Playwright Chromium 和 Linux 运行依赖..."
"$UV_BIN" run playwright install chromium
"$UV_BIN" run playwright install-deps chromium

echo "正在注册 systemd 服务..."
install -m 0644 "$PROJECT_DIR/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

echo "正在安装 gta 快捷命令..."
chmod 0755 "$PROJECT_DIR/ctl.py"
ln -sfn "$PROJECT_DIR/ctl.py" "$GTA_BIN"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "检测到 .env 不存在，已从 .env.example 创建模板。请手动编辑。"
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
else
  echo ".env 已存在，本次部署不会覆盖。"
fi

cat <<EOF

一键部署完成。

下一步：
  1. 手动编辑 /root/GmgnTwitterTgAlert/.env
  2. 启动服务：gta start
  3. 停止服务：gta stop
  4. 重启并重新读取 .env：gta restart
  5. 查看状态和实时日志：gta status
  6. 更新代码和依赖：gta update

可选 WARP 代理：
  gta warp
  如需启用代理，请随后在 .env 中设置：
  PROXY_SERVER=socks5://127.0.0.1:40000

服务不会在部署完成后自动启动，也不会自动设置开机自启。
EOF
