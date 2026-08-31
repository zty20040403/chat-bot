from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins import ai_chat
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.model_preferences import ModelPreferenceStore


class ModelSelectionTests(unittest.TestCase):
    def test_preference_stores_can_use_independent_postgres_namespaces(
        self,
    ) -> None:
        module = importlib.import_module(
            "src.plugins.ai_chat.model_preferences"
        )
        path = Path("reasoning.json")
        state = SimpleNamespace(load=lambda: None, save=lambda payload: None)
        with patch.object(module, "open_json_state", return_value=state) as opener:
            ModelPreferenceStore(path, namespace="reasoning_preferences")
        opener.assert_called_once_with(path, "reasoning_preferences")

    def test_group_enabled_override_is_persistent(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            preferences = ModelPreferenceStore(path)
            self.assertIsNone(preferences.get_group_enabled_override(1))
            preferences.set_group_enabled(1, False)
            self.assertFalse(preferences.get_group_enabled_override(1))
            self.assertFalse(
                ModelPreferenceStore(path).get_group_enabled_override(1)
            )
            preferences.set_group_enabled(1, True)
            self.assertTrue(preferences.get_group_enabled_override(1))

    def test_user_preference_overrides_group_default_and_remains_scoped(
        self,
    ) -> None:
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
            preferences.set("group:1:user:10", "fast")
            with (
                patch.object(ai_chat, "model_profiles", catalog),
                patch.object(ai_chat, "model_preferences", preferences),
                patch.object(
                    ai_chat,
                    "settings",
                    SimpleNamespace(group_model_profiles={1: "strong"}),
                ),
            ):
                selected = ai_chat._preferred_model_profile("group:1:user:10")
                neighbor = ai_chat._preferred_model_profile("group:1:user:11")
                other_group = ai_chat._preferred_model_profile("group:2:user:11")

        self.assertEqual(selected.name, "fast")
        self.assertEqual(selected.protocol, "openai-chat")
        self.assertEqual(neighbor.name, "strong")
        self.assertEqual(other_group.name, "fast")

    def test_dynamic_group_default_overrides_deployment_default(self) -> None:
        catalog = ModelCatalog.from_json(
            json.dumps(
                {
                    "default": "fast",
                    "profiles": {
                        "fast": {
                            "provider": "deepseek",
                            "model": "fast-model",
                            "api_key_required": False,
                        },
                        "strong": {
                            "provider": "openai",
                            "model": "strong-model",
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
            preferences.set_group_default(1, "strong")
            with (
                patch.object(ai_chat, "model_profiles", catalog),
                patch.object(ai_chat, "model_preferences", preferences),
                patch.object(
                    ai_chat,
                    "settings",
                    SimpleNamespace(group_model_profiles={1: "fast"}),
                ),
            ):
                selected = ai_chat._preferred_model_profile("group:1:user:10")
                preferences.clear_group_default(1)
                inherited = ai_chat._preferred_model_profile("group:1:user:10")

        self.assertEqual(selected.name, "strong")
        self.assertEqual(inherited.name, "fast")

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

    def test_invalid_reasoning_preference_is_cleared_instead_of_crashing(
        self,
    ) -> None:
        catalog = ModelCatalog.from_json(
            json.dumps(
                {
                    "main": {
                        "provider": "cliproxy",
                        "model": "gpt-test",
                        "api_key_required": False,
                    }
                }
            ),
            default_profile="main",
            environ={},
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = ModelPreferenceStore(root / "models.json")
            reasoning = ModelPreferenceStore(root / "reasoning.json")
            reasoning.set("private:10", "gpt-5.6-luna")
            with (
                patch.object(ai_chat, "model_profiles", catalog),
                patch.object(ai_chat, "model_preferences", models),
                patch.object(ai_chat, "reasoning_preferences", reasoning),
            ):
                selected = ai_chat._preferred_model_profile("private:10")

        self.assertEqual(selected.name, "main")
        self.assertEqual(selected.reasoning_effort, "")
        self.assertIsNone(reasoning.get_explicit("private:10"))


if __name__ == "__main__":
    unittest.main()
