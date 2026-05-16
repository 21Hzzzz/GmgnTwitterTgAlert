import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from gmgn_twitter_monitor.summary_store import SummaryStore


class SummaryStoreTests(unittest.TestCase):
    def test_insert_query_and_dedupe_by_internal_id(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            db_path = str(Path(tmpdir) / "summary.db")
            store = SummaryStore(db_path)
            store.init()

            message = {
                "internal_id": "internal-1",
                "tweet_id": "tweet-1",
                "timestamp": 1_700_000_000,
                "action": "tweet",
                "author": {"handle": "cz_binance"},
                "content": {"text": "important update"},
                "reference": {"text": "reference text"},
            }

            self.assertTrue(store.insert_message("AD", "-100ad", message, received_at=100))
            self.assertFalse(store.insert_message("AD", "-100ad", message, received_at=101))

            rows = store.fetch_messages("AD", "-100ad", 90, 110)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["internal_id"], "internal-1")
            self.assertEqual(rows[0]["content_text"], "important update")
            self.assertEqual(rows[0]["reference_text"], "reference text")

    def test_fetch_messages_by_id_range_and_max_id(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            db_path = str(Path(tmpdir) / "summary.db")
            store = SummaryStore(db_path)
            store.init()

            for index in range(3):
                store.insert_message(
                    "DEFAULT",
                    "-100default",
                    {
                        "internal_id": f"internal-{index}",
                        "tweet_id": f"tweet-{index}",
                        "timestamp": 1_700_000_000 + index,
                        "action": "tweet",
                        "author": {"handle": "cz_binance"},
                        "content": {"text": f"message {index}"},
                        "reference": None,
                    },
                    received_at=100 + index,
                )

            self.assertEqual(store.get_max_message_id("DEFAULT", "-100default"), 3)
            rows = store.fetch_messages_by_id_range("DEFAULT", "-100default", 1, 3)

            self.assertEqual([row["internal_id"] for row in rows], ["internal-1", "internal-2"])

    def test_record_run_upserts_status(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            db_path = str(Path(tmpdir) / "summary.db")
            store = SummaryStore(db_path)
            store.init()

            store.record_run(
                "MAIN",
                "-100main",
                last_run_at=200,
                window_start=100,
                window_end=200,
                status="empty",
                last_message_id=12,
            )
            store.record_run(
                "MAIN",
                "-100main",
                last_run_at=300,
                window_start=200,
                window_end=300,
                status="sent",
                message_id=456,
                last_message_id=34,
            )

            run = store.get_run("MAIN", "-100main")
            self.assertEqual(run["last_run_at"], 300)
            self.assertEqual(run["status"], "sent")
            self.assertEqual(run["message_id"], 456)
            self.assertEqual(run["last_message_id"], 34)
            self.assertEqual(store.get_last_message_id("MAIN", "-100main"), 34)

    def test_init_migrates_existing_summary_runs_table(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            db_path = str(Path(tmpdir) / "summary.db")

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE summary_runs (
                        group_key TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        last_run_at INTEGER NOT NULL,
                        window_start INTEGER,
                        window_end INTEGER,
                        status TEXT NOT NULL,
                        message_id INTEGER,
                        error TEXT,
                        PRIMARY KEY (group_key, chat_id)
                    )
                    """
                )
                conn.commit()

            store = SummaryStore(db_path)
            store.init()

            store.record_run(
                "DEFAULT",
                "-100default",
                last_run_at=100,
                window_start=50,
                window_end=100,
                status="empty",
            )
            self.assertEqual(store.get_last_message_id("DEFAULT", "-100default"), 0)


if __name__ == "__main__":
    unittest.main()
