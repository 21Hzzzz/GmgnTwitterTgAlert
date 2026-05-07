#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/21Hzzzz/GmgnTwitterTgAlert"
PROJECT_DIR="/root/GmgnTwitterTgAlert"
SERVICE_NAME="gmgn-twitter-monitor.service"
GTA_BIN="/usr/local/bin/gta"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer must be run as root."
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot detect Linux distribution: /etc/os-release is missing."
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This one-line installer is intended for Ubuntu."
  exit 1
fi

echo "Installing base packages..."
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git xvfb

if [[ -d "$PROJECT_DIR" ]]; then
  if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    echo "$PROJECT_DIR exists but is not a git repository. Aborting."
    exit 1
  fi

  remote_url="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$remote_url" in
    "$REPO_URL"|"$REPO_URL.git"|"git@github.com:21Hzzzz/GmgnTwitterTgAlert.git")
      ;;
    *)
      echo "$PROJECT_DIR exists but origin is not $REPO_URL."
      echo "Current origin: ${remote_url:-<missing>}"
      exit 1
      ;;
  esac

  dirty="$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=no)"
  if [[ -n "$dirty" ]]; then
    echo "Tracked local changes detected in $PROJECT_DIR. Aborting."
    echo "$dirty"
    exit 1
  fi

  echo "Updating project..."
  git -C "$PROJECT_DIR" pull --ff-only
else
  echo "Cloning project..."
  git clone "$REPO_URL" "$PROJECT_DIR"
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
else
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV_BIN="/root/.local/bin/uv"
fi

if [[ ! -x "$UV_BIN" ]]; then
  echo "uv was not found at $UV_BIN"
  exit 1
fi

cd "$PROJECT_DIR"

echo "Creating/updating Python virtual environment..."
"$UV_BIN" venv
"$UV_BIN" pip install -r requirements.txt

echo "Installing Playwright Chromium and Linux dependencies..."
"$UV_BIN" run playwright install chromium
"$UV_BIN" run playwright install-deps chromium

echo "Registering systemd service..."
install -m 0644 "$PROJECT_DIR/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

echo "Installing gta command..."
chmod 0755 "$PROJECT_DIR/ctl.py"
ln -sfn "$PROJECT_DIR/ctl.py" "$GTA_BIN"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "Creating .env from .env.example. Please edit it manually."
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
else
  echo ".env already exists; leaving it unchanged."
fi

cat <<EOF

Installation finished.

Next steps:
  1. Edit /root/GmgnTwitterTgAlert/.env manually.
  2. Start with: gta start
  3. Stop with: gta stop
  4. Restart and reload .env with: gta restart
  5. Watch status/logs with: gta status
  6. Update code/dependencies with: gta update

Optional WARP proxy:
  gta warp
  Then set PROXY_SERVER=socks5://127.0.0.1:40000 in .env if needed.

The service was not started or enabled automatically.
EOF
