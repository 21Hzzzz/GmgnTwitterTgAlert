import asyncio
import base64
import binascii
import hashlib
import json
import os
import signal
import subprocess
import time
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from loguru import logger
from playwright.async_api import async_playwright

try:
    from xvfbwrapper import Xvfb
except (ImportError, OSError):
    Xvfb = None

from . import config
from .browser import BrowserManager
from .distributor import (
    DistributorHub,
    TelegramDistributor,
)
from .logging_setup import setup_logging
from .parser import build_standardized_message, extract_triggers_map, parse_socketio_payload
from .storage import SQLiteStorage
from .summary_scheduler import DailySummaryScheduler
from .watchdog import Watchdog


# ---------------------------------------------------------------------------
#  cp 去重缓冲器：防止快照版(cp=0)和完整版(cp=1)重复推送
# ---------------------------------------------------------------------------
class MessageDeduplicator:
    """基于 internal_id 的消息去重器。

    策略：快照版立即推送，启动 5s 定时器；如果在 5s 内收到完整版，
    则触发 TG_UPDATE 编辑原 Telegram 消息。
    """

    TIMEOUT_UPDATE = 5.0  # 5s 等待 TG 的完整版更新

    def __init__(self, publish_callback):
        self._publish = publish_callback
        self._pending_update: dict[str, tuple[dict, asyncio.TimerHandle]] = {}
        self._processed_tg_ids: set[str] = set()
        self._history_queue: list[str] = []
        self._instagram_fingerprints_by_key: dict[str, list[str]] = {}
        # 关键：持有 asyncio.Task 引用，防止 GC 回收导致协程中途消失
        self._background_tasks: set[asyncio.Task] = set()

    def _mark_history(self, internal_id: str) -> None:
        if internal_id and internal_id not in self._history_queue:
            self._history_queue.append(internal_id)
            if len(self._history_queue) > 1000:
                old_id = self._history_queue.pop(0)
                self._processed_tg_ids.discard(old_id)
            if len(self._instagram_fingerprints_by_key) > 1000:
                self._instagram_fingerprints_by_key.pop(next(iter(self._instagram_fingerprints_by_key)), None)

    @staticmethod
    def _is_instagram_item(raw_item: dict) -> bool:
        tags = raw_item.get("ut") or []
        return raw_item.get("pf") == 4 and "instagram" in tags

    @staticmethod
    def _instagram_identity_key(raw_item: dict) -> str:
        u_data = raw_item.get("u") or {}
        handle = (u_data.get("s") or "").lower()
        action = raw_item.get("tw") or "unknown"
        source_id = raw_item.get("ti") or raw_item.get("i") or ""
        return f"ins:{handle}:{action}:{source_id}"

    @staticmethod
    def _stable_media_identity(url: str | None, allow_proxy_decode: bool = True) -> str:
        if not url:
            return ""

        try:
            parsed = urlsplit(url)
        except ValueError:
            return url

        query = parse_qs(parsed.query)
        ig_cache_key = query.get("ig_cache_key")
        if ig_cache_key:
            return f"ig_cache_key:{ig_cache_key[0]}"

        path = unquote(parsed.path.rstrip("/"))
        basename = path.rsplit("/", 1)[-1] if path else ""
        if allow_proxy_decode and basename:
            try:
                padded = basename + ("=" * (-len(basename) % 4))
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    return MessageDeduplicator._stable_media_identity(
                        decoded,
                        allow_proxy_decode=False,
                    )
            except (binascii.Error, ValueError, UnicodeDecodeError):
                pass

        if basename:
            return f"path:{basename}"
        return f"host:{parsed.netloc}"

    @staticmethod
    def _instagram_fingerprint(raw_item: dict) -> str:
        content = raw_item.get("c") if isinstance(raw_item.get("c"), dict) else {}
        media = content.get("m") if isinstance(content, dict) else []
        media_parts = []
        if isinstance(media, list):
            media_parts = [
                {
                    "type": item.get("t"),
                    "identity": MessageDeduplicator._stable_media_identity(item.get("u")),
                }
                for item in media
                if isinstance(item, dict)
            ]

        payload = {
            "ts": raw_item.get("ts") or "",
            "text": content.get("t") if isinstance(content, dict) else "",
            "media": media_parts,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha1(encoded).hexdigest()[:12]

    def _dedup_identity(self, raw_item: dict) -> str:
        internal_id = raw_item.get("i", "")
        if not internal_id:
            return ""
        if not self._is_instagram_item(raw_item):
            return internal_id

        identity_key = self._instagram_identity_key(raw_item)
        if raw_item.get("cp") != 1:
            return identity_key

        fingerprint = self._instagram_fingerprint(raw_item)
        seen = self._instagram_fingerprints_by_key.setdefault(identity_key, [])
        if not seen:
            seen.append(fingerprint)
            return identity_key
        if fingerprint == seen[0]:
            return identity_key
        if fingerprint in seen:
            return f"{identity_key}:{fingerprint}"

        seen.append(fingerprint)
        logger.warning(
            f"📸 Instagram 同一 GMGN ID 出现不同内容，拆分为独立推送: {identity_key}#{fingerprint}"
        )
        return f"{identity_key}:{fingerprint}"

    @staticmethod
    def _diag_handle(raw_item: dict) -> str:
        u_data = raw_item.get("u") or {}
        handle = (u_data.get("s") or "").lower()
        return handle if handle in config.DIAG_HANDLES else ""

    @staticmethod
    def _diag_item_summary(raw_item: dict) -> str:
        content = raw_item.get("c") if isinstance(raw_item.get("c"), dict) else {}
        reference = raw_item.get("sc") if isinstance(raw_item.get("sc"), dict) else {}
        ref_user = raw_item.get("su") if isinstance(raw_item.get("su"), dict) else {}
        return (
            f"action={raw_item.get('tw') or 'unknown'} cp={raw_item.get('cp')} "
            f"pf={raw_item.get('pf')} internal_id={raw_item.get('i') or ''} "
            f"tweet_id={raw_item.get('ti') or ''} ref=@{ref_user.get('s') or ''} "
            f"content_len={len(content.get('t') or '')} ref_len={len(reference.get('t') or '')}"
        )

    def process(self, raw_item: dict) -> None:
        """处理一条原始 gmgn 数据项。"""
        internal_id = self._dedup_identity(raw_item)
        if not internal_id:
            return

        cp = raw_item.get("cp")
        diag_handle = self._diag_handle(raw_item)
        if diag_handle:
            logger.info(f"🔎 诊断原始入站: @{diag_handle} {self._diag_item_summary(raw_item)}")

        # --- 1. TG 实时推送 & 5s 更新逻辑 ---
        if internal_id not in self._processed_tg_ids:
            self._processed_tg_ids.add(internal_id)
            self._mark_history(internal_id)
            if diag_handle:
                logger.info(f"🔎 诊断去重: @{diag_handle} 首次进入 TG_FAST dispatch_id={internal_id}")
            self._dispatch(raw_item, target="TG_FAST")

            if cp != 1:
                loop = asyncio.get_event_loop()
                timer = loop.call_later(
                    self.TIMEOUT_UPDATE,
                    self._timeout_update,
                    internal_id,
                )
                self._pending_update[internal_id] = (raw_item, timer)
                if diag_handle:
                    logger.info(f"🔎 诊断去重: @{diag_handle} 等待 cp=1 TG_UPDATE dispatch_id={internal_id}")
            else:
                # cp=1 直接到达：完整版已在手，立即触发 TG_UPDATE 进行翻译编辑
                if diag_handle:
                    logger.info(f"🔎 诊断去重: @{diag_handle} cp=1 直接进入 TG_UPDATE dispatch_id={internal_id}")
                self._dispatch(raw_item, target="TG_UPDATE")

        elif cp == 1 and internal_id in self._pending_update:
            _, timer = self._pending_update.pop(internal_id)
            timer.cancel()
            if diag_handle:
                logger.info(f"🔎 诊断去重: @{diag_handle} 收到 cp=1，触发 TG_UPDATE dispatch_id={internal_id}")
            self._dispatch(raw_item, target="TG_UPDATE")
        elif diag_handle:
            logger.info(
                f"🔎 诊断去重: @{diag_handle} TG 已处理过且无需更新 "
                f"cp={cp} dispatch_id={internal_id}"
            )

    def _timeout_update(self, internal_id: str) -> None:
        if internal_id in self._pending_update:
            raw_item, _ = self._pending_update.pop(internal_id)
            logger.info(f"⏱️ TG等待完整版更新超时(5s): {internal_id[:20]}... 使用快照更新TG")
            self._dispatch(raw_item, target="TG_UPDATE")

    def _dispatch(self, raw_item: dict, target: str) -> None:
        """标准化并推送消息。"""
        try:
            dispatch_id = self._dedup_identity(raw_item)
            message = build_standardized_message(raw_item)
            standardized_msg = message.to_dict()
            standardized_msg["_internal_id"] = dispatch_id
            standardized_msg["_source_internal_id"] = raw_item.get("i", "")
            standardized_msg["_dispatch_target"] = target
            standardized_msg["platform_flag"] = raw_item.get("pf")
            if self._is_instagram_item(raw_item):
                identity_key = self._instagram_identity_key(raw_item)
                standardized_msg["_instagram_source_id"] = raw_item.get("ti") or raw_item.get("i") or ""
                standardized_msg["_instagram_identity_fingerprint"] = self._instagram_fingerprint(raw_item)
                standardized_msg["_instagram_identity_collided"] = dispatch_id != identity_key

            log_tag = f"[{message.action.upper()}]"
            summary_text = (
                f"{message.author.handle}: {message.content.text[:50]}..."
                if message.content.text
                else f"{message.author.handle} (无正文)"
            )
            if message.reference:
                summary_text += f" (REF: @{message.reference.author_handle})"

            summary_text += _build_delay_string(raw_item.get("ts", 0))

            logger.info(f"✨ 标准化推送 ({target}) {log_tag} | {summary_text}")
            if (message.author.handle or "").lower() in config.DIAG_HANDLES:
                content_text = (standardized_msg.get("content") or {}).get("text") or ""
                ref_text = (standardized_msg.get("reference") or {}).get("text") or ""
                logger.info(
                    f"🔎 诊断标准化: @{message.author.handle} target={target} "
                    f"dispatch_id={dispatch_id} source_id={raw_item.get('i') or ''} "
                    f"action={message.action} tweet_id={message.tweet_id or ''} "
                    f"content_len={len(content_text)} ref_len={len(ref_text)}"
                )
            task = asyncio.create_task(self._publish(standardized_msg))
            self._background_tasks.add(task)

            def _done_callback(done_task: asyncio.Task) -> None:
                self._background_tasks.discard(done_task)
                if (message.author.handle or "").lower() not in config.DIAG_HANDLES:
                    return
                if done_task.cancelled():
                    logger.warning(
                        f"🔎 诊断发布任务被取消: @{message.author.handle} "
                        f"target={target} dispatch_id={dispatch_id}"
                    )
                    return
                exc = done_task.exception()
                if exc:
                    logger.error(
                        f"🔎 诊断发布任务异常: @{message.author.handle} "
                        f"target={target} dispatch_id={dispatch_id}: {exc}"
                    )
                else:
                    logger.info(
                        f"🔎 诊断发布任务完成: @{message.author.handle} "
                        f"target={target} dispatch_id={dispatch_id}"
                    )

            task.add_done_callback(_done_callback)
        except Exception as e:
            logger.error(f"❌ 数据标准化失败: {e}")


def _build_delay_string(raw_ts: Any) -> str:
    if not raw_ts:
        return ""
    try:
        ts_ms = int(raw_ts)
        is_ms_timestamp = ts_ms > 9_999_999_999
        ts_sec = ts_ms / 1000.0 if is_ms_timestamp else float(ts_ms)
        ms_part = ts_ms % 1000 if is_ms_timestamp else 0
        
        # 1. 源端时间
        ts_str = time.strftime('%H:%M:%S', time.localtime(ts_sec))
        if is_ms_timestamp:
            ts_str += f".{ms_part:03d}"
            
        # 2. 本机收到时间
        recv_time = time.time()
        recv_str = time.strftime('%H:%M:%S', time.localtime(recv_time))
        recv_ms_part = int((recv_time - int(recv_time)) * 1000)
        recv_str += f".{recv_ms_part:03d}"
            
        # 3. 延迟计算
        delay_ms = (recv_time - ts_sec) * 1000
        return f" [GMGN抓取发推时间: {ts_str} | 服务器收到时间: {recv_str} | 端到端耗时: {delay_ms:.0f}ms]"
    except (ValueError, TypeError):
        pass
    return ""

def _format_delay_info(parsed: dict) -> str:
    try:
        if "data" in parsed and isinstance(parsed["data"], list) and len(parsed["data"]) > 0:
            return _build_delay_string(parsed["data"][0].get("ts", 0))
    except Exception:
        pass
    return ""


def _is_gmgn_ws_url(url: str) -> bool:
    if "gmgn.ai" not in url:
        return False
    lowered = url.lower()
    return "/ws" in lowered or "socket.io" in lowered or "transport=websocket" in lowered


def _is_gmgn_polling_url(url: str) -> bool:
    lowered = url.lower()
    return "gmgn.ai" in lowered and "transport=polling" in lowered


def _format_ws_url_for_log(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _decode_ws_frame_text(frame_data: Any) -> str:
    if isinstance(frame_data, bytes):
        try:
            return frame_data.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return frame_data if isinstance(frame_data, str) else str(frame_data)


def _is_heartbeat_text(text: str) -> bool:
    if not text:
        return False
    if text in {"2", "3", "2probe", "3probe"}:
        return True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    return parsed.get("action") == "heartbeat" or parsed.get("channel") == "heartbeat"


def _is_heartbeat_frame(frame_data: Any) -> bool:
    return _is_heartbeat_text(_decode_ws_frame_text(frame_data))


def _extract_channel_hint(text: str) -> str:
    marker = '"channel"'
    idx = text.find(marker)
    if idx == -1:
        return ""
    colon_idx = text.find(":", idx + len(marker))
    if colon_idx == -1:
        return ""
    quote_start = text.find('"', colon_idx + 1)
    if quote_start == -1:
        return ""
    quote_end = text.find('"', quote_start + 1)
    if quote_end == -1:
        return ""
    return text[quote_start + 1:quote_end]


def _format_ws_frame_preview(frame_data: Any, limit: int = 200) -> str:
    if isinstance(frame_data, bytes):
        try:
            text = frame_data.decode("utf-8")
        except UnicodeDecodeError:
            text = frame_data.hex()
            return f"bytes(hex,len={len(frame_data)}): {text[:limit]}"
        return f"bytes(utf8,len={len(frame_data)}): {text[:limit]!r}"
    text = str(frame_data)
    suffix = "..." if len(text) > limit else ""
    return f"{type(frame_data).__name__}(len={len(text)}): {text[:limit]!r}{suffix}"


class WSFrameStats:
    def __init__(self, interval_seconds: int):
        self.interval_seconds = max(1, interval_seconds)
        self.window_start = time.time()
        self.total = 0
        self.sent = 0
        self.received = 0
        self.heartbeats = 0
        self.target = 0
        self.skipped = 0
        self.channels = Counter()

    def record(self, direction: str, text: str, kind: str) -> None:
        self.total += 1
        if direction == "sent":
            self.sent += 1
        else:
            self.received += 1

        if kind == "heartbeat":
            self.heartbeats += 1
        elif kind == "target":
            self.target += 1
        elif kind == "skipped":
            self.skipped += 1

        channel = _extract_channel_hint(text)
        if channel:
            self.channels[channel] += 1

        self.maybe_log()

    def maybe_log(self) -> None:
        now = time.time()
        if now - self.window_start < self.interval_seconds:
            return

        top_channels = ", ".join(
            f"{channel}:{count}" for channel, count in self.channels.most_common(5)
        ) or "none"
        logger.info(
            "📊 GMGN WS帧统计 "
            f"{int(now - self.window_start)}s | total={self.total} "
            f"sent={self.sent} received={self.received} "
            f"target={self.target} skipped={self.skipped} "
            f"heartbeat={self.heartbeats} top_channels=[{top_channels}]"
        )

        self.window_start = now
        self.total = 0
        self.sent = 0
        self.received = 0
        self.heartbeats = 0
        self.target = 0
        self.skipped = 0
        self.channels.clear()


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------
def _cleanup_orphan_processes() -> None:
    """清理上次异常退出遗留的孤儿进程（Xvfb / Chromium）。"""
    for target in ("chromium", "Xvfb"):
        result = subprocess.run(
            ["pkill", "-u", os.environ.get("USER") or str(os.getuid()), "-f", target],
            capture_output=True,
        )
        killed = result.returncode == 0
        logger.info(f"清理孤儿 {target} 进程: {'✅ 已清理' if killed else '⬜ 无残留'}")


def _create_virtual_display():
    if Xvfb is None:
        raise RuntimeError("Xvfb 仅支持 Linux；请确认已安装 xvfbwrapper 和 Xvfb。")
    return Xvfb(width=config.XVFB_WIDTH, height=config.XVFB_HEIGHT)


def _build_distributor_hub(storage: SQLiteStorage | None = None) -> DistributorHub:
    """组装仅包含 Telegram 下游的分发器集线器。"""
    distributors = [
        TelegramDistributor(
            bot_token=config.TG_BOT_TOKEN,
            default_channel_id=config.TG_CHANNEL_ID,
            enable_default=config.TG_ENABLE_DEFAULT,
            channel_map=config.TG_CHANNEL_MAP,
            filter_handles=config.TG_FILTER_HANDLES,
            storage=storage,
        ),
    ]
    return DistributorHub(distributors, storage=storage)


async def login_only(auth_url: str) -> None:
    """执行一次 GMGN 授权并在浏览器登录态落盘后退出。"""
    if not auth_url.strip():
        raise ValueError("GMGN_AUTH_URL 为空，无法执行首次授权")

    setup_logging()
    _cleanup_orphan_processes()
    vdisplay = _create_virtual_display()
    browser = BrowserManager()
    vdisplay.start()
    try:
        async with async_playwright() as playwright:
            await browser.launch(playwright)
            await browser.run_login(auth_url.strip())
    finally:
        await browser.close()
        vdisplay.stop()


async def main():
    setup_logging()
    _cleanup_orphan_processes()

    # 打印本次启动时间与 systemd 12h 后预计重启时间
    start_ts = time.time()
    next_restart_ts = start_ts + 43200  # 与 RuntimeMaxSec=43200 对应
    logger.info(
        f"🚀 服务启动 | 本次启动: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_ts))}"
        f" | 预计重启: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_restart_ts))}"
    )

    vdisplay = _create_virtual_display()
    vdisplay.start()

    # 注册停止信号处理器（systemd stop / kill 均会触发）
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def request_shutdown() -> None:
        if not shutdown_event.is_set():
            logger.info("收到停止信号，准备优雅退出...")
            shutdown_event.set()

    loop.add_signal_handler(signal.SIGTERM, request_shutdown)
    loop.add_signal_handler(signal.SIGINT, request_shutdown)

    browser = BrowserManager()
    watchdog = Watchdog(config.WATCHDOG_TIMEOUT)
    storage = SQLiteStorage(config.SUMMARY_DB_PATH)
    hub = _build_distributor_hub(storage)
    summary_scheduler = DailySummaryScheduler(storage, hub)
    deduplicator = MessageDeduplicator(hub.publish)
    connected_ws = set()
    watchdog_timeout_count = 0
    ignored_ws_log_count = 0
    heartbeat_count = 0
    last_heartbeat_log = 0.0
    raw_heartbeat_log_count = 0
    frame_stats = WSFrameStats(config.GMGN_WS_FRAME_STATS_INTERVAL)

    try:
        await storage.start()
        await hub.start_all()
        await summary_scheduler.start()

        async with async_playwright() as playwright:
            page = await browser.launch(playwright)

            def feed_upstream_activity(reason: str) -> None:
                nonlocal watchdog_timeout_count
                watchdog.feed()
                watchdog_timeout_count = 0

            def handle_ws_activity_frame(text: str, direction: str) -> bool:
                nonlocal heartbeat_count, last_heartbeat_log, raw_heartbeat_log_count
                feed_upstream_activity(f"ws_{direction}")
                if _is_heartbeat_text(text):
                    heartbeat_count += 1
                    frame_stats.record(direction, text, "heartbeat")
                    now = time.time()
                    if (
                        raw_heartbeat_log_count < 4
                        or now - last_heartbeat_log >= max(1, config.GMGN_HEARTBEAT_LOG_INTERVAL)
                    ):
                        raw_heartbeat_log_count += 1
                        last_heartbeat_log = now
                        preview = _format_ws_frame_preview(text)
                        logger.info(
                            f"💓 GMGN WS 心跳包 #{heartbeat_count} {direction}: {preview}"
                        )
                    return True
                return False

            def handle_ws_sent_frame(frame_data):
                text = _decode_ws_frame_text(frame_data)
                if handle_ws_activity_frame(text, "sent"):
                    return
                if config.GMGN_TARGET_CHANNEL not in text:
                    frame_stats.record("sent", text, "skipped")
                    return
                frame_stats.record("sent", text, "target")

            def handle_ws_frame(frame_data):
                text = _decode_ws_frame_text(frame_data)
                if handle_ws_activity_frame(text, "received"):
                    return
                feed_upstream_activity("ws_message")
                if config.GMGN_TARGET_CHANNEL not in text:
                    frame_stats.record("received", text, "skipped")
                    return

                frame_stats.record("received", text, "target")
                try:
                    parsed = parse_socketio_payload(text)
                    if not parsed:
                        return

                    delay_info = _format_delay_info(parsed)
                    logger.info(f"📦 原始解析消息: {json.dumps(parsed, ensure_ascii=False)}{delay_info}")

                    triggers_map = extract_triggers_map(parsed["data"])
                    for item in parsed["data"]:
                        deduplicator.process(item)

                    if triggers_map:
                        logger.info(f"🎯 动作提取简报: {triggers_map}")
                except Exception as e:
                    logger.error(f"❌ 处理 WS 数据时发生错误: {e}")

            def on_web_socket(ws):
                nonlocal ignored_ws_log_count, watchdog_timeout_count
                if _is_gmgn_ws_url(ws.url):
                    if ws.url not in connected_ws:
                        connected_ws.add(ws.url)
                        logger.success(f"[WS 建立连接] 监听中... {_format_ws_url_for_log(ws.url)}")

                    watchdog_timeout_count = 0
                    feed_upstream_activity("ws_connected")
                    ws.on("framesent", lambda frame: handle_ws_sent_frame(frame))
                    ws.on("framereceived", lambda frame: handle_ws_frame(frame))
                    ws.on("close", lambda _: connected_ws.discard(ws.url))
                elif "gmgn.ai" in ws.url and ignored_ws_log_count < 5:
                    ignored_ws_log_count += 1
                    logger.debug(f"忽略非监控 WS: {_format_ws_url_for_log(ws.url)}")

            async def handle_http_response(response):
                """拦截 Socket.io HTTP 降级轮询响应，防止 WS 重连间隙漏消息。"""
                try:
                    if not _is_gmgn_polling_url(response.url):
                        return
                    if response.status != 200:
                        logger.warning(f"GMGN Polling 响应异常 [{response.status}]: {response.url}")
                        return

                    feed_upstream_activity("polling_response")
                    text = await response.text()
                    if '42["message"' not in text:
                        return

                    # Engine.IO v4 Polling 格式: "长度:消息内容长度:消息内容..."
                    idx = 0
                    while idx < len(text):
                        colon_idx = text.find(':', idx)
                        if colon_idx == -1:
                            break
                        length_str = text[idx:colon_idx]
                        if not length_str.isdigit():
                            break
                        msg_len = int(length_str)
                        msg_start = colon_idx + 1
                        msg_end = msg_start + msg_len
                        if msg_end > len(text):
                            break
                        msg_content = text[msg_start:msg_end]

                        if msg_content.startswith('42'):
                            # 复用 parse_socketio_payload，确保与 WS 通道完全一致的
                            # 频道过滤 (twitter_user_monitor_basic) + 字符串反序列化
                            parsed = parse_socketio_payload(msg_content)
                            if parsed:
                                feed_upstream_activity("polling_message")
                                delay_info = _format_delay_info(parsed)
                                logger.info(f"📦 原始解析消息(Polling): {json.dumps(parsed, ensure_ascii=False)}{delay_info}")
                                triggers_map = extract_triggers_map(parsed["data"])
                                for item in parsed["data"]:
                                    deduplicator.process(item)
                                if triggers_map:
                                    logger.info(f"🎯 动作提取简报(Polling): {triggers_map}")

                        idx = msg_end
                except Exception as e:
                    logger.debug(f"Polling 响应解析跳过: {e}")

            def handle_request_failed(request):
                if "gmgn.ai" not in request.url:
                    return
                failure = request.failure or "unknown"
                if _is_gmgn_ws_url(request.url) or _is_gmgn_polling_url(request.url):
                    logger.warning(f"GMGN 上游连接请求失败: {_format_ws_url_for_log(request.url)} | {failure}")

            page.on("websocket", on_web_socket)
            page.on("response", handle_http_response)
            page.on("requestfailed", handle_request_failed)

            await browser.goto_monitor_page()
            await browser.handle_popups()
            await browser.assert_logged_in(settle_ms=1000)
            await browser.switch_to_mine_tab()
            await browser.save_screenshot()

            logger.success(
                f"进入挂机监听模式... (已配置 {config.WATCHDOG_TIMEOUT}s 看门狗，按 Ctrl+C 终止)"
            )

            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=config.WATCHDOG_POLL_INTERVAL,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
                if watchdog.is_timed_out():
                    time_since_last_msg = watchdog.time_since_last_msg()
                    watchdog_timeout_count += 1
                    if connected_ws:
                        logger.warning(
                            f"⚠️ 看门狗警报: {time_since_last_msg:.0f}秒内未收到上游 WS/Polling 活动，"
                            f"当前连接数: {len(connected_ws)}"
                        )
                    else:
                        logger.warning(
                            f"⚠️ 看门狗警报: {time_since_last_msg:.0f}秒内未建立上游 WS/Polling 连接"
                        )
                    force_goto = not connected_ws or watchdog_timeout_count >= 3
                    logger.info(f"尝试恢复网页结构... ({'完整导航' if force_goto else '普通刷新'})")
                    try:
                        await browser.recover_after_timeout(force_goto=force_goto)
                        watchdog.feed()
                    except Exception as e:
                        logger.error(f"刷新重连时发生异常: {e}")
    except Exception as e:
        if shutdown_event.is_set() and "Target page, context or browser has been closed" in str(e):
            logger.info("停止期间浏览器上下文已关闭，忽略 Playwright 关闭信号。")
        else:
            raise
    finally:
        await summary_scheduler.stop()
        await hub.stop_all()
        await storage.close()
        await browser.close()
        vdisplay.stop()
