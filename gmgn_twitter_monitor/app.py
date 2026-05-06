import asyncio
import json
import os
import signal
import subprocess
import time

from loguru import logger
from playwright.async_api import async_playwright

try:
    from xvfbwrapper import Xvfb
except (ImportError, OSError) as e:
    Xvfb = None
    XVFB_IMPORT_ERROR = e
else:
    XVFB_IMPORT_ERROR = None

from . import config
from .browser import BrowserManager
from .distributor import (
    DistributorHub,
    LoggingDistributor,
    TelegramDistributor,
)
from .logging_setup import setup_logging
from .parser import build_standardized_message, extract_triggers_map, parse_socketio_payload
from .watchdog import Watchdog


# ---------------------------------------------------------------------------
#  cp 去重缓冲器：防止快照版(cp=0)和完整版(cp=1)重复推送
# ---------------------------------------------------------------------------
class MessageDeduplicator:
    """基于 internal_id 的消息去重器。

    策略：
    - cp=0 或无 cp 字段（快照版）→ 暂存，启动 TIMEOUT 超时定时器。
    - cp=1（完整版）→ 取消对应定时器，立即用完整版推送。
    - 已推送过的 internal_id → 全局过滤（记录最近 1000 条），防止任何情况下的重复。
    - 超时触发 → 用暂存的快照版推送（兜底）。
    """

    TIMEOUT = 0.5  # 500ms 等待完整版

    def __init__(self, publish_callback):
        self._publish = publish_callback
        self._pending: dict[str, tuple[dict, asyncio.TimerHandle]] = {}
        self._processed_ids: set[str] = set()
        self._history_queue: list[str] = []

    def _mark_processed(self, internal_id: str) -> None:
        if not internal_id:
            return
        if internal_id not in self._processed_ids:
            self._processed_ids.add(internal_id)
            self._history_queue.append(internal_id)
            if len(self._history_queue) > 1000:
                old_id = self._history_queue.pop(0)
                self._processed_ids.discard(old_id)

    def process(self, raw_item: dict) -> None:
        """处理一条原始 gmgn 数据项。"""
        internal_id = raw_item.get("i", "")
        if internal_id in self._processed_ids:
            return  # 已经成功推送过，忽略后续的重复消息

        cp = raw_item.get("cp")

        if cp == 1:
            # 完整版到达 → 取消定时器，用完整版推送
            if internal_id in self._pending:
                _, timer = self._pending.pop(internal_id)
                timer.cancel()
            self._mark_processed(internal_id)
            self._dispatch(raw_item)
            return

        # cp=0 或无 cp 字段（快照版）→ 暂存并设超时
        if internal_id and internal_id not in self._pending:
            loop = asyncio.get_event_loop()
            timer = loop.call_later(
                self.TIMEOUT,
                self._timeout_fallback,
                internal_id,
            )
            self._pending[internal_id] = (raw_item, timer)

    def _timeout_fallback(self, internal_id: str) -> None:
        """超时兜底：完整版没来，用快照版推送，保证不丢消息。"""
        if internal_id in self._pending:
            raw_item, _ = self._pending.pop(internal_id)
            logger.warning(f"⏱️ 去重等待完整版超时: {internal_id[:20]}... 使用快照兜底推送")
            self._mark_processed(internal_id)
            self._dispatch(raw_item)

    def _dispatch(self, raw_item: dict) -> None:
        """标准化并推送消息。"""
        try:
            message = build_standardized_message(raw_item)
            standardized_msg = message.to_dict()
            log_tag = f"[{message.action.upper()}]"
            summary_text = (
                f"{message.author.handle}: {message.content.text[:50]}..."
                if message.content.text
                else f"{message.author.handle} (无正文)"
            )
            if message.reference:
                summary_text += f" (REF: @{message.reference.author_handle})"

            logger.info(f"✨ 标准化推送 {log_tag} | {summary_text}")
            asyncio.create_task(self._publish(standardized_msg))
        except Exception as e:
            logger.error(f"❌ 数据标准化失败: {e}")


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------
def _cleanup_orphan_processes() -> None:
    """清理上次异常退出遗留的孤儿进程（Xvfb / Chromium）。"""
    current_user = os.environ.get("USER") or os.environ.get("USERNAME") or "root"
    for target in ("chromium", "Xvfb"):
        result = subprocess.run(
            ["pkill", "-u", current_user, "-f", target],
            capture_output=True,
        )
        killed = result.returncode == 0
        logger.info(f"清理孤儿 {target} 进程: {'✅ 已清理' if killed else '⬜ 无残留'}")


