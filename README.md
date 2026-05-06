# GmgnTwitterTgAlert

基于 GMGN.ai 的 Twitter/KOL 实时监控转发工具。当前 fork 已收窄为 **Telegram-only**：程序通过 Playwright 浏览器监听 GMGN 页面数据，将标准化后的消息发送到一个主群组，并按可选路由分组额外发送到指定 Telegram 群组或频道。

## 当前特性

- 捕获 GMGN 推送的发推、转推、回复、引用、关注/取关、删帖、换头像、改昵称、改简介、置顶/取消置顶等动作。
- 主群组接收所有捕获到的消息。
- 分组路由按 Twitter handle 匹配，同一条消息可以同时发往主群和多个分组。
- Telegram 频道 ID 自动去重，避免同一目标收到重复消息。
- DeepSeek 翻译可选，配置 API key 后会在 Telegram 消息发送后追加中文译文。
- WARP 代理可选，默认直连；只有配置 `PROXY_SERVER` 时才走代理。
- systemd 守护，默认 12 小时重启一次，降低长期浏览器运行带来的状态漂移。

## 目录结构

```text
GmgnTwitterTgAlert/
├── gmgn_twitter_monitor/          # 核心包
│   ├── app.py                     # 主循环和浏览器监听
│   ├── browser.py                 # Playwright 浏览器管理
│   ├── config.py                  # .env 配置读取与 Telegram 路由解析
│   ├── distributor.py             # Telegram 分发器
│   ├── parser.py                  # GMGN 原始数据标准化
│   ├── translator.py              # DeepSeek 翻译
│   └── watchdog.py                # 无消息超时刷新
├── scripts/
│   ├── install_root_ubuntu.sh     # root/Ubuntu 部署脚本
│   └── install_warp_proxy.sh      # 可选 WARP 本地代理脚本
├── gmgn-twitter-monitor.service   # systemd service
├── .env.example                   # 配置模板
├── ctl.py                         # 服务控制台
└── requirements.txt
```

## 部署环境

本 fork 默认部署方式：

- Ubuntu / Debian-like Linux
- 使用 `root` 用户
- 项目路径：`/root/GmgnTwitterTgAlert`
- 不在 Windows 环境运行服务；Windows 只用于代码校验

## 1. 克隆并安装

```bash
cd /root
git clone <your-fork-url> GmgnTwitterTgAlert
cd /root/GmgnTwitterTgAlert
bash scripts/install_root_ubuntu.sh
```

安装脚本会：

- 安装基础 apt 包和 `uv`
- 创建/更新 `.venv`
- 安装 Python 依赖
- 安装 Playwright Chromium 和 Linux 运行依赖
- 注册 systemd service 并执行 `systemctl daemon-reload`

脚本不会启动服务，也不会设置开机自启。这样可以避免 `.env` 或首次授权未准备好时误运行。

## 2. 配置 Telegram

```bash
cp .env.example .env
nano .env
```

核心配置：

```env
FIRST_RUN_LOGIN=False
AUTH_URL=
PROXY_SERVER=

TG_BOT_TOKEN=123456789:your-token

TG_ENABLE_MAIN=True
TG_MAIN_CHANNEL_ID=-1001234567890

TG_ROUTING_BINANCE=cz_binance,heyibinance
TG_ENABLE_BINANCE=True
TG_CHANNEL_ID_BINANCE=-1001234567891

TG_FILTER_HANDLES=
DEEPSEEK_API_KEY=
```

配置语义：

- `TG_ENABLE_MAIN=True` 且 `TG_MAIN_CHANNEL_ID` 有值时，主群接收所有捕获消息。
- `TG_ROUTING_<GROUP>` 中的 handle 命中后，会额外发送到 `TG_CHANNEL_ID_<GROUP>`。
- 同一个 handle 可以放进多个分组。
- 如果分组频道 ID 与主群相同，程序只会发送一次。
- `TG_FILTER_HANDLES` 默认为空，表示不过滤；一旦填写，它就是全局白名单，未列入的 handle 连主群也不会收到。

