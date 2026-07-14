import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("GMGN_ENV_FILE", str(BASE_DIR / ".env")))
load_dotenv(ENV_FILE)

STATE_DIR = Path(os.getenv("GMGN_STATE_DIR", str(BASE_DIR)))


def _int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


LOG_FILE = os.getenv("LOG_FILE", str(STATE_DIR / "twitter_monitor.log"))
USER_DATA_DIR = os.getenv("USER_DATA_DIR", str(STATE_DIR / "browser_data"))
SCREENSHOT_PATH = os.getenv("SCREENSHOT_PATH", str(STATE_DIR / "monitor_running.png"))
LOGIN_FAILURE_SCREENSHOT = os.getenv(
    "LOGIN_FAILURE_SCREENSHOT", str(STATE_DIR / "login_failed.png")
)
LOGIN_REQUIRED_MARKER = os.getenv(
    "LOGIN_REQUIRED_MARKER", str(STATE_DIR / ".login-required")
)
SUMMARY_DB_PATH = os.getenv("SUMMARY_DB_PATH", str(STATE_DIR / "twitter_monitor.db"))
MONITOR_URL = os.getenv("MONITOR_URL", "https://gmgn.ai/follow?target=xTracker&chain=bsc")
PROXY_SERVER = os.getenv("PROXY_SERVER", "socks5://127.0.0.1:40000")
WATCHDOG_TIMEOUT = _int_env("WATCHDOG_TIMEOUT", 120)
WATCHDOG_POLL_INTERVAL = _int_env("WATCHDOG_POLL_INTERVAL", 5)
XVFB_WIDTH = _int_env("XVFB_WIDTH", 1920)
XVFB_HEIGHT = _int_env("XVFB_HEIGHT", 1080)

# ---------- GMGN 上游 WebSocket 降噪 ----------
# 页面会订阅一些高频行情频道（例如 chain_stat），这些频道与 Twitter 监控无关，
# 但会让 Playwright 的 WS frame 回调非常繁忙。默认拦截这些订阅，并保留帧统计日志。
GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS = [
    ch.strip()
    for ch in os.getenv("GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS", "chain_stat").split(",")
    if ch.strip()
]
GMGN_WS_FRAME_STATS_INTERVAL = _int_env("GMGN_WS_FRAME_STATS_INTERVAL", 600)
GMGN_HEARTBEAT_LOG_INTERVAL = _int_env("GMGN_HEARTBEAT_LOG_INTERVAL", 600)
GMGN_TARGET_CHANNEL = os.getenv("GMGN_TARGET_CHANNEL", "twitter_user_monitor_basic")
DIAG_HANDLES = {
    h.strip().lower()
    for h in os.getenv("DIAG_HANDLES", "heyibinance,heyi,cz_binance,cz,elonmusk").split(",")
    if h.strip()
}

# ---------- Telegram 推送配置 ----------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_ENABLE_DEFAULT = os.getenv("TG_ENABLE_DEFAULT", "False").lower() in ("true", "1", "yes")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "")

# 动态解析路由分组
TG_CHANNEL_MAP: dict[str, list[str]] = {}
# TG 赛道过滤: {handle: {channel_id: [关键词...]}}，空列表表示不过滤
TG_CHANNEL_TRACK_FILTER: dict[str, dict[str, list[str]]] = {}
_routing_handles = set()

