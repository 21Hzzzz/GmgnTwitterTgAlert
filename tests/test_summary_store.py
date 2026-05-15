import os
import tempfile
import unittest
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
            )
            store.record_run(
                "MAIN",
                "-100main",
                last_run_at=300,
                window_start=200,
                window_end=300,
                status="sent",
                message_id=456,
            )

            run = store.get_run("MAIN", "-100main")
            self.assertEqual(run["last_run_at"], 300)
            self.assertEqual(run["status"], "sent")
            self.assertEqual(run["message_id"], 456)


if __name__ == "__main__":
    unittest.main()