## 3. 首次 GMGN 授权

首次部署需要写入浏览器登录态：

1. 在 Telegram 中从 GMGN Bot 获取未过期的授权链接。
2. 在 `.env` 中设置：

```env
FIRST_RUN_LOGIN=True
AUTH_URL=https://gmgn.ai/tglogin?...
```

3. 手动运行一次：

```bash
/root/.local/bin/uv run python -m gmgn_twitter_monitor
```

看到浏览器状态写入成功后停止程序，然后把 `.env` 改回：

```env
FIRST_RUN_LOGIN=False
```

只要 `browser_data/` 不被删除，后续 systemd 服务会复用登录态。

## 4. 启动与运维

手动启动：

```bash
systemctl start gmgn-twitter-monitor.service
```

设置开机自启：

```bash
systemctl enable gmgn-twitter-monitor.service
```

查看状态与日志：

```bash
systemctl status gmgn-twitter-monitor.service --no-pager -l
journalctl -u gmgn-twitter-monitor.service -f -o cat
```

也可以使用控制台：

```bash
/root/.local/bin/uv run python ctl.py
```

`ctl.py` 在 root 用户下会直接调用 `systemctl` / `journalctl`，非 root 用户下会自动加 `sudo`。

## 5. 更新已有部署

如果服务器上正在运行旧版本，按下面步骤更新。`.env` 和 `browser_data/` 已被 `.gitignore` 排除，正常拉取代码不会覆盖你的配置和登录态。

```bash
cd /root/GmgnTwitterTgAlert

# 先停止正在运行的服务
systemctl stop gmgn-twitter-monitor.service

# 查看是否有本地改动
git status --short
```

如果 `git status --short` 没有输出，直接拉取：

```bash
git pull --ff-only
```

如果服务器上临时改过文件，先暂存再拉取：

```bash
git stash push -u -m "server local changes before update"
git pull --ff-only
```

更新依赖并刷新 systemd：

```bash
/root/.local/bin/uv pip install -r requirements.txt
systemctl daemon-reload
```

启动前做一次不启动服务的校验：

```bash
/root/.local/bin/uv run python -m compileall -q gmgn_twitter_monitor gmgn_twitter_monitor.py ctl.py test_socks.py tests
/root/.local/bin/uv run python -m unittest discover -s tests -v
```

确认 `.env` 中首次登录开关已经关闭，再启动服务：

```env
FIRST_RUN_LOGIN=False
```

```bash
systemctl start gmgn-twitter-monitor.service
journalctl -u gmgn-twitter-monitor.service -f -o cat
```

如果这次更新包含 `gmgn-twitter-monitor.service` 的路径或启动命令变更，建议重新注册服务文件：

```bash
install -m 0644 gmgn-twitter-monitor.service /etc/systemd/system/gmgn-twitter-monitor.service
systemctl daemon-reload
```

## 6. 可选 WARP 代理

默认情况下程序直连。只有当 `.env` 中设置 `PROXY_SERVER` 时，浏览器和 DeepSeek 请求才会走代理。

安装 Cloudflare WARP 本地代理：

```bash
cd /root/GmgnTwitterTgAlert
bash scripts/install_warp_proxy.sh
```

脚本会安装 Cloudflare WARP，将其配置为本地代理模式并测试：

```text
socks5://127.0.0.1:40000
```

启用代理：

```env
PROXY_SERVER=socks5://127.0.0.1:40000
```

测试当前 `.env` 中配置的代理：

```bash
/root/.local/bin/uv run python test_socks.py
```

Cloudflare 官方文档说明：Linux 初次连接需要 `warp-cli registration new` 和 `warp-cli connect`；本地代理模式只会代理显式配置为使用该本地 SOCKS/HTTPS 代理的应用。

## 7. 本地校验

不要在 Windows 开发机启动服务。只做语法和单元测试：

```bash
uv run python -m compileall -q gmgn_twitter_monitor gmgn_twitter_monitor.py ctl.py
uv run python -m unittest discover -s tests -v
```

## License

MIT