for k, v in os.environ.items():
    if k.startswith("TG_ROUTING_") and v:
        group_name = k[len("TG_ROUTING_"):]
        handles = [h.strip().lower() for h in v.split(",") if h.strip()]

        tg_enable_str = os.getenv(f"TG_ENABLE_{group_name}", "True").lower()
        if tg_enable_str in ("true", "1", "yes"):
            channel_id = os.getenv(f"TG_CHANNEL_ID_{group_name}")
            tg_track_raw = os.getenv(f"TG_TRACK_FILTER_{group_name}", "")
            tg_track_filter = [kw.strip() for kw in tg_track_raw.split(",") if kw.strip()]
            if channel_id:
                for h in handles:
                    if h not in TG_CHANNEL_MAP:
                        TG_CHANNEL_MAP[h] = []
                    if channel_id not in TG_CHANNEL_MAP[h]:
                        TG_CHANNEL_MAP[h].append(channel_id)
                    if tg_track_filter:
                        if h not in TG_CHANNEL_TRACK_FILTER:
                            TG_CHANNEL_TRACK_FILTER[h] = {}
                        TG_CHANNEL_TRACK_FILTER[h][channel_id] = tg_track_filter
                    _routing_handles.add(h)

TG_FILTER_HANDLES = [
    h.strip().lower()
    for h in os.getenv("TG_FILTER_HANDLES", "").split(",")
    if h.strip()
]
# 未启用 ALL 全量群时，自动将路由组 handles 并入全局监控白名单。
# ALL 启用时必须保持空白名单，才能接收所有上游消息。
if _routing_handles and not TG_ENABLE_DEFAULT:
    TG_FILTER_HANDLES = list(set(TG_FILTER_HANDLES) | _routing_handles)

# ---------- Binance Square 配置 ----------
BINANCE_SQUARE_HANDLES = [
    h.strip().lower()
    for h in os.getenv("BINANCE_SQUARE_HANDLES", "").split(",")
    if h.strip()
]

# Instagram 是上游来源类型，不是下游推送渠道。该开关仅控制其文本增强。
INSTAGRAM_TRANSLATION_ENABLE = os.getenv("INSTAGRAM_TRANSLATION_ENABLE", "False").lower() in ("true", "1", "yes")

# ---------- DeepSeek 翻译配置 ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# ---------- AI 分析（赛道分类 + 摘要 + 翻译）----------
AI_ANALYZE_HANDLES: set[str] = {
    h.strip().lower()
    for h in os.getenv("AI_ANALYZE_HANDLES", "").split(",")
    if h.strip()
}

# ---------- 定时频道总结配置 ----------
SUMMARY_ENABLE = os.getenv("SUMMARY_ENABLE", "False").lower() in ("true", "1", "yes")
SUMMARY_TIMEZONE = os.getenv("SUMMARY_TIMEZONE", "Asia/Shanghai")
SUMMARY_TIMES = [
    t.strip()
    for t in os.getenv("SUMMARY_TIMES", "07:30,20:00").split(",")
    if t.strip()
]
SUMMARY_GROUPS = [
    g.strip().upper()
    for g in os.getenv("SUMMARY_GROUPS", "BINANCE").split(",")
    if g.strip()
]
SUMMARY_MAX_TWEETS = int(os.getenv("SUMMARY_MAX_TWEETS", "120"))
SUMMARY_AI_TIMEOUT_SECONDS = int(os.getenv("SUMMARY_AI_TIMEOUT_SECONDS", "180"))
SUMMARY_TWEET_TEXT_LIMIT = int(os.getenv("SUMMARY_TWEET_TEXT_LIMIT", "500"))

SUMMARY_CHANNELS: list[dict] = []
for group_name in SUMMARY_GROUPS:
    source_channel_id = (
        os.getenv(f"SUMMARY_SOURCE_CHANNEL_ID_{group_name}")
        or os.getenv(f"TG_CHANNEL_ID_{group_name}", "")
    )
    target_tg_channel_id = (
        os.getenv(f"SUMMARY_TG_CHANNEL_ID_{group_name}")
        or source_channel_id
    )
    if source_channel_id:
        SUMMARY_CHANNELS.append({
            "key": group_name,
            "label": os.getenv(f"SUMMARY_LABEL_{group_name}", group_name),
            "source_platform": os.getenv(f"SUMMARY_SOURCE_PLATFORM_{group_name}", "telegram"),
            "source_target_id": source_channel_id,
            "target_tg_channel_id": target_tg_channel_id,
        })
