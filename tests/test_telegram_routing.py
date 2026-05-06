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


if __name__ == "__main__":
    unittest.main()
