import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _parse_handles(raw: str) -> list[str]:
    return [
        handle.strip().lower().lstrip("@")
        for handle in raw.split(",")
        if handle.strip()
    ]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _env_multiline(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.replace("\\n", "\n")


DEFAULT_DEEPSEEK_TRANSLATION_PROMPT = (
    "你是推文翻译器。用户会输入一段 JSON，包含多个字段（如 content, reference 等）。\n"
    "请将其中所有的英文或其它外语推文翻译为简体中文，并以严格的 JSON 格式返回，保持原有键名不变。\n"
    "规则：\n"
    "1. 只输出翻译结果，不要解释，绝对不要添加任何 markdown 代码块（如 ```json）。\n"
    "2. 保留原文中的 @用户名、$代币符号、URL 链接和 emoji 不翻译。\n"
    "3. 如果某段文本已经是中文，或者只是短标点符号（如 `!`、`?` 等），则原样保留它的内容。\n"
    "4. 返回结果必须是合法的 JSON 对象。"
)

DEFAULT_DEEPSEEK_SUMMARY_PROMPT = (
    "你是加密市场信息流分析助手。你会收到一组来自 GMGN/X 的监控消息 JSON。\n"
    "无论输入消息是中文、英文还是其它语言，输出内容必须全部使用简体中文；保留 @用户名、$代币符号、URL 和项目名。\n"
    "你的主要目标不是尽可能多地提取信息，而是尽可能忽略无用信息，同时不要漏掉可能重要的信号。\n"
    "应过滤日常闲聊、表情互动、无上下文短回复、重复转推、纯玩笑和低信息量观点。\n"
    "交易所、钱包、公链、项目方、监管/政府账号等官方推特发布的公告、上线、下架、活动、规则变化、风险提示和产品更新都应视为重点。\n"
    "KOL 或高影响力账号提到代币、项目、链上异动、交易机会、风险事件、市场结构变化时，也应保留。\n"
    "如果某条信息不确定是否重要，放入 watchlist，不要直接丢弃。\n"
    "请只返回合法 JSON，不要 markdown，不要解释，不要输出英文总结。\n"
    "返回结构必须为："
    '{"important":[{"title":"...","reason":"...","source_ids":[1],"confidence":"high|medium|low"}],'
    '"watchlist":[{"title":"...","reason":"...","source_ids":[1]}],'
    '"noise_summary":"...","stats":{"useful_count":0,"noise_count":0}}。\n'
    "important 放明确重要的信息；watchlist 放可能重要但证据不足的信息；"
    "noise_summary 简短说明被过滤的闲聊类型。source_ids 必须引用输入消息里的 id。"
)


LOG_FILE = str(BASE_DIR / "twitter_monitor.log")
USER_DATA_DIR = str(BASE_DIR / "browser_data")
SCREENSHOT_PATH = str(BASE_DIR / "monitor_running.png")
FIRST_LOGIN_SCREENSHOT_PATH = str(BASE_DIR / "first_login_after_auth.png")
MONITOR_URL = "https://gmgn.ai/follow?target=xTracker&chain=bsc"
PROXY_SERVER = os.getenv("PROXY_SERVER", "").strip()
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "zh-CN").strip() or "zh-CN"
BROWSER_ACCEPT_LANGUAGE = (
    os.getenv("BROWSER_ACCEPT_LANGUAGE", f"{BROWSER_LOCALE},zh;q=0.9,en;q=0.8").strip()
    or f"{BROWSER_LOCALE},zh;q=0.9,en;q=0.8"
)
WATCHDOG_TIMEOUT = 120
WATCHDOG_POLL_INTERVAL = 5
XVFB_WIDTH = 1920
XVFB_HEIGHT = 1080

# ---------- Telegram delivery ----------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_ENABLE_DEFAULT = _env_bool("TG_ENABLE_DEFAULT")
TG_CHANNEL_ID_DEFAULT = os.getenv("TG_CHANNEL_ID_DEFAULT", "").strip()
TG_ENABLE_MAIN = _env_bool("TG_ENABLE_MAIN")
TG_CHANNEL_ID_MAIN = os.getenv("TG_CHANNEL_ID_MAIN", "").strip()

# Dynamic route groups. Handles are not auto-added to TG_FILTER_HANDLES; the
# filter is an explicit global allowlist only.
TG_CHANNEL_MAP: dict[str, list[str]] = {}
TG_ROUTE_TARGETS_BY_HANDLE: dict[str, list[dict[str, str]]] = {}
TG_ROUTE_GROUP_CHANNELS: dict[str, str] = {}

