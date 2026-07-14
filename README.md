# GMGN Twitter Telegram Alert

通过 Playwright 监听 GMGN 的 Twitter/KOL 实时信号，标准化并推送到一个或多个 Telegram 群组。项目只包含 Telegram 下游推送，不开放 WebSocket 端口，也不包含飞书或 HTTP Webhook。

## 功能

- 发帖、转推、回复、引用、关注/取关、删帖、头像/横幅/昵称/简介和置顶变更等动作。
- `cp=0` 快照立即推送，`cp=1` 完整数据到达后编辑同一条 Telegram 消息。
- 按 handle 将消息路由到多个 Telegram 群组，同一账号可属于多个组。
- 可选 DeepSeek 翻译、投资赛道分析、摘要和赛道过滤。
- 可选按 Telegram 群组生成每日定时摘要。
- 浏览器登录态持久化，systemd 崩溃重启并每 12 小时刷新进程。

## 支持的服务器

- Ubuntu 22.04、24.04、26.04
- Debian 12、13
- x86_64 或 arm64
- 使用 root 用户执行安装命令；常驻服务实际以低权限 `gmgn-monitor` 用户运行。

其他发行版或旧版本会在修改系统前被安装器拒绝。

## 准备工作

1. 在 [BotFather](https://t.me/BotFather) 创建 Bot，保存 Token。
2. 将 Bot 加入目标 Telegram 群组，并授予发送消息权限。
3. 获取群组 ID。超级群组 ID 通常形如 `-1001234567890`，可通过 Bot API `getUpdates` 或常用 ID Bot 查询。
4. 从 GMGN Telegram Bot 获取未过期的 `https://gmgn.ai/tglogin?...` 授权链接。
5. 如需翻译或摘要，准备 DeepSeek API Key。

建议使用专门的 GMGN 小号保存登录态，避免主账号风险。

## 一键安装或升级

在 VPS 的 root Shell 中执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh)
```

首次安装会依次：

1. 检查系统版本与 CPU 架构。
2. 检测已有 SOCKS5 代理；不可用时询问安装 Cloudflare WARP 或使用自有代理。
3. 收集 Telegram Bot Token 和一个或多个路由组。
4. 使用 Telegram `getMe`/`getChat` 验证 Bot 和群组，不发送测试消息。
5. 安装 uv、Python 3.12、锁定依赖、Playwright Chromium 和 Linux 依赖。
6. 使用 GMGN 授权链接执行一次登录，保存浏览器状态。
7. 创建并启动 systemd 服务。

再次执行同一命令就是升级。配置、浏览器登录态和数据库不会被覆盖；新版本健康检查失败时会切回旧 release。

### 无交互安装

自动化环境至少需要提供以下变量，并明确代理策略：

```bash
export NONINTERACTIVE=1
export TG_BOT_TOKEN='123456789:replace-me'
export TG_CHANNEL_ID_ALL='-1001234567890'
export GMGN_AUTH_URL='https://gmgn.ai/tglogin?...'

# 二选一
export PROXY_SERVER='socks5://127.0.0.1:1080'
# export INSTALL_WARP=1

bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh)
```

`ALL` 群组接收所有消息，不需要配置 handles。可选变量包括 `DEEPSEEK_API_KEY`、`SUMMARY_ENABLE`、`SUMMARY_TIMES`、`SUMMARY_TIMEZONE`。

## 重新配置与授权

重新运行配置向导：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh) reconfigure
```

GMGN 登录过期时重新授权：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh) relogin
```

重新授权失败时，安装器会恢复原浏览器登录态。

## Telegram 路由配置

生产配置位于 `/etc/gmgn-twitter-monitor/gmgn.env`。`ALL` 是不需要 handles 的全量群组：

```env
TG_ENABLE_DEFAULT=True
TG_CHANNEL_ID=-1001234567890
TG_CHANNEL_ID_ALL=-1001234567890
```

还可添加按 handles 分流的定向组，每个组使用相同后缀：

```env
TG_ROUTING_BINANCE=cz_binance,heyibinance
TG_ENABLE_BINANCE=True
TG_CHANNEL_ID_BINANCE=-1001234567890
TG_TRACK_FILTER_BINANCE=

