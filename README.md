# GmgnTwitterTgAlert

基于 GMGN.ai 的 Twitter/KOL 实时监控转发工具。当前版本为 **Telegram-only**：程序通过 Playwright 浏览器监听 GMGN 页面数据，将标准化后的消息发送到默认全量群，并按 Twitter handle 路由到可选分组；未命中分组的消息可额外发送到主群组。

## 当前特性

- 捕获 GMGN 推送的发推、转推、回复、引用、关注/取关、删帖、换头像、改昵称、改简介、置顶/取消置顶等动作。
- 默认群组接收所有捕获到的消息。
- 分组路由按 Twitter handle 匹配；命中分组的消息发往默认群和对应分组，未命中分组的消息发往默认群和主群。
- Telegram 频道 ID 自动去重，避免同一目标收到重复消息。
- DeepSeek 翻译可选，配置 API key 后会在 Telegram 消息发送后追加中文译文。
- DeepSeek AI 定时总结可选，按 Telegram 目标群汇总过去一段时间内的消息并自动置顶摘要。
- WARP 代理可选，默认直连；只有配置 `PROXY_SERVER` 时才走代理。
- systemd 守护，默认 12 小时重启一次，降低长期浏览器运行带来的状态漂移。

## 一键部署

部署目标固定为 Ubuntu root，项目路径固定为 `/root/GmgnTwitterTgAlert`。

```bash
curl -fsSL https://raw.githubusercontent.com/21Hzzzz/GmgnTwitterTgAlert/main/scripts/install_root_ubuntu.sh | bash
```

脚本会自动完成：

- 安装基础 apt 包、`git`、`xvfb` 和 `uv`
- clone 或更新 `https://github.com/21Hzzzz/GmgnTwitterTgAlert`
- 创建/更新 Python 虚拟环境并安装依赖
- 安装 Playwright Chromium 和 Linux 运行依赖
- 注册 `gmgn-twitter-monitor.service`
- 安装 `/usr/local/bin/gta` 快捷命令
- 若 `.env` 不存在，从 `.env.example` 创建一份模板

脚本不会自动启动服务，也不会自动启用开机自启。`.env` 需要用户手动编辑。

## 配置 `.env`

```bash
nano /root/GmgnTwitterTgAlert/.env
```

核心配置示例：

```env
PROXY_SERVER=

TG_BOT_TOKEN=123456789:your-token

TG_ENABLE_DEFAULT=True
TG_CHANNEL_ID_DEFAULT=-1001234567890

TG_ENABLE_MAIN=True
TG_CHANNEL_ID_MAIN=-1001234567892

TG_ROUTING_AD=Unipioneer,798_eth,Reboottttttt
TG_ENABLE_AD=True
TG_CHANNEL_ID_AD=-1002490671103

TG_FILTER_HANDLES=
BINANCE_SQUARE_HANDLES=cz,heyi
DEEPSEEK_API_KEY=
DEEPSEEK_TRANSLATION_MODEL=deepseek-v4-flash
DEEPSEEK_SUMMARY_MODEL=deepseek-v4-pro
DEEPSEEK_TRANSLATION_PROMPT=
DEEPSEEK_SUMMARY_PROMPT=

AI_SUMMARY_ENABLED=False
AI_SUMMARY_DB_PATH=
AI_SUMMARY_INTERVAL_MINUTES=30
AI_SUMMARY_TIMEOUT_SECONDS=120
AI_SUMMARY_MAX_RETRIES=3

AI_SUMMARY_ENABLE_DEFAULT=False
AI_SUMMARY_INTERVAL_MINUTES_DEFAULT=

AI_SUMMARY_ENABLE_MAIN=False
AI_SUMMARY_INTERVAL_MINUTES_MAIN=

AI_SUMMARY_ENABLE_AD=False
AI_SUMMARY_INTERVAL_MINUTES_AD=
```

配置语义：

