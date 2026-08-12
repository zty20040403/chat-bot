from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins.ai_chat.config import Settings


class SettingsGroupFilterTests(unittest.TestCase):
    def test_proactive_defaults_use_interest_gate_and_frequent_voice(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.proactive_interest_threshold, 90)
        self.assertEqual(settings.proactive_voice_percent, 60)

    def test_zero_sandbox_file_limit_means_unlimited(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_SANDBOX_MAX_FILE_MB": "0"},
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.sandbox_max_file_bytes, 0)

    def test_media_defaults_use_luna_and_are_disabled_locally(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertFalse(settings.media_enabled)
        self.assertEqual(settings.vision_profile, "gpt-5.6-luna")
        self.assertEqual(settings.media_max_vision_bytes, 20 * 1024 * 1024)

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

    def test_group_model_profiles_parse_group_ids_and_profile_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AI_GROUP_MODEL_PROFILES_JSON": (
                    '{"201644592":"gpt-5.6-sol","930690526":"deepseek"}'
                )
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(
            settings.group_model_profiles,
            {201644592: "gpt-5.6-sol", 930690526: "deepseek"},
        )

    def test_group_model_profiles_reject_invalid_json(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_GROUP_MODEL_PROFILES_JSON": "not-json"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must be valid JSON"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
