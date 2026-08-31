# GmgnTwitterClaw 🦅

**基于 GMGN.ai 的实时 Twitter KOL 监控引擎**，通过浏览器自动化拦截 WebSocket 数据流，将推特动态标准化后实时推送到 Telegram 频道。

### ✨ 核心特性

- **全动作捕获**：覆盖发推、转推、回复、引用、关注/取关、删帖、换头像、换横幅、改昵称、改简介、置顶/取消置顶共 13 种推特行为
- **智能图文预览**：纯图推文优先使用原图直链确保 100% 准确预览，含视频推文通过 FxTwitter 实现内嵌播放，关注/取关等主页类动作通过 vxTwitter 渲染为用户名片
- **免费机器翻译 + DeepSeek AI 分析**：普通推文走 Google → Microsoft → 腾讯交互翻译（无需 Key），推送完成后自动追加中文译文；指定博主（如白毛股神）仍走 DeepSeek 做赛道分析、摘要与翻译
- **多频道智能路由**：按推特 Handle 分组路由到不同 Telegram 频道，同一博主可同时推送至多个频道
- **双轨数据捕获**：WebSocket 实时监听 + HTTP Polling 降级拦截，重连间隙零丢失
- **去重引擎**：基于 `internal_id` 的快照/完整版智能去重，快照即时推送并在 5 秒内以完整版更新
- **Telegram 专用分发**：按推特 Handle 分组路由到一个或多个 Telegram 频道
- **12 小时自动刷新**：systemd `RuntimeMaxSec` 定时重启，防止长时间运行导致浏览器内存泄漏

---

## 💡 FAQ：首次授权与账号准备必读

在开始部署之前，你需要了解 GMGN 的底层授权机制：