- `TG_ENABLE_DEFAULT=True` 且 `TG_CHANNEL_ID_DEFAULT` 有值时，默认群接收所有捕获消息。
- `TG_ENABLE_MAIN=True` 且 `TG_CHANNEL_ID_MAIN` 有值时，主群只接收未命中任何分组路由的消息。
- `TG_ROUTING_<GROUP>` 中的 handle 命中后，会额外发送到 `TG_CHANNEL_ID_<GROUP>`。
- 同一个 handle 可以放进多个分组。
- 如果默认群、主群、分组频道 ID 相同，程序只会发送一次。
- `TG_FILTER_HANDLES` 默认为空，表示不过滤；一旦填写，它就是全局白名单，未列入的 handle 不会发往任何 Telegram 目标。
- `BINANCE_SQUARE_HANDLES` 用于币安广场等非 Twitter 来源账号；这些账号无法通过 `fxtwitter.com` 生成预览时，程序会改用 GMGN 数据里的原图直链作为 Telegram 大图预览。
- `DEEPSEEK_TRANSLATION_MODEL` 默认 `deepseek-v4-flash`，用于实时翻译。
- `DEEPSEEK_SUMMARY_MODEL` 默认 `deepseek-v4-pro`，用于 AI 定时总结。
- `DEEPSEEK_TRANSLATION_PROMPT` 和 `DEEPSEEK_SUMMARY_PROMPT` 可覆盖默认提示词；留空使用内置默认值，换行可写成 `\n`。
- `AI_SUMMARY_ENABLED=True` 时启用 AI 定时总结框架，但每个目标群仍需单独开启 `AI_SUMMARY_ENABLE_<GROUP>=True`。
- `AI_SUMMARY_INTERVAL_MINUTES` 是默认总结窗口；`AI_SUMMARY_INTERVAL_MINUTES_<GROUP>` 可为单个目标群覆盖窗口。
- `AI_SUMMARY_TIMEOUT_SECONDS` 和 `AI_SUMMARY_MAX_RETRIES` 控制 DeepSeek 总结超时与重试；失败时不会推进总结窗口，下次会重试同一窗口。
- 摘要分组 key 为 `DEFAULT`、`MAIN` 或 `TG_ROUTING_<GROUP>` 的 `<GROUP>`，例如 `AI_SUMMARY_ENABLE_AD=True`。
- 摘要发送到对应 Telegram 目标群后会调用 `pinChatMessage` 静默置顶；bot 必须拥有置顶权限。
- `AI_SUMMARY_DB_PATH` 为空时默认使用项目根目录下的 `summary.db`。

## `gta` 快捷命令

```bash
gta start
gta stop
gta restart
gta status
gta update
```

- `gta start`：启动服务。每次都会询问是否首次登录；如果选择 `y`，粘贴 GMGN 授权 URL，程序会先写入浏览器登录缓存，然后再启动服务。
- `gta stop`：停止服务。
- `gta restart`：重启服务，并通过进程重启重新读取 `.env`。
- `gta status`：先显示 systemd 状态，再进入实时日志跟踪；按 `Ctrl+C` 退出。
- `gta update`：自动拉取最新代码、刷新依赖、刷新 systemd service 和 `gta` 命令；不会自动重启服务。

`gta update` 如果检测到 tracked 本地改动，会停止并提示你手动处理，避免覆盖服务器上的修改。

## 首次 GMGN 授权

首次部署或登录状态失效时：

1. 从 GMGN Bot 获取未过期授权链接。
2. 执行：

```bash
gta start
```

3. 看到提示后输入 `y`，粘贴授权 URL。
4. 等待首次登录流程完成，脚本会继续启动服务。
   - 如果 GMGN 弹出“谷歌身份验证”窗口，终端会继续提示输入 6 位动态验证码；输入后程序会自动填入并点击确认。

授权链接不再写入 `.env`，也不需要额外配置首次登录开关。

## 可选 WARP 代理

默认情况下程序直连。需要 Cloudflare WARP 本地代理时运行：

```bash
gta warp
```

安装完成后，在 `.env` 中启用：

```env
PROXY_SERVER=socks5://127.0.0.1:40000
```

也可以直接运行脚本：

```bash
bash /root/GmgnTwitterTgAlert/scripts/install_warp_proxy.sh
```

## 更新

```bash
gta update
gta restart
```

`gta update` 不会自动重启服务。确认 `.env` 和更新内容无误后，再执行 `gta restart`。

## 本地代码检查

本项目服务不建议在 Windows 开发机直接运行。只做语法和测试检查：

```bash
python -m compileall -q gmgn_twitter_monitor gmgn_twitter_monitor.py ctl.py test_socks.py tests
python -m unittest discover -s tests -v
bash -n scripts/install_root_ubuntu.sh scripts/install_warp_proxy.sh
```

## License

MIT
