import asyncio
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gmgn_twitter_monitor import config
from gmgn_twitter_monitor.app import MessageDeduplicator, _build_distributor_hub, login_only
from gmgn_twitter_monitor.browser import BrowserManager
from gmgn_twitter_monitor.distributor import TelegramDistributor
from gmgn_twitter_monitor.storage import SQLiteStorage
from gmgn_twitter_monitor.summary_scheduler import DailySummaryScheduler


class _Message:
    action = "tweet"
    author = SimpleNamespace(handle="alice")
    content = SimpleNamespace(text="hello")
    reference = None
    tweet_id = "1"

    def to_dict(self):
        return {
            "action": "tweet",
            "tweet_id": "1",
            "timestamp": 1,
            "author": {"handle": "alice", "name": "Alice", "tags": []},
            "content": {"text": "hello", "media": []},
            "reference": None,
        }


class TelegramOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_navigation_does_not_wait_for_network_idle(self):
        class FakePage:
            def __init__(self):
                self.goto_calls = []

            async def goto(self, url, **kwargs):
                self.goto_calls.append((url, kwargs))

            async def wait_for_timeout(self, _milliseconds):
                return None

        browser = BrowserManager()
        browser.page = FakePage()
        browser.assert_logged_in = AsyncMock(return_value=True)
        await browser.run_login("https://gmgn.ai/tglogin?test=1")
        self.assertEqual(
            [kwargs["wait_until"] for _, kwargs in browser.page.goto_calls],
            ["domcontentloaded", "domcontentloaded"],
        )
        self.assertTrue(all(kwargs["timeout"] == 60000 for _, kwargs in browser.page.goto_calls))
        browser.assert_logged_in.assert_awaited_once_with(settle_ms=2000)

    async def test_login_verification_rejects_logged_out_page(self):
        class FakeLocator:
            def __init__(self):
                self.first = self

            async def is_visible(self, timeout=None):
                return True

        class FakePage:
            def __init__(self):
                self.screenshot_path = None

            def locator(self, _selector):
                return FakeLocator()

            async def wait_for_timeout(self, _milliseconds):
                return None

            async def screenshot(self, path):
                self.screenshot_path = path

        with tempfile.TemporaryDirectory() as tmp:
            failure_path = str(Path(tmp) / "login_failed.png")
            marker_path = str(Path(tmp) / ".login-required")
            browser = BrowserManager()
            browser.page = FakePage()
            with (
                patch.object(config, "LOGIN_FAILURE_SCREENSHOT", failure_path),
                patch.object(config, "LOGIN_REQUIRED_MARKER", marker_path),
            ):
                with self.assertRaisesRegex(RuntimeError, "仍显示未登录"):
                    await browser.assert_logged_in()

            self.assertEqual(browser.page.screenshot_path, failure_path)
            self.assertTrue(Path(marker_path).is_file())

    async def test_mine_tab_uses_dom_click_without_navigation_wait(self):
        class FakeLocator:
            def __init__(self):
                self.first = self
                self.dom_clicks = 0
                self.checked_attribute = None

            async def is_visible(self, timeout=None):
                return True

            async def evaluate(self, _expression):
                self.dom_clicks += 1

            async def get_attribute(self, name):
                self.checked_attribute = name
                return "true"

            async def click(self, **_kwargs):
                raise AssertionError("regular Playwright click must not be used")

        class FakePage:
            def __init__(self):
                self.tab = FakeLocator()

            def locator(self, _selector):
                return self.tab

            async def wait_for_timeout(self, _milliseconds):
                return None

        browser = BrowserManager()
        browser.page = FakePage()

        self.assertTrue(await browser.switch_to_mine_tab())
        self.assertEqual(browser.page.tab.dom_clicks, 1)
        self.assertEqual(browser.page.tab.checked_attribute, "aria-selected")

    async def test_all_group_is_always_added_to_routed_targets(self):
        telegram = TelegramDistributor(
            "token",
            "-100-all",
            enable_default=True,
            channel_map={"alice": ["-100-routed"]},
        )
        self.assertEqual(
            telegram._target_channel_ids("alice"),
            ["-100-all", "-100-routed"],
        )
        self.assertEqual(telegram._target_channel_ids("bob"), ["-100-all"])

    async def test_snapshot_then_complete_only_dispatches_telegram_targets(self):
        published = []

        async def publish(message):
            published.append(message["_dispatch_target"])

        dedup = MessageDeduplicator(publish)
        with patch("gmgn_twitter_monitor.app.build_standardized_message", return_value=_Message()):
            dedup.process({"i": "msg-1", "cp": 0, "u": {"s": "alice"}})
            dedup.process({"i": "msg-1", "cp": 1, "u": {"s": "alice"}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertEqual(published, ["TG_FAST", "TG_UPDATE"])

    async def test_snapshot_timeout_updates_same_telegram_message(self):
        published = []

        async def publish(message):
            published.append(message["_dispatch_target"])

        dedup = MessageDeduplicator(publish)
        dedup.TIMEOUT_UPDATE = 0.01
        with patch("gmgn_twitter_monitor.app.build_standardized_message", return_value=_Message()):
            dedup.process({"i": "msg-2", "cp": 0, "u": {"s": "alice"}})
            await asyncio.sleep(0.04)

        self.assertEqual(published, ["TG_FAST", "TG_UPDATE"])

    async def test_summary_sender_uses_telegram_distributor_only(self):
        telegram = TelegramDistributor("token", "", channel_map={"alice": ["-1001"]})
        sent = []

        async def send_summary(chat_id, text):
            sent.append((chat_id, text))
            return True

        telegram.send_summary = send_summary
        scheduler = DailySummaryScheduler(SimpleNamespace(), SimpleNamespace(distributors=[telegram]))
        result = await scheduler._send_summary(
            {"target_tg_channel_id": "-1001"},
            "summary",
        )
        self.assertTrue(result)
        self.assertEqual(sent, [("-1001", "summary")])

    async def test_login_only_rejects_missing_auth_url(self):
        with self.assertRaisesRegex(ValueError, "GMGN_AUTH_URL"):
            await login_only("")

    async def test_legacy_summary_schema_is_migrated_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE summary_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary_key TEXT NOT NULL,
                    source_platform TEXT NOT NULL,
                    source_target_id TEXT NOT NULL,
                    window_start INTEGER NOT NULL,
                    window_end INTEGER NOT NULL,
                    generated_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    tg_sent INTEGER NOT NULL DEFAULT 0,
                    feishu_sent INTEGER NOT NULL DEFAULT 0,
                    content TEXT,
                    error TEXT,
                    UNIQUE(summary_key, source_platform, source_target_id, window_start, window_end)
                );
                INSERT INTO summary_runs (
                    summary_key, source_platform, source_target_id,
                    window_start, window_end, generated_at, status,
                    item_count, tg_sent, feishu_sent, content, error
                ) VALUES ('MAIN', 'telegram', '-1001', 1, 2, 3, 'sent_all', 4, 1, 1, 'kept', '');
                """
            )
            conn.close()

            storage = SQLiteStorage(str(db_path))
            await storage.start()
            columns = await storage._fetchall("PRAGMA table_info(summary_runs)")
            rows = await storage._fetchall("SELECT * FROM summary_runs")
            await storage.close()

            self.assertNotIn("feishu_sent", {row["name"] for row in columns})
            self.assertEqual(rows[0]["content"], "kept")
            self.assertEqual(rows[0]["tg_sent"], 1)


class ConfigurationTests(unittest.TestCase):
    def test_dynamic_routes_only_build_telegram_maps(self):
        values = {
            "TG_ROUTING_UNITTEST": "Alice,Bob",
            "TG_ENABLE_UNITTEST": "True",
            "TG_CHANNEL_ID_UNITTEST": "-10042",
            "TG_TRACK_FILTER_UNITTEST": "A股,美股",
        }
        with patch.dict(os.environ, values, clear=False):
            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.TG_CHANNEL_MAP["alice"], ["-10042"])
            self.assertEqual(
                reloaded.TG_CHANNEL_TRACK_FILTER["bob"]["-10042"],
                ["A股", "美股"],
            )
            self.assertFalse(hasattr(reloaded, "FEISHU_CHANNEL_MAP"))
        importlib.reload(config)

    def test_all_group_does_not_restrict_global_handle_filter(self):
        values = {
            "TG_ENABLE_DEFAULT": "True",
            "TG_CHANNEL_ID": "-100-all",
            "TG_ROUTING_UNITTESTALL": "Alice",
            "TG_ENABLE_UNITTESTALL": "True",
            "TG_CHANNEL_ID_UNITTESTALL": "-100-routed",
            "TG_FILTER_HANDLES": "",
        }
        with patch.dict(os.environ, values, clear=False):
            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.TG_FILTER_HANDLES, [])
            self.assertEqual(reloaded.TG_CHANNEL_MAP["alice"], ["-100-routed"])
        importlib.reload(config)

    def test_runtime_hub_contains_only_telegram(self):
        hub = _build_distributor_hub()
        self.assertEqual(len(hub.distributors), 1)
        self.assertIsInstance(hub.distributors[0], TelegramDistributor)

    def test_installer_exposes_required_raw_actions(self):
        installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
        for action in ("reconfigure", "relogin", "uninstall", "--purge"):
            self.assertIn(action, installer)
        self.assertIn('default_group="ALL"', installer)
        self.assertNotIn("TG_ROUTING_ALL", installer)
        self.assertIn('PYTHONPATH="$release_path"', installer)
        self.assertIn('PYTHONPATH="$CURRENT_LINK"', installer)
        self.assertIn('LOGIN_MARKER="${STATE_DIR}/.login-complete"', installer)
        self.assertIn('LOGIN_REQUIRED_MARKER="${STATE_DIR}/.login-required"', installer)
        self.assertIn('READY_SCREENSHOT="${STATE_DIR}/monitor_running.png"', installer)
        self.assertIn("User=gmgn-monitor", (Path(__file__).parents[1] / "gmgn-twitter-monitor.service").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