def _build_distributor_hub() -> DistributorHub:
    """根据 config 组装分发器集线器。"""
    distributors = [
        LoggingDistributor(),
        TelegramDistributor(
            bot_token=config.TG_BOT_TOKEN,
            main_channel_id=config.TG_MAIN_CHANNEL_ID,
            enable_main=config.TG_ENABLE_MAIN,
            channel_map=config.TG_CHANNEL_MAP,
            filter_handles=config.TG_FILTER_HANDLES,
        ),
    ]
    return DistributorHub(distributors)


async def main():
    setup_logging()
    if Xvfb is None:
        raise RuntimeError(
            "xvfbwrapper is required to run the monitor on Linux. "
            "Install dependencies with scripts/install_root_ubuntu.sh."
        ) from XVFB_IMPORT_ERROR

    _cleanup_orphan_processes()

    # 打印本次启动时间与 systemd 12h 后预计重启时间
    start_ts = time.time()
    next_restart_ts = start_ts + 43200  # 与 RuntimeMaxSec=43200 对应
    logger.info(
        f"🚀 服务启动 | 本次启动: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_ts))}"
        f" | 预计重启: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_restart_ts))}"
    )

    vdisplay = Xvfb(width=config.XVFB_WIDTH, height=config.XVFB_HEIGHT)
    vdisplay.start()

    # 注册 SIGTERM 处理器（systemd stop / kill 均会触发）
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(
        signal.SIGTERM,
        lambda: loop.call_soon_threadsafe(loop.stop),
    )

    browser = BrowserManager()
    watchdog = Watchdog(config.WATCHDOG_TIMEOUT)
    hub = _build_distributor_hub()
    deduplicator = MessageDeduplicator(hub.publish)
    connected_ws = set()

    try:
        await hub.start_all()

        async with async_playwright() as playwright:
            page = await browser.launch(playwright)

            def handle_ws_frame(frame_data):
                watchdog.feed()
                try:
                    parsed = parse_socketio_payload(frame_data)
                    if not parsed:
                        return

                    logger.info(f"📦 原始解析消息: {json.dumps(parsed, ensure_ascii=False)}")

                    triggers_map = extract_triggers_map(parsed["data"])
                    for item in parsed["data"]:
                        deduplicator.process(item)

                    if triggers_map:
                        logger.info(f"🎯 动作提取简报: {triggers_map}")
                except Exception as e:
                    logger.error(f"❌ 处理 WS 数据时发生错误: {e}")

            def on_web_socket(ws):
                if "gmgn.ai/ws" in ws.url:
                    if ws.url not in connected_ws:
                        connected_ws.add(ws.url)
                        logger.success("[WS 建立连接] 监听中...")

                    watchdog.feed()
                    ws.on("framereceived", lambda frame: handle_ws_frame(frame))
                    ws.on("close", lambda _: connected_ws.discard(ws.url))

            async def handle_http_response(response):
                """拦截 Socket.io HTTP 降级轮询响应，防止 WS 重连间隙漏消息。"""
                try:
                    if "gmgn.ai/ws" not in response.url or "transport=polling" not in response.url:
                        return
                    if response.status != 200:
                        return

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
                                watchdog.feed()
                                logger.info(f"📦 原始解析消息(Polling): {json.dumps(parsed, ensure_ascii=False)}")
                                triggers_map = extract_triggers_map(parsed["data"])
                                for item in parsed["data"]:
                                    deduplicator.process(item)
                                if triggers_map:
                                    logger.info(f"🎯 动作提取简报(Polling): {triggers_map}")

                        idx = msg_end
                except Exception as e:
                    logger.debug(f"Polling 响应解析跳过: {e}")

            page.on("websocket", on_web_socket)
            page.on("response", handle_http_response)

            await browser.run_first_login_if_needed()
            await browser.goto_monitor_page()
            await browser.handle_popups()
            await browser.switch_to_mine_tab()
            await browser.save_screenshot()

            logger.success(
                f"进入挂机监听模式... (已配置 {config.WATCHDOG_TIMEOUT}s 看门狗，按 Ctrl+C 终止)"
            )

            while True:
                await asyncio.sleep(config.WATCHDOG_POLL_INTERVAL)
                if watchdog.is_timed_out():
                    time_since_last_msg = watchdog.time_since_last_msg()
                    logger.warning(f"⚠️ 看门狗警报: {time_since_last_msg:.0f}秒内未收到任何WS消息，频道可能卡死断开！")
                    logger.info("尝试刷新整个网页结构...")
                    try:
                        await browser.recover_after_timeout()
                        watchdog.feed()
                    except Exception as e:
                        logger.error(f"刷新重连时发生异常: {e}")
    finally:
        await hub.stop_all()
        await browser.close()
        try:
            vdisplay.stop()
        except Exception as e:
            logger.warning(f"Xvfb 关闭时发生清理错误，已忽略: {e}")
