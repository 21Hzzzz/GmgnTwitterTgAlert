import os
import tempfile
import unittest
from pathlib import Path

from gmgn_twitter_monitor.summary_scheduler import SummaryScheduler
from gmgn_twitter_monitor.summary_store import SummaryStore


class FakeSummarizer:
    def __init__(self, text: str | None, last_error: str | None = None):
        self.text = text
        self.last_error = last_error
        self.calls = []

    async def summarize(self, messages, window_start, window_end):
        self.calls.append((messages, window_start, window_end))
        return self.text


class FakeTelegramClient:
    def __init__(self):
        self.sent = []
        self.pinned = []

    async def send_summary_message(self, chat_id: str, text: str) -> int | None:
        self.sent.append((chat_id, text))
        return 123

    async def pin_message(self, chat_id: str, message_id: int) -> bool:
        self.pinned.append((chat_id, message_id))
        return True


class SummarySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_target_sends_and_pins_summary(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            store = SummaryStore(str(Path(tmpdir) / "summary.db"))
            store.init()
            store.insert_message(
                "AD",
                "-100ad",
                {
                    "internal_id": "m1",
                    "tweet_id": "t1",
                    "timestamp": 1_700_000_000,
                    "action": "tweet",
                    "author": {"handle": "cz_binance"},
                    "content": {"text": "alpha"},
                    "reference": None,
                },
                received_at=1_000,
            )
            telegram = FakeTelegramClient()
            summarizer = FakeSummarizer("<b>summary</b>")
            scheduler = SummaryScheduler(
                store,
                [{"group_key": "AD", "chat_id": "-100ad", "interval_minutes": 30}],
                telegram,
                summarizer=summarizer,
                started_at=0,
            )

            await scheduler.run_once(now=1_800)

            self.assertEqual(len(summarizer.calls), 1)
            self.assertEqual(telegram.sent, [("-100ad", "<b>summary</b>")])
            self.assertEqual(telegram.pinned, [("-100ad", 123)])
            run = store.get_run("AD", "-100ad")
            self.assertEqual(run["status"], "sent")
            self.assertEqual(run["last_run_at"], 1_800)

    async def test_empty_window_records_empty_without_sending(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            store = SummaryStore(str(Path(tmpdir) / "summary.db"))
            store.init()
            telegram = FakeTelegramClient()
            summarizer = FakeSummarizer("<b>summary</b>")
            scheduler = SummaryScheduler(
                store,
                [{"group_key": "MAIN", "chat_id": "-100main", "interval_minutes": 30}],
                telegram,
                summarizer=summarizer,
                started_at=0,
            )

            await scheduler.run_once(now=1_800)

            self.assertEqual(summarizer.calls, [])
            self.assertEqual(telegram.sent, [])
            self.assertEqual(store.get_run("MAIN", "-100main")["status"], "empty")

    async def test_summarizer_failure_records_failed_without_sending(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            store = SummaryStore(str(Path(tmpdir) / "summary.db"))
            store.init()
            store.insert_message(
                "MAIN",
                "-100main",
                {
                    "internal_id": "m1",
                    "tweet_id": "t1",
                    "timestamp": 1_700_000_000,
                    "action": "tweet",
                    "author": {"handle": "elonmusk"},
                    "content": {"text": "alpha"},
                    "reference": None,
                },
                received_at=1_000,
            )
            telegram = FakeTelegramClient()
            scheduler = SummaryScheduler(
                store,
                [{"group_key": "MAIN", "chat_id": "-100main", "interval_minutes": 30}],
                telegram,
                summarizer=FakeSummarizer(None, last_error="timeout after 120s"),
                started_at=0,
            )

            await scheduler.run_once(now=1_800)

            self.assertEqual(telegram.sent, [])
            run = store.get_run("MAIN", "-100main")
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["last_run_at"], 0)
            self.assertEqual(run["error"], "timeout after 120s")


if __name__ == "__main__":
    unittest.main()
