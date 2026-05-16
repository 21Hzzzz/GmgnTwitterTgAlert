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
            self.assertEqual(run["last_message_id"], 1)
            self.assertEqual(summarizer.calls[0][1:], (1_000, 1_000))

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
            run = store.get_run("MAIN", "-100main")
            self.assertEqual(run["status"], "empty")
            self.assertEqual(run["last_message_id"], 0)

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
            self.assertEqual(run["last_message_id"], 0)
            self.assertEqual(run["error"], "timeout after 120s")

    async def test_delayed_run_uses_id_cursor_without_dropping_old_messages(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            store = SummaryStore(str(Path(tmpdir) / "summary.db"))
            store.init()
            for index, received_at in enumerate((100, 500, 1_000), start=1):
                store.insert_message(
                    "DEFAULT",
                    "-100default",
                    {
                        "internal_id": f"m{index}",
                        "tweet_id": f"t{index}",
                        "timestamp": 1_700_000_000 + index,
                        "action": "tweet",
                        "author": {"handle": "elonmusk"},
                        "content": {"text": f"message {index}"},
                        "reference": None,
                    },
                    received_at=received_at,
                )
            telegram = FakeTelegramClient()
            summarizer = FakeSummarizer("<b>summary</b>")
            scheduler = SummaryScheduler(
                store,
                [{"group_key": "DEFAULT", "chat_id": "-100default", "interval_minutes": 15}],
                telegram,
                summarizer=summarizer,
                started_at=0,
            )

            await scheduler.run_once(now=1_200)

            sent_messages = summarizer.calls[0][0]
            self.assertEqual([row["internal_id"] for row in sent_messages], ["m1", "m2", "m3"])
            self.assertEqual(summarizer.calls[0][1:], (100, 1_000))
            self.assertEqual(store.get_run("DEFAULT", "-100default")["last_message_id"], 3)

    async def test_new_messages_after_cutoff_wait_for_next_successful_run(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            store = SummaryStore(str(Path(tmpdir) / "summary.db"))
            store.init()
            store.insert_message(
                "DEFAULT",
                "-100default",
                {
                    "internal_id": "m1",
                    "tweet_id": "t1",
                    "timestamp": 1_700_000_001,
                    "action": "tweet",
                    "author": {"handle": "elonmusk"},
                    "content": {"text": "message 1"},
                    "reference": None,
                },
                received_at=100,
            )
            telegram = FakeTelegramClient()
            summarizer = FakeSummarizer("<b>summary</b>")
            scheduler = SummaryScheduler(
                store,
                [{"group_key": "DEFAULT", "chat_id": "-100default", "interval_minutes": 15}],
                telegram,
                summarizer=summarizer,
                started_at=0,
            )

            original_pin_message = telegram.pin_message

            async def insert_new_message_then_pin(chat_id: str, message_id: int) -> bool:
                store.insert_message(
                    "DEFAULT",
                    "-100default",
                    {
                        "internal_id": "m2",
                        "tweet_id": "t2",
                        "timestamp": 1_700_000_002,
                        "action": "tweet",
                        "author": {"handle": "elonmusk"},
                        "content": {"text": "message 2"},
                        "reference": None,
                    },
                    received_at=1_100,
                )
                return await original_pin_message(chat_id, message_id)

            telegram.pin_message = insert_new_message_then_pin

            await scheduler.run_once(now=1_000)
            self.assertEqual(store.get_run("DEFAULT", "-100default")["last_message_id"], 1)

            await scheduler.run_once(now=1_900)
            self.assertEqual([row["internal_id"] for row in summarizer.calls[1][0]], ["m2"])
            self.assertEqual(store.get_run("DEFAULT", "-100default")["last_message_id"], 2)


if __name__ == "__main__":
    unittest.main()
