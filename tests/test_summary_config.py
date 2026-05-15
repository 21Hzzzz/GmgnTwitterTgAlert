import importlib
import os
import unittest
from unittest.mock import patch

import dotenv


class SummaryConfigTests(unittest.TestCase):
    def tearDown(self):
        import gmgn_twitter_monitor.config as config

        importlib.reload(config)

    def load_config(self, env: dict[str, str]):
        import gmgn_twitter_monitor.config as config

        with patch.dict(os.environ, env, clear=True), patch.object(
            dotenv,
            "load_dotenv",
            lambda *args, **kwargs: False,
        ):
            return importlib.reload(config)

    def test_summary_targets_use_group_enable_and_interval_override(self):
        config = self.load_config(
            {
                "TG_ENABLE_DEFAULT": "True",
                "TG_CHANNEL_ID_DEFAULT": "-100default",
                "TG_ENABLE_MAIN": "True",
                "TG_CHANNEL_ID_MAIN": "-100main",
                "TG_ROUTING_AD": "cz,heyi",
                "TG_ENABLE_AD": "True",
                "TG_CHANNEL_ID_AD": "-100ad",
                "AI_SUMMARY_ENABLED": "True",
                "AI_SUMMARY_INTERVAL_MINUTES": "30",
                "AI_SUMMARY_ENABLE_AD": "True",
                "AI_SUMMARY_INTERVAL_MINUTES_AD": "15",
                "AI_SUMMARY_ENABLE_MAIN": "True",
            }
        )

        self.assertEqual(
            config.TG_ROUTE_TARGETS_BY_HANDLE["cz"],
            [{"group_key": "AD", "chat_id": "-100ad"}],
        )
        self.assertEqual(
            config.AI_SUMMARY_TARGETS,
            [
                {"group_key": "AD", "chat_id": "-100ad", "interval_minutes": 15},
                {"group_key": "MAIN", "chat_id": "-100main", "interval_minutes": 30},
            ],
        )

    def test_summary_targets_dedupe_same_chat_id(self):
        config = self.load_config(
            {
                "TG_ENABLE_DEFAULT": "True",
                "TG_CHANNEL_ID_DEFAULT": "-100same",
                "TG_ROUTING_AD": "cz",
                "TG_ENABLE_AD": "True",
                "TG_CHANNEL_ID_AD": "-100same",
                "AI_SUMMARY_ENABLED": "True",
                "AI_SUMMARY_ENABLE_DEFAULT": "True",
                "AI_SUMMARY_ENABLE_AD": "True",
            }
        )

        self.assertEqual(
            config.AI_SUMMARY_TARGETS,
            [{"group_key": "DEFAULT", "chat_id": "-100same", "interval_minutes": 30}],
        )

    def test_global_summary_disable_clears_targets(self):
        config = self.load_config(
            {
                "TG_ENABLE_DEFAULT": "True",
                "TG_CHANNEL_ID_DEFAULT": "-100default",
                "AI_SUMMARY_ENABLED": "False",
                "AI_SUMMARY_ENABLE_DEFAULT": "True",
            }
        )

        self.assertEqual(config.AI_SUMMARY_TARGETS, [])

    def test_deepseek_model_defaults_and_overrides(self):
        config = self.load_config({})

        self.assertEqual(config.DEEPSEEK_TRANSLATION_MODEL, "deepseek-v4-flash")
        self.assertEqual(config.DEEPSEEK_SUMMARY_MODEL, "deepseek-v4-pro")

        config = self.load_config(
            {
                "DEEPSEEK_TRANSLATION_MODEL": "custom-translation",
                "DEEPSEEK_SUMMARY_MODEL": "custom-summary",
            }
        )

        self.assertEqual(config.DEEPSEEK_TRANSLATION_MODEL, "custom-translation")
        self.assertEqual(config.DEEPSEEK_SUMMARY_MODEL, "custom-summary")

    def test_deepseek_prompt_defaults_and_overrides(self):
        config = self.load_config({})

        self.assertIn("推文翻译器", config.DEEPSEEK_TRANSLATION_PROMPT)
        self.assertIn("加密市场信息流分析助手", config.DEEPSEEK_SUMMARY_PROMPT)

        config = self.load_config(
            {
                "DEEPSEEK_TRANSLATION_PROMPT": "line1\\nline2",
                "DEEPSEEK_SUMMARY_PROMPT": "summary\\nprompt",
            }
        )

        self.assertEqual(config.DEEPSEEK_TRANSLATION_PROMPT, "line1\nline2")
        self.assertEqual(config.DEEPSEEK_SUMMARY_PROMPT, "summary\nprompt")


if __name__ == "__main__":
    unittest.main()
