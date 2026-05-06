#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="gmgn-twitter-monitor.service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  echo "This installer is intended for Ubuntu."
  exit 1
fi

echo "Installing base packages..."
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release xvfb

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

echo "Creating/updating Python virtual environment..."
cd "$PROJECT_DIR"
"$UV_BIN" venv
"$UV_BIN" pip install -r requirements.txt

echo "Installing Playwright Chromium and Linux dependencies..."
"$UV_BIN" run playwright install chromium
"$UV_BIN" run playwright install-deps chromium

echo "Registering systemd service..."
install -m 0644 "$PROJECT_DIR/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

cat <<EOF

Installation finished.

Next steps:
  1. cp .env.example .env
  2. Edit .env with Telegram and GMGN authorization values.
  3. Run the first login manually if needed:
     FIRST_RUN_LOGIN=True in .env, then:
     $UV_BIN run python -m gmgn_twitter_monitor
  4. Set FIRST_RUN_LOGIN=False after browser_data is created.
  5. Start the service manually:
     systemctl start $SERVICE_NAME

The service was not started or enabled automatically.
EOF
