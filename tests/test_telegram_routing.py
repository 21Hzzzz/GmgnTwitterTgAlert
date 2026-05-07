import unittest

from gmgn_twitter_monitor.distributor import TelegramDistributor


class TelegramRoutingTests(unittest.TestCase):
    def make_distributor(
        self,
        *,
        enable_default: bool = True,
        default_channel_id: str = "-100default",
        enable_main: bool = True,
        main_channel_id: str = "-100main",
        channel_map: dict[str, list[str]] | None = None,
        filter_handles: list[str] | None = None,
    ) -> TelegramDistributor:
        return TelegramDistributor(
            bot_token="token",
            default_channel_id=default_channel_id,
            enable_default=enable_default,
            main_channel_id=main_channel_id,
            enable_main=enable_main,
            channel_map=channel_map or {},
            filter_handles=filter_handles or [],
        )

    def test_unrouted_handle_goes_to_default_and_main(self):
        distributor = self.make_distributor(channel_map={"cz_binance": ["-100binance"]})

        self.assertEqual(
            distributor.resolve_target_channel_ids("elonmusk"),
            ["-100default", "-100main"],
        )

    def test_routed_handle_goes_to_default_and_route_group(self):
        distributor = self.make_distributor(channel_map={"cz_binance": ["-100binance"]})

        self.assertEqual(
            distributor.resolve_target_channel_ids("CZ_BINANCE"),
            ["-100default", "-100binance"],
        )

    def test_default_disabled_routes_to_main_or_route_group_only(self):
        distributor = self.make_distributor(
            enable_default=False,
            channel_map={"cz_binance": ["-100binance"]},
        )

        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), ["-100main"])
        self.assertEqual(
            distributor.resolve_target_channel_ids("cz_binance"),
            ["-100binance"],
        )

    def test_main_disabled_unrouted_handle_goes_to_default_only(self):
        distributor = self.make_distributor(
            enable_main=False,
            channel_map={"cz_binance": ["-100binance"]},
        )

        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), ["-100default"])
        self.assertEqual(
            distributor.resolve_target_channel_ids("cz_binance"),
            ["-100default", "-100binance"],
        )

    def test_duplicate_channel_ids_are_sent_once(self):
        distributor = self.make_distributor(
            default_channel_id="-100same",
            main_channel_id="-100same",
            channel_map={"cz_binance": ["-100same", "-100same", "-100other"]},
        )

        self.assertEqual(
            distributor.resolve_target_channel_ids("@cz_binance"),
            ["-100same", "-100other"],
        )
        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), ["-100same"])

    def test_filter_handles_blocks_non_allowlisted_handles(self):
        distributor = self.make_distributor(
            channel_map={"cz_binance": ["-100binance"]},
            filter_handles=["cz_binance"],
        )

        self.assertEqual(distributor.resolve_target_channel_ids("elonmusk"), [])
        self.assertEqual(
            distributor.resolve_target_channel_ids("cz_binance"),
            ["-100default", "-100binance"],
        )


class CapturingTelegramDistributor(TelegramDistributor):
    def __init__(self):
        super().__init__(
            bot_token="token",
            default_channel_id="-100default",
            enable_default=True,
            main_channel_id="-100main",
            enable_main=True,
        )
        self.last_endpoint = None
        self.last_payload = None

    async def _send_api(self, endpoint: str, payload: dict) -> dict | None:
        self.last_endpoint = endpoint
        self.last_payload = payload
        return {"ok": True}


class TelegramPreviewUrlTests(unittest.TestCase):
    def make_distributor(self) -> TelegramDistributor:
        return TelegramDistributor(
            bot_token="token",
            default_channel_id="-100default",
            enable_default=True,
            main_channel_id="-100main",
            enable_main=True,
            raw_preview_handles=["cz", "heyi"],
        )

    def test_raw_preview_handle_uses_content_media_url(self):
        distributor = self.make_distributor()
        message = {
            "tweet_id": "123",
            "content": {"media": [{"type": "photo", "url": "https://img.example/content.jpg"}]},
            "reference": {"media": [{"type": "photo", "url": "https://img.example/reference.jpg"}]},
        }

        self.assertEqual(
            distributor._resolve_preview_url(message, "CZ", "tweet"),
            "https://img.example/content.jpg",
        )

    def test_raw_preview_handle_uses_reference_media_when_content_has_none(self):
        distributor = self.make_distributor()
        message = {
            "tweet_id": "123",
            "content": {"media": []},
            "reference": {"media": [{"type": "video", "url": "https://img.example/reference.mp4"}]},
        }

        self.assertEqual(
            distributor._resolve_preview_url(message, "heyi", "quote"),
            "https://img.example/reference.mp4",
        )

    def test_normal_handle_still_uses_fxtwitter_preview(self):
        distributor = self.make_distributor()
        message = {
            "tweet_id": "123",
            "content": {"media": [{"type": "photo", "url": "https://img.example/content.jpg"}]},
            "reference": None,
        }

        self.assertEqual(
            distributor._resolve_preview_url(message, "elonmusk", "tweet"),
            "https://fxtwitter.com/elonmusk/status/123",
        )


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
