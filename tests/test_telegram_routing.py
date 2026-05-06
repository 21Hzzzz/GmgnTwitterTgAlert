import unittest

from gmgn_twitter_monitor.distributor import TelegramDistributor


class TelegramRoutingTests(unittest.TestCase):
    def make_distributor(
        self,
        *,
        enable_main: bool = True,
        main_channel_id: str = "-100main",
        channel_map: dict[str, list[str]] | None = None,
        filter_handles: list[str] | None = None,
    ) -> TelegramDistributor:
        return TelegramDistributor(
            bot_token="token",
            main_channel_id=main_channel_id,
            enable_main=enable_main,
            channel_map=channel_map or {},
            filter_handles=filter_handles or [],
        )

    def test_main_group_receives_unrouted_handle(self):
        distributor = self.make_distributor(channel_map={"cz_binance": ["-100binance"]})

        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), ["-100main"])

    def test_routed_handle_goes_to_main_and_route_group(self):
        distributor = self.make_distributor(channel_map={"cz_binance": ["-100binance"]})

        self.assertEqual(
            distributor.resolve_target_channel_ids("CZ_BINANCE"),
            ["-100main", "-100binance"],
        )

    def test_duplicate_channel_ids_are_sent_once(self):
        distributor = self.make_distributor(
            main_channel_id="-100same",
            channel_map={"cz_binance": ["-100same", "-100same", "-100other"]},
        )

        self.assertEqual(
            distributor.resolve_target_channel_ids("@cz_binance"),
            ["-100same", "-100other"],
        )

    def test_filter_handles_blocks_non_allowlisted_handles(self):
        distributor = self.make_distributor(
            channel_map={"cz_binance": ["-100binance"]},
            filter_handles=["cz_binance"],
        )

        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), [])
        self.assertEqual(
            distributor.resolve_target_channel_ids("cz_binance"),
            ["-100main", "-100binance"],
        )


class CapturingTelegramDistributor(TelegramDistributor):
    def __init__(self):
        super().__init__(
            bot_token="token",
            main_channel_id="-100main",
            enable_main=True,
        )
        self.last_endpoint = None
        self.last_payload = None

    async def _send_api(self, endpoint: str, payload: dict) -> dict | None:
        self.last_endpoint = endpoint
        self.last_payload = payload
        return {"ok": True}


class TelegramTranslationEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_edit_keeps_original_text_before_translation(self):
        distributor = CapturingTelegramDistributor()
        message = {
            "action": "tweet",
            "author": {"handle": "cz_binance", "name": "CZ", "followers": None},
            "content": {"text": "hello world", "media": []},
            "reference": None,
            "bio_change": None,
        }

        await distributor._translate_and_edit(
            123,
            "header without original text",
            "🕒 推文时间: 2026-05-06 21:00:00",
            message,
            {"content": "你好，世界"},
            "-100main",
        )

        self.assertEqual(distributor.last_endpoint, "editMessageText")
        edited_text = distributor.last_payload["text"]
        self.assertIn("hello world", edited_text)
        self.assertIn("—— 🇨🇳 中文翻译 ——", edited_text)
        self.assertIn("你好，世界", edited_text)
        self.assertLess(edited_text.index("hello world"), edited_text.index("你好，世界"))


if __name__ == "__main__":
    unittest.main()
