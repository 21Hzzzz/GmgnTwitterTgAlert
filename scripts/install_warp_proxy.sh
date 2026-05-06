#!/usr/bin/env bash
set -euo pipefail

PROXY_PORT="${WARP_PROXY_PORT:-40000}"

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

echo "Installing Cloudflare WARP..."
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/cloudflare-client.list

apt-get update
apt-get install -y cloudflare-warp
systemctl enable --now warp-svc

echo "Registering WARP client if needed..."
if ! warp-cli registration show >/dev/null 2>&1; then
  warp-cli --accept-tos registration new || warp-cli --accept-tos register
fi

echo "Switching WARP to local proxy mode on port ${PROXY_PORT}..."
warp-cli mode proxy || warp-cli set-mode proxy
warp-cli proxy port "$PROXY_PORT" || warp-cli set-proxy-port "$PROXY_PORT"
warp-cli connect

echo "Testing local WARP proxy..."
curl --proxy "socks5h://127.0.0.1:${PROXY_PORT}" https://www.cloudflare.com/cdn-cgi/trace

cat <<EOF

WARP proxy setup finished.

To use it for this project, set this in /root/GmgnTwitterTgAlert/.env:
  PROXY_SERVER=socks5://127.0.0.1:${PROXY_PORT}
EOF
