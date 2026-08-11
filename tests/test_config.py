from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins.ai_chat.config import Settings


class SettingsGroupFilterTests(unittest.TestCase):
    def test_zero_sandbox_file_limit_means_unlimited(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_SANDBOX_MAX_FILE_MB": "0"},
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.sandbox_max_file_bytes, 0)

    def test_disabled_groups_override_allowlist(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AI_ENABLED_GROUPS": "100,200",
                "AI_DISABLED_GROUPS": "200,300",
            },
        ):
            settings = Settings.from_env()

        self.assertTrue(settings.is_group_enabled(100))
        self.assertFalse(settings.is_group_enabled(200))
        self.assertFalse(settings.is_group_enabled(300))
        self.assertFalse(settings.is_group_enabled(400))

    def test_disabled_groups_work_without_allowlist(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AI_ENABLED_GROUPS": "",
                "AI_DISABLED_GROUPS": "201644592",
            },
        ):
            settings = Settings.from_env()

        self.assertFalse(settings.is_group_enabled(201644592))
        self.assertTrue(settings.is_group_enabled(930690526))


if __name__ == "__main__":
    unittest.main()
