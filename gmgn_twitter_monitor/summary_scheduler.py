import asyncio
import time
from contextlib import suppress
from typing import Any

from loguru import logger

from .summarizer import DeepSeekSummarizer
from .summary_store import SummaryStore


class SummaryScheduler:
    """Periodic AI summary worker for Telegram targets."""

    CHECK_INTERVAL_SECONDS = 30

    def __init__(
        self,
        store: SummaryStore,
        targets: list[dict[str, str | int]],
        telegram_client: Any,
        summarizer: Any | None = None,
        *,
        started_at: int | None = None,
    ):
        self.store = store
        self.targets = targets
        self.telegram_client = telegram_client
        self.summarizer = summarizer or DeepSeekSummarizer()
        self._started_at = started_at if started_at is not None else int(time.time())
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not self.targets:
            return
        self.store.init()
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="ai-summary-scheduler")
        logger.success(f"AI 定时总结已启动，目标数: {len(self.targets)}")

    async def stop(self) -> None:
        self._stopping.set()
        if not self._task:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("AI 定时总结已停止")

    async def run_once(self, now: int | None = None) -> None:
        now = now if now is not None else int(time.time())
        for target in self.targets:
            try:
                await self._run_target_if_due(target, now)
            except Exception as e:
                logger.error(f"AI 定时总结任务异常: {target} - {repr(e)}")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.CHECK_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_target_if_due(self, target: dict[str, str | int], now: int) -> None:
        group_key = str(target["group_key"])
        chat_id = str(target["chat_id"])
        interval_minutes = int(target["interval_minutes"])
        interval_seconds = interval_minutes * 60

        last_run_at = self.store.get_last_run_at(group_key, chat_id)
        due_base = last_run_at if last_run_at is not None else self._started_at
        if now - due_base < interval_seconds:
            return

        window_start = now - interval_seconds
        messages = self.store.fetch_messages(group_key, chat_id, window_start, now)
        if not messages:
            self.store.record_run(
                group_key,
                chat_id,
                last_run_at=now,
                window_start=window_start,
                window_end=now,
                status="empty",
            )
            logger.info(f"AI 总结跳过: {group_key} -> {chat_id} 无新消息")
            return

        summary_text = await self.summarizer.summarize(messages, window_start, now)
        if not summary_text:
            self.store.record_run(
                group_key,
                chat_id,
                last_run_at=now,
                window_start=window_start,
                window_end=now,
                status="failed",
                error="summarizer returned no content",
            )
            return

        message_id = await self.telegram_client.send_summary_message(chat_id, summary_text)
        if not message_id:
            self.store.record_run(
                group_key,
                chat_id,
                last_run_at=now,
                window_start=window_start,
                window_end=now,
                status="failed",
                error="telegram sendMessage failed",
            )
            return

        pinned = await self.telegram_client.pin_message(chat_id, message_id)
        self.store.record_run(
            group_key,
            chat_id,
            last_run_at=now,
            window_start=window_start,
            window_end=now,
            status="sent" if pinned else "sent_pin_failed",
            message_id=message_id,
        )
        logger.info(f"AI 总结已发送并置顶: {group_key} -> {chat_id} message_id={message_id}")