- **GMGN 官网**: [https://gmgn.ai/r/1RFSf1fc?chain=bsc](https://gmgn.ai/r/1RFSf1fc?chain=bsc)
- **获取授权链接**: 首次使用时，在 Telegram 中找到 GMGN Bot 提供的专属登录/授权链接（右键复制链接），填入 `.env` 的 `AUTH_URL`，并将 `FIRST_RUN_LOGIN=True`。
- **⚠️ 账号风控注意**: 强烈建议使用一个 **空 TG / 小号** 来扫码授权隔离风险。但请注意 GMGN 官方规则：对于没有任何交易量的纯空号，GMGN 会限制其关注小众博主（需要有交易量才能解锁）。相关限制规则请自行了解。
- **📹 推特演示说明**: [点此查看视频说明演示](https://x.com/0xTechMelon/status/2049114161498726883?s=20)

---

## 📂 项目结构

```
GmgnTwitterClaw/
├── gmgn_twitter_monitor/          # 核心包
│   ├── __init__.py
│   ├── __main__.py                # python -m 入口
│   ├── app.py                     # 主循环：浏览器启动 + WS/Polling 双轨拦截 + 去重引擎
│   ├── browser.py                 # Playwright 浏览器生命周期管理（启动/登录/截图/恢复）
│   ├── config.py                  # 配置中心：从 .env 读取环境变量 + 路由分组解析
│   ├── distributor.py             # 日志与 Telegram 分发、消息格式化
│   ├── analyzer.py                # DeepSeek AI 深度分析（赛道分类/摘要/A股提取/翻译）
│   ├── logging_setup.py           # loguru 日志格式化
│   ├── models.py                  # StandardizedMessage 数据模型（dataclass）
│   ├── parser.py                  # 原始 WS 数据 → 标准化 JSON 转换器
│   ├── translator.py              # 免费机器翻译（Google → Microsoft → 腾讯交互翻译）
│   └── watchdog.py                # 看门狗：超时无数据自动刷新页面
├── gmgn_twitter_monitor.py        # 兼容入口（等价于 python -m gmgn_twitter_monitor）
├── ctl.py                         # 交互式运维控制台（服务管理/日志查看/截图等）
├── gmgn-twitter-monitor.service   # systemd 服务单元文件
├── .env.example                   # 环境变量模板
├── requirements.txt               # Python 依赖清单
└── browser_data/                  # 浏览器登录态持久化目录（自动生成，勿删）
```

---

## 🚀 快速安装

在新的 Ubuntu/Debian VPS 上直接执行：

```bash
curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/4f9ad06/install.sh | bash
```

脚本会自行拉取项目、创建虚拟环境、安装 Python/Chromium 依赖、询问 Telegram 配置和可选的 GMGN 授权链接，并可立即启动。`PROXY_SERVER` 可选，留空即直连。


## 🏗️ 系统架构

### Telegram 分发架构

```
   gmgn.ai WebSocket / HTTP Polling
              │
              ▼
   ┌─────────────────────┐
   │  Parser 标准化 JSON   │ ← 13 种 Twitter 动作全解析
   └──────────┬──────────┘
              │
    MessageDeduplicator      ← 快照即时推送、完整版更新
              │
    DistributorHub.publish()
     ┌────────┴────────┐
┌────▼───┐          ┌──▼────┐
│Logging │          │  TG   │
│ 日志   │          │ 频道  │
└────────┘          │ 多频道│
                     │ 路由  │
                     └──┬────┘
              │
        Google/微软异步翻译
       (推送后追加译文；AI 账号走 DeepSeek)
```

### Telegram 推送特性

- **智能图片预览**：纯图推文直接使用 WebSocket 底层提取的真实图片直链 (`pbs.twimg.com`) 作为 `link_preview_options.url`，100% 准确展示原图（多图时展示首图）；含视频推文自动降级到 `fxtwitter.com` 实现内嵌播放
- **vxTwitter 主页名片**：关注、取关、改昵称、改简介等动作自动通过 `vxtwitter.com` 渲染为用户头像+简介的名片卡
- **原帖直达链接**：卡片底部统一附带 `x.com` 原帖/主页链接，支持点击直达
- **换头像对比图**：头像变更动作保留原生 `sendMediaGroup`，展示新旧头像的并列对比
- **免费机器翻译与 DeepSeek 深度分析**：普通推文发送后异步走 Google → Microsoft → 腾讯交互翻译。对 `AI_ANALYZE_HANDLES` 中的博主（如白毛股神）额外进行投资赛道分析、智能摘要及 A 股个股提取，完成后自动编辑原消息追加分析与译文，主推送流程零阻塞
- **429 退避重试**：遇到 Telegram Rate Limit 时自动等待并重试

---

## 📡 推送数据格式（标准化 JSON）

每条消息对应一个 Twitter 动作，三大通道（Telegram/WebSocket/Webhook）使用完全一致的 JSON 结构：

```json
{
  "action": "tweet",
  "original_action": null,
  "tweet_id": "1234567890123456789",
  "internal_id": "abc123def456",
  "timestamp": 1712300000,
  "author": {
    "handle": "cz_binance",
    "name": "CZ 🔶 BNB",
    "avatar": "https://pbs.twimg.com/profile_images/xxx/photo.jpg",
    "followers": 12800000,
    "tags": ["Smart_kol"]
  },
  "content": {
    "text": "推文正文内容...",
    "media": [
      { "type": "photo", "url": "https://pbs.twimg.com/media/xxx.jpg" }
    ]
  },
  "reference": {
    "tweet_id": "9876543210",
    "author_handle": "elonmusk",
    "author_name": "Elon Musk",
    "author_avatar": "https://pbs.twimg.com/...",
    "author_followers": 239600000,
    "text": "被引用/回复/转推的原文...",
    "media": [],
    "type": "quoted"
  },
  "unfollow_target": null,
  "avatar_change": null,
  "bio_change": null
}
```

### `action` 字段枚举（共 13 种）

| 值 | 含义 | 说明 |
|----|------|------|
| `tweet` | 发布新推文 | 原创推文，`content.text` 有正文 |
| `repost` | 转推（RT） | `reference` 包含被转推的原推信息 |
| `reply` | 回复 | `reference` 包含被回复的原推信息 |
| `quote` | 引用推文 | `content.text` 有引用评论，`reference` 有原推 |
| `follow` | 新增关注 | `unfollow_target` 包含被关注者信息 |
| `unfollow` | 取消关注 | `unfollow_target` 包含被取关者信息 |
| `delete_post` | 删除推文 | `original_action` 记录被删推文的原始类型 |
| `photo` | 更换头像 | `avatar_change` 包含 `before`/`after` 头像 URL |
| `banner` | 更换横幅 | `banner_change` 包含 `before`/`after` 横幅 URL；GMGN 上游 `tw=other` 且包含横幅字段时会归一为该动作 |
| `description` | 简介更新 | `bio_change` 包含 `before`/`after` 简介文本 |
| `name` | 更改昵称 | 作者信息中包含新昵称 |
| `pin` | 置顶推文 | `tweet_id` 包含被置顶的推文 ID |
| `unpin` | 取消置顶 | `tweet_id` 包含被取消置顶的推文 ID |

### 条件字段说明

| 字段 | 出现条件 |
|------|----------|
| `reference` | `repost` / `reply` / `quote` / `delete_post` |
| `unfollow_target` | `follow` / `unfollow` |
| `avatar_change` | `photo` |
| `banner_change` | `banner` |
| `bio_change` | `description` |
| `original_action` | `delete_post` |

---

## 🔌 WSS 客户端接入示例

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

---

## 📋 配置速查

| 配置项 | 值 |
|--------|-----|
| TG 推送 | `.env → TG_BOT_TOKEN` + 路由分组变量 |
| 翻译 | 普通推文免费 MT（`TRANSLATE_PROVIDERS`）；AI 账号 `.env → DEEPSEEK_API_KEY` |
| GMGN 上游心跳日志 | 10 分钟 |
| 看门狗超时 | 120 秒（无消息自动刷新页面） |
| 服务自动重启 | 每 12 小时（`RuntimeMaxSec=43200`） |
| 监控目标 | `gmgn.ai/follow?target=xTracker&chain=bsc` |
| WARP 代理 | `socks5://127.0.0.1:40000` |
| SSL 证书 | Let's Encrypt，Certbot 自动续期 |

---

## 📜 License

MIT