for key, value in sorted(os.environ.items()):
    if not key.startswith("TG_ROUTING_") or not value:
        continue

    group_name = key[len("TG_ROUTING_"):]
    if not _env_bool(f"TG_ENABLE_{group_name}", True):
        continue

    channel_id = os.getenv(f"TG_CHANNEL_ID_{group_name}", "").strip()
    if not channel_id:
        continue

    TG_ROUTE_GROUP_CHANNELS[group_name] = channel_id
    for handle in _parse_handles(value):
        TG_CHANNEL_MAP.setdefault(handle, [])
        if channel_id not in TG_CHANNEL_MAP[handle]:
            TG_CHANNEL_MAP[handle].append(channel_id)
        TG_ROUTE_TARGETS_BY_HANDLE.setdefault(handle, [])
        route_target = {"group_key": group_name, "chat_id": channel_id}
        if route_target not in TG_ROUTE_TARGETS_BY_HANDLE[handle]:
            TG_ROUTE_TARGETS_BY_HANDLE[handle].append(route_target)

TG_FILTER_HANDLES = _parse_handles(os.getenv("TG_FILTER_HANDLES", ""))

# ---------- Non-Twitter preview handling ----------
BINANCE_SQUARE_HANDLES = _parse_handles(os.getenv("BINANCE_SQUARE_HANDLES", ""))

# ---------- DeepSeek translation ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_TRANSLATION_MODEL = os.getenv("DEEPSEEK_TRANSLATION_MODEL", "").strip() or "deepseek-v4-flash"
DEEPSEEK_SUMMARY_MODEL = os.getenv("DEEPSEEK_SUMMARY_MODEL", "").strip() or "deepseek-v4-pro"
DEEPSEEK_TRANSLATION_PROMPT = _env_multiline(
    "DEEPSEEK_TRANSLATION_PROMPT",
    DEFAULT_DEEPSEEK_TRANSLATION_PROMPT,
)
DEEPSEEK_SUMMARY_PROMPT = _env_multiline(
    "DEEPSEEK_SUMMARY_PROMPT",
    DEFAULT_DEEPSEEK_SUMMARY_PROMPT,
)

# ---------- AI scheduled summaries ----------
AI_SUMMARY_ENABLED = _env_bool("AI_SUMMARY_ENABLED")
AI_SUMMARY_DB_PATH = os.getenv("AI_SUMMARY_DB_PATH", "").strip() or str(BASE_DIR / "summary.db")
AI_SUMMARY_INTERVAL_MINUTES = _env_int("AI_SUMMARY_INTERVAL_MINUTES", 30)
AI_SUMMARY_TIMEOUT_SECONDS = _env_int("AI_SUMMARY_TIMEOUT_SECONDS", 120)
AI_SUMMARY_MAX_RETRIES = _env_int("AI_SUMMARY_MAX_RETRIES", 3)


def _summary_group_enabled(group_key: str) -> bool:
    return AI_SUMMARY_ENABLED and _env_bool(f"AI_SUMMARY_ENABLE_{group_key}", False)


def _summary_group_interval(group_key: str) -> int:
    return _env_int(
        f"AI_SUMMARY_INTERVAL_MINUTES_{group_key}",
        AI_SUMMARY_INTERVAL_MINUTES,
    )


def _build_summary_targets() -> list[dict[str, str | int]]:
    if not AI_SUMMARY_ENABLED:
        return []

    targets: list[dict[str, str | int]] = []
    seen_chat_ids: set[str] = set()

    def append_target(group_key: str, chat_id: str) -> None:
        if not chat_id or not _summary_group_enabled(group_key):
            return
        if chat_id in seen_chat_ids:
            return
        seen_chat_ids.add(chat_id)
        targets.append(
            {
                "group_key": group_key,
                "chat_id": chat_id,
                "interval_minutes": _summary_group_interval(group_key),
            }
        )

    if TG_ENABLE_DEFAULT:
        append_target("DEFAULT", TG_CHANNEL_ID_DEFAULT)

    for group_key, chat_id in TG_ROUTE_GROUP_CHANNELS.items():
        append_target(group_key, chat_id)

    if TG_ENABLE_MAIN:
        append_target("MAIN", TG_CHANNEL_ID_MAIN)

    return targets


AI_SUMMARY_TARGETS = _build_summary_targets()
