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


LOG_FILE = str(BASE_DIR / "twitter_monitor.log")
USER_DATA_DIR = str(BASE_DIR / "browser_data")
SCREENSHOT_PATH = str(BASE_DIR / "monitor_running.png")
MONITOR_URL = "https://gmgn.ai/follow?target=xTracker&chain=bsc"
PROXY_SERVER = os.getenv("PROXY_SERVER", "").strip()
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

for key, value in sorted(os.environ.items()):
    if not key.startswith("TG_ROUTING_") or not value:
        continue

    group_name = key[len("TG_ROUTING_"):]
    if not _env_bool(f"TG_ENABLE_{group_name}", True):
        continue

    channel_id = os.getenv(f"TG_CHANNEL_ID_{group_name}", "").strip()
    if not channel_id:
        continue

    for handle in _parse_handles(value):
        TG_CHANNEL_MAP.setdefault(handle, [])
        if channel_id not in TG_CHANNEL_MAP[handle]:
            TG_CHANNEL_MAP[handle].append(channel_id)

TG_FILTER_HANDLES = _parse_handles(os.getenv("TG_FILTER_HANDLES", ""))

# ---------- DeepSeek translation ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
