import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


FIRST_RUN_LOGIN = os.getenv("FIRST_RUN_LOGIN", "False").lower() in ("true", "1", "yes")
AUTH_URL = os.getenv("AUTH_URL", "")

LOG_FILE = str(BASE_DIR / "twitter_monitor.log")
USER_DATA_DIR = str(BASE_DIR / "browser_data")
SCREENSHOT_PATH = str(BASE_DIR / "monitor_running.png")
SUMMARY_DB_PATH = os.getenv("SUMMARY_DB_PATH", str(BASE_DIR / "twitter_monitor.db"))
MONITOR_URL = "https://gmgn.ai/follow?target=xTracker&chain=bsc"
PROXY_SERVER = os.getenv("PROXY_SERVER", "")
WATCHDOG_TIMEOUT = 120
WATCHDOG_POLL_INTERVAL = 5
XVFB_WIDTH = 1920
XVFB_HEIGHT = 1080

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
# 单频道模式不使用赛道路由；保留空映射以兼容 Telegram 格式化逻辑。
TG_CHANNEL_TRACK_FILTER: dict[str, dict[str, list[str]]] = {}

# ---------- Binance Square 内容识别 ----------
BINANCE_SQUARE_HANDLES = [
    h.strip().lower()
    for h in os.getenv("BINANCE_SQUARE_HANDLES", "cz,heyi,richardteng").split(",")
    if h.strip()
]
# ---------- 机器翻译（普通推文直译，无需 API Key）----------
# 与聚合端油管/Ins 相同：Google → Microsoft → 腾讯交互翻译
_TRANSLATE_DEFAULT = "google,microsoft,transmart"
TRANSLATE_PROVIDERS: tuple[str, ...] = tuple(
    p.strip().lower()
    for p in os.getenv("TRANSLATE_PROVIDERS", _TRANSLATE_DEFAULT).split(",")
    if p.strip()
) or ("google", "microsoft", "transmart")

# ---------- DeepSeek（仅 AI 分析账号 + 定时频道总结）----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ---------- AI 分析（赛道分类 + 摘要 + 翻译，如白毛股神 aleabitoreddit）----------
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