TG_ROUTING_MUSK=elonmusk
TG_ENABLE_MUSK=True
TG_CHANNEL_ID_MUSK=-1009876543210
TG_TRACK_FILTER_MUSK=
```

- handle 不带 `@`，推荐小写。
- 同一 handle 可以出现在多个组中。
- `TG_TRACK_FILTER_*` 依赖 AI 分析结果；使用时还要把相应 handle 加入 `AI_ANALYZE_HANDLES`。
- `ALL` 群组会始终收到所有消息；命中定向路由时，消息会同时发送到 `ALL` 和对应定向群组。

手工编辑后执行：

```bash
systemctl restart gmgn-twitter-monitor
```

## DeepSeek 与定时摘要

```env
DEEPSEEK_API_KEY=sk-xxxxxxxx
AI_ANALYZE_HANDLES=elonmusk,aleabitoreddit

SUMMARY_ENABLE=True
SUMMARY_TIMEZONE=Asia/Shanghai
SUMMARY_TIMES=07:30,20:00
SUMMARY_GROUPS=BINANCE
SUMMARY_LABEL_BINANCE=Binance
```

DeepSeek Key 留空时只发送原文。摘要数据来自对应 `TG_CHANNEL_ID_<GROUP>` 的成功投递记录，并发送回同一 Telegram 群组。

## 服务管理

```bash
systemctl status gmgn-twitter-monitor --no-pager
systemctl restart gmgn-twitter-monitor
systemctl stop gmgn-twitter-monitor
journalctl -u gmgn-twitter-monitor -f
```

路径：

| 内容 | 路径 |
|---|---|
| 当前程序 | `/opt/gmgn-twitter-monitor/current` |
| 配置 | `/etc/gmgn-twitter-monitor/gmgn.env` |
| 浏览器登录态 | `/var/lib/gmgn-twitter-monitor/browser_data` |
| 会话登录态 | `/var/lib/gmgn-twitter-monitor/gmgn_session_storage.json` |
| 完整认证状态 | `/var/lib/gmgn-twitter-monitor/gmgn_storage_state.json` |
| SQLite | `/var/lib/gmgn-twitter-monitor/twitter_monitor.db` |
| 文件日志 | `/var/lib/gmgn-twitter-monitor/twitter_monitor.log` |
| 运行截图 | `/var/lib/gmgn-twitter-monitor/monitor_running.png` |
| 授权失败截图 | `/var/lib/gmgn-twitter-monitor/login_failed.png` |

## 卸载

默认卸载会先把配置、登录态和数据库备份到 `/root/gmgn-twitter-monitor-backups/<时间>/`：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh) uninstall
```

彻底删除且不创建备份：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/install.sh) uninstall --purge
```

卸载不会删除 WARP、uv 或 apt 系统包，避免影响服务器上的其他程序。

## 故障排查

- `Bot 无法访问群组`：确认 Bot 已加入群组，群组 ID 正确，并拥有发消息权限。
- `代理无法连通`：运行 `curl --proxy socks5://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace`；WARP 用户再检查 `warp-cli status`。
- `You are not logged in to GMGN`：执行 `relogin` 并输入新的单次授权链接；授权命令会进入监控页验证实际登录状态，不再仅凭授权页成功加载作判断。
- `无法定位 Mine/我的`：登录态可能失效，执行 `relogin`。
- Chromium 启动失败：查看 `journalctl -u gmgn-twitter-monitor -n 100 --no-pager`。
- 没有翻译：确认 `DEEPSEEK_API_KEY` 有效；赛道分析还要求 handle 位于 `AI_ANALYZE_HANDLES`。

## 本地开发

```bash
uv sync --python 3.12 --frozen
uv run playwright install chromium
cp .env.example .env
uv run python -m gmgn_twitter_monitor
```

一次性登录：

```bash
GMGN_AUTH_URL='https://gmgn.ai/tglogin?...' uv run python -m gmgn_twitter_monitor --login
```

## License

MIT
