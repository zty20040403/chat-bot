from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins import ai_chat
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.model_preferences import ModelPreferenceStore


class ModelSelectionTests(unittest.TestCase):
    def test_preferences_are_profile_names_and_remain_conversation_scoped(self) -> None:
        catalog = ModelCatalog.from_json(
            json.dumps(
                {
                    "default": "fast",
                    "profiles": {
                        "fast": {
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "api_key_required": False,
                            "aliases": ["ds"],
                        },
                        "strong": {
                            "provider": "anthropic",
                            "protocol": "anthropic-messages",
                            "model": "claude-test",
                            "api_key_required": False,
                        },
                    },
                }
            ),
            default_profile="fast",
            environ={},
        )
        with TemporaryDirectory() as tmp:
            preferences = ModelPreferenceStore(Path(tmp) / "models.json")
            preferences.set("group:1:user:10", "strong")
            with (
                patch.object(ai_chat, "model_profiles", catalog),
                patch.object(ai_chat, "model_preferences", preferences),
            ):
                selected = ai_chat._preferred_model_profile("group:1:user:10")
                neighbor = ai_chat._preferred_model_profile("group:1:user:11")

        self.assertEqual(selected.name, "strong")
        self.assertEqual(selected.protocol, "anthropic-messages")
        self.assertEqual(neighbor.name, "fast")

    def test_removed_profile_preference_falls_back_without_exposing_secret(self) -> None:
        catalog = ModelCatalog.from_json(
            json.dumps(
                {
                    "main": {
                        "provider": "openai",
                        "model": "gpt-test",
                        "api_key_env": "MODEL_TEST_KEY",
                    }
                }
            ),
            default_profile="main",
            environ={"MODEL_TEST_KEY": "private-key"},
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text('{"private:10":"deleted"}', encoding="utf-8")
            preferences = ModelPreferenceStore(path)
            with (
                patch.object(ai_chat, "model_profiles", catalog),
                patch.object(ai_chat, "model_preferences", preferences),
            ):
                selected = ai_chat._preferred_model_profile("private:10")

        self.assertEqual(selected.name, "main")
        self.assertNotIn("private-key", repr(selected))


if __name__ == "__main__":
    unittest.main()
