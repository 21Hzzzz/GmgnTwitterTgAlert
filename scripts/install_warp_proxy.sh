#!/usr/bin/env bash
set -euo pipefail

PROXY_PORT="${WARP_PROXY_PORT:-40000}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 用户运行 WARP 安装脚本。"
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "无法检测 Linux 发行版：缺少 /etc/os-release。"
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "此 WARP 安装脚本仅支持 Ubuntu。"
  exit 1
fi

echo "正在安装 Cloudflare WARP..."
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/cloudflare-client.list

apt-get update
apt-get install -y cloudflare-warp
systemctl enable --now warp-svc

echo "正在注册 WARP 客户端（如已注册会跳过）..."
if ! warp-cli registration show >/dev/null 2>&1; then
  warp-cli --accept-tos registration new || warp-cli --accept-tos register
fi

echo "正在切换 WARP 到本地代理模式，端口：${PROXY_PORT}..."
warp-cli mode proxy || warp-cli set-mode proxy
warp-cli proxy port "$PROXY_PORT" || warp-cli set-proxy-port "$PROXY_PORT"
warp-cli connect

echo "正在测试本地 WARP 代理..."
curl --proxy "socks5h://127.0.0.1:${PROXY_PORT}" https://www.cloudflare.com/cdn-cgi/trace

cat <<EOF

WARP 本地代理安装完成。

如需让本项目使用该代理，请在 /root/GmgnTwitterTgAlert/.env 中设置：
  PROXY_SERVER=socks5://127.0.0.1:${PROXY_PORT}
EOF
