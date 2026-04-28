# Gmgn Twitter Monitor 迁移与部署指南

这份文档记录了本项目在全新的无显卡（Headless）Linux VPS 上从零开始运行所需的所有"脚手架"命令。
如果你需要将此爬虫/监控程序迁移到其他的服务器，请严格按照以下步骤依次执行。

## 💡 FAQ: 首次授权与账号准备必读

在开始部署之前，你需要了解 GMGN 的底层授权机制：

- **GMGN 官网**: [https://gmgn.ai/r/1RFSf1fc?chain=bsc](https://gmgn.ai/r/1RFSf1fc?chain=bsc)
- **获取授权链接**: 首次使用时，你需要在 Telegram 中找到 GMGN Bot 提供的专属登录/授权链接（右键复制链接），并将其填入到本项目的配置文件 `config.py` 中的 `AUTH_URL` 里（详见下文第5步）。
- **⚠️ 账号风控注意**: 强烈建议使用一个 **空 TG / 小号** 来扫码授权隔离风险。但请注意 GMGN 官方规则：对于没有任何交易量的纯空号，GMGN 会限制其关注小众博主（需要有交易量才能解锁）。相关限制规则请自行了解。
- **📹 推特演示说明**: [点此查看视频说明演示](https://x.com/0xTechMelon/status/2049114161498726883?s=20)

---

## 1. 安装基础依赖和 Python 工具 `uv`

`uv` 是比原生的 `pip` 快几百倍的现代化 Python 环境管理工具，本程序使用它来隔离虚拟环境。

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 使 uv 在当前终端立即生效
source $HOME/.local/bin/env

# 进入项目目录 (假设项目已经 clone 到了服务器的主目录下)
cd ~/workspace/GmgnTwitterClaw

# 创建独立虚拟环境并安装 requirements.txt 声明的 Python 库
uv venv
uv pip install -r requirements.txt
```

## 2. 安装 Playwright 内核与 Linux 缺失的底层桌面包

因为程序的核心本质是操纵真的浏览器进行抓取，所以我们需要安装浏览器内核及在 Linux 裸机运行虚拟桌面所必须的 C 语言底层库。

```bash
# 下载 Chromium 浏览器内核
uv run playwright install chromium

# 一键安装 Linux 运行 Chrome 所必需的全套底层依赖 (例如 libatk, libgbm, libdrm 等，会自动调用 apt)
sudo uv run playwright install-deps chromium
```

## 3. 设置 Cloudflare WARP 代理 (突破 IP 盾防御核心)

如果不配置这一步，机房 VPS 的 IP 访问 gmgn.ai 会被 Cloudflare 100% 出现盾阻断（"Sorry, you have been blocked"），甚至连验证码都不会给。通过挂载官方 WARP 服务，并将其转化为本地 Proxy，脚本将可以获得家庭宽带级别的隐身穿透能力。

```bash
# 1. 注入 Cloudflare 的 GPG 密钥并添加官方 APT 源 (仅限 Ubuntu/Debian 系示范)
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list

# 2. 安装并注册 Warp 客户端
sudo apt-get update
sudo apt-get install -y cloudflare-warp

warp-cli registration new
# (中途如果遇到隐私提示，输入 Y 回车同意)

# 3. 将 Warp 设定为本地 Socks5 代理模式，把端口绑定到 40000
warp-cli mode proxy
warp-cli proxy port 40000

# 4. 连接
warp-cli connect

# 5. (可选) 测试代理是否通顺
curl -x socks5://127.0.0.1:40000 https://cloudflare.com/cdn-cgi/trace
# 如果输出的信息中有 warp=on 字眼，说明穿透成功。
```

## 4. 配置环境变量

所有敏感信息通过 `.env` 文件管理，**严禁提交到 Git**（已在 `.gitignore` 中屏蔽）。

```bash
# 复制模板并填入真实值
cp .env.example .env
nano .env
```

`.env` 配置项说明：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `WS_TOKEN` | WebSocket 鉴权 Token，建议强随机串 | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TG_BOT_TOKEN` | Telegram Bot API Token | `123456:ABC-DEF...` |
| `TG_CHANNEL_ID` | 目标 TG 频道 ID | `-100xxxxxxxxxx` |
| `TG_FILTER_HANDLES` | 只转发这些 handle（逗号分隔），留空转发全部 | `cz_binance,heyibinance` |
| `WEBHOOK_URL` | Webhook 推送目标 URL（留空则禁用） | `https://your-site.com/api/webhook` |
| `WEBHOOK_SECRET` | HMAC-SHA256 签名密钥（可选） | 任意字符串 |

## 5. 首次运行与授权 (代码配置)

当你的新服务器第一次打算跑脚本时，你需要让程序获得你具体的身份登录状态。

1. 修改 `gmgn_twitter_monitor/config.py`，将 `FIRST_RUN_LOGIN` 改为 `True`。
2. 在同一个文件里，将 `AUTH_URL` 赋值为你最新的（未过期）授权链接 `https://gmgn.ai/tglogin?...&id=...`。
3. 执行监控脚本：

```bash
uv run python -m gmgn_twitter_monitor
```
> 该程序会自动利用 `xvfbwrapper` 在后台开启隐形的虚拟桌面，使用有头模式（突破 CF 封锁）访问该授权页面，并等候 8 秒将登录凭证序列化写入当前目录下的 `./browser_data` 文件夹。此后它会自动关闭可能会弹出的弹窗，切换到【我的】标签进行监听。
>
> 兼容方式仍然保留：如果你已有旧脚本依赖，也可以继续执行 `uv run python gmgn_twitter_monitor.py`。

**【重要】**
一旦第一次看到日志输出获取成功，为了加速以后重启的流程，建议你回去把 `gmgn_twitter_monitor/config.py` 里的 `FIRST_RUN_LOGIN` 重新改回 `False`。只要 `browser_data` 文件夹不被删，服务器就可以在接下来的很长一段时间内复用该状态免密直接连接。

## 6. 三通道推送架构

系统内置 4 个分发器，通过 `DistributorHub` 扇出架构并行推送，任何一个通道失败不影响其余：

```
                  ┌──────────────────────────────────────────┐
                  │         Parser 标准化 JSON                │
                  └────────────────┬─────────────────────────┘
                                   │
                          DistributorHub.publish()
                     ┌─────────────┼──────────────┐
                     │             │              │
              ┌──────▼──────┐ ┌───▼────────┐ ┌───▼──────────┐
              │  Telegram   │ │  Webhook   │ │   WSS 广播    │
              │  频道推送   │ │  HTTP POST │ │  实时连接     │
              │ (按 handle  │ │ (HMAC签名) │ │ (Token 鉴权) │
              │  白名单过滤)│ │            │ │              │
              └──────┬──────┘ └─────┬──────┘ └──────┬───────┘
                     │              │               │
                TG Bot API     你的聚合站     wss://your-domain.com/ws
```

### 6.1 Telegram 频道推送

- **自动过滤**: 仅转发 `TG_FILTER_HANDLES` 中指定账号的推文
- **富文本格式**: HTML 格式，包含作者信息、推文内容、引用来源、媒体附件、原推链接
- **429 退避**: 遇到 Telegram Rate Limit 时自动等待并重试
- **配置**: 在 `.env` 中设置 `TG_BOT_TOKEN` 和 `TG_CHANNEL_ID`

### 6.2 Webhook 推送

- **标准 JSON POST**: 将完整的标准化消息体 POST 到目标 URL
- **HMAC-SHA256 签名**: 可选，通过 `X-Signature-SHA256` 请求头传递，接收端可验证来源
- **按需启用**: `WEBHOOK_URL` 为空时自动跳过，无额外开销
- **配置**: 在 `.env` 中设置 `WEBHOOK_URL` 和 `WEBHOOK_SECRET`

### 6.3 WSS 实时连接

- **加密连接**: 通过 Nginx 反代 + Let's Encrypt 证书实现 TLS 加密
- **连接地址**: `wss://your-domain.com/ws`
- **鉴权方式**: 连接后 10 秒内发送 `{"token": "your-ws-token"}`
- **心跳**: 服务端每 30 秒 ping，客户端自动 pong（websockets 库默认处理）
- **配置**: 在 `.env` 中设置 `WS_TOKEN`

## 7. Nginx + TLS 配置 (WSS)

如果在新服务器上需要重新配置 WSS：

```bash
# 安装 Nginx 和 Certbot
sudo apt-get install -y nginx certbot python3-certbot-nginx

# 创建站点配置（参考 /etc/nginx/sites-available/your-domain.com）
# 启用站点
sudo ln -sf /etc/nginx/sites-available/your-domain.com /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# 申请 SSL 证书（自动注入 Nginx 配置）
sudo certbot --nginx -d your-domain.com --non-interactive --agree-tos --email your@email.com --redirect

# 测试配置
sudo nginx -t && sudo systemctl reload nginx
```

证书由 Certbot 的 systemd timer 自动续期，无需手动干预。

## 8. systemd 服务自动守护

```bash
# 将 service 文件链接到 systemd 目录
sudo ln -sf $(pwd)/gmgn-twitter-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload

# 开机自启 + 立即启动
sudo systemctl enable gmgn-twitter-monitor.service
sudo systemctl start gmgn-twitter-monitor.service

# 查看运行状态
sudo systemctl status gmgn-twitter-monitor.service

# 查看实时日志
sudo journalctl -u gmgn-twitter-monitor -f
```

崩溃后 10 秒自动重启（由 `RestartSec=10` 控制）。

## 9. WSS 客户端接入示例

```python
import asyncio
import json

import websockets
from loguru import logger

WS_URL = "wss://your-domain.com/ws"
TOKEN  = "your-ws-token"  # 与 .env 中 WS_TOKEN 一致

async def handle_signal(msg: dict):
    action = msg["action"]
    handle = msg["author"]["handle"]
    text   = msg["content"]["text"] or ""
    logger.info(f"[{action}] @{handle}: {text[:80]}")

async def listen_forever():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({"token": TOKEN}))
                resp = json.loads(await ws.recv())
                assert resp.get("status") == "connected", f"鉴权失败: {resp}"
                logger.success("✅ 已连接，开始接收信号...")
                async for raw in ws:
                    await handle_signal(json.loads(raw))
        except (websockets.exceptions.ConnectionClosed,
                OSError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ 连接断开: {e}，5秒后重连...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(listen_forever())
```

## 10. 推送数据格式（标准化 JSON）

每条消息对应一个 Twitter 动作：

```json
{
  "action": "tweet",
  "tweet_id": "1234567890123456789",
  "internal_id": "abc123",
  "timestamp": 1712300000,
  "author": {
    "handle": "cz_binance",
    "name": "CZ 🔶 BNB",
    "avatar": "https://pbs.twimg.com/profile_images/xxx/photo.jpg",
    "followers": 12800000
  },
  "content": {
    "text": "推文正文内容...",
    "media": [
      { "type": "photo", "url": "https://pbs.twimg.com/media/xxx.jpg" }
    ]
  },
  "reference": null
}
```

### `action` 字段枚举

| 值 | 含义 |
|----|------|
| `tweet` | 原创推文 |
| `repost` | 转推（RT） |
| `reply` | 回复 |
| `quote` | 引用推文 |
| `unknown` | 未知类型 |

### Webhook 签名验证示例

```python
import hmac
import hashlib

def verify_signature(body: bytes, secret: str, received_signature: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_signature)

# 在你的接收端：
# signature = request.headers.get("X-Signature-SHA256")
# is_valid = verify_signature(request.body, "your-secret", signature)
```

## 11. 配置速查

| 配置项 | 值 |
|--------|-----|
| WSS 地址 | `wss://your-domain.com/ws` |
| 鉴权 Token | `.env → WS_TOKEN` |
| TG 推送 | `.env → TG_BOT_TOKEN + TG_CHANNEL_ID` |
| Webhook | `.env → WEBHOOK_URL` |
| 心跳间隔 | 30 秒 |
| 看门狗超时 | 120 秒（无消息自动刷新页面） |
| 监控目标 | `gmgn.ai/follow?target=xTracker&chain=bsc` |
| WARP 代理 | `socks5://127.0.0.1:40000` |
| SSL 证书 | Let's Encrypt，Certbot 自动续期 |