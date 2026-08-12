from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx
import nonebot
from fastapi import FastAPI

nonebot.init()

from src.plugins.ai_chat.admin import AdminServices, register_admin
from src.plugins.ai_chat.delivery import DeliveryStore
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.quota import UsageStore


class EmptyTasks:
    def list_all(self):
        return []

    def cancel_any(self, _task_id):
        return None


class BackgroundTasks:
    def running(self):
        return ("delivery",)

    def failures(self):
        return {"historian": "upstream timeout"}


class SandboxManager:
    async def admin_snapshot(self):
        return {
            "items": [
                {
                    "sandbox_id": "s123abc",
                    "owner": "group:930690526:user:3526452465",
                    "activities": [
                        {
                            "command": "python main.py",
                            "elapsed_seconds": 2,
                        }
                    ],
                }
            ],
            "active_commands": 1,
        }


class ModelPreferences:
    def items(self):
        return [("group:930690526:user:3526452465", "main")]


class MessageLedger:
    def list_scopes(self):
        return [
            SimpleNamespace(
                platform="onebot-v11",
                kind="group",
                native_conversation_id="930690526",
            )
        ]


class Settings:
    enabled_groups = {930690526}
    disabled_groups = {201644592}
    group_model_profiles = {930690526: "main"}

    @staticmethod
    def is_group_enabled(group_id):
        return group_id != 201644592


class AdminTests(unittest.TestCase):
    def test_dashboard_api_requires_token_and_returns_runtime_state(self) -> None:
        deliveries = DeliveryStore(":memory:")
        usage = UsageStore(":memory:")
        models = ModelCatalog.from_json(
            json.dumps(
                {
                    "main": {
                        "provider": "test",
                        "model": "model-a",
                        "api_key_env": "TEST_MODEL_KEY",
                    }
                }
            ),
            default_profile="main",
            environ={"TEST_MODEL_KEY": "never-return-this-secret"},
        )
        self.addCleanup(deliveries.close)
        self.addCleanup(usage.close)
        app = FastAPI()
        register_admin(
            app,
            AdminServices(
                version="test",
                started_at=1,
                delivery_store=deliveries,
                usage_store=usage,
                running_tasks=EmptyTasks(),
                background_tasks=BackgroundTasks(),
                model_catalog=models,
                model_preferences=ModelPreferences(),
                message_ledger=MessageLedger(),
                settings=Settings(),
                sandbox_manager=SandboxManager(),
                sticker_inventory=lambda: {
                    "counts": {"total": 1, "learned_images": 1},
                    "items": [
                        {
                            "inventory_id": "learned-1",
                            "source": "learned",
                            "kind": "image",
                            "name": "sticker.png",
                            "reference": "https://example.test/sticker.png",
                            "size_bytes": None,
                        }
                    ],
                },
            ),
            token="secret",
        )

        async def run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                page = await client.get("/bot-admin")
                denied = await client.get("/bot-admin/api/overview")
                allowed = await client.get(
                    "/bot-admin/api/overview",
                    headers={"Authorization": "Bearer secret"},
                )
                sandboxes = await client.get(
                    "/bot-admin/api/sandboxes",
                    headers={"Authorization": "Bearer secret"},
                )
                stickers = await client.get(
                    "/bot-admin/api/stickers",
                    headers={"Authorization": "Bearer secret"},
                )
                group_models = await client.get(
                    "/bot-admin/api/group-models",
                    headers={"Authorization": "Bearer secret"},
                )
                return page, denied, allowed, sandboxes, stickers, group_models

        page, denied, allowed, sandboxes, stickers, group_models = asyncio.run(
            run()
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn("QQ Bot", page.text)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["version"], "test")
        self.assertEqual(
            allowed.json()["background_tasks"],
            {
                "running": ["delivery"],
                "failures": {"historian": "upstream timeout"},
            },
        )
        self.assertEqual(allowed.json()["models"]["default"], "main")
        self.assertTrue(allowed.json()["models"]["profiles"][0]["configured"])
        self.assertNotIn("never-return-this-secret", allowed.text)
        self.assertIn("data-view=\"sandboxes\"", page.text)
        self.assertIn("class=\"sidebar\"", page.text)
        self.assertIn("id=\"usage-chart\"", page.text)
        self.assertIn("QQ Bot Control", page.text)
        self.assertNotIn("__DASHBOARD_", page.text)
        self.assertNotIn("__ADMIN_PREFIX__", page.text)
        self.assertNotIn("__TOKEN_REQUIRED__", page.text)
        self.assertNotIn("unpkg.com", page.text)
        self.assertEqual(sandboxes.json()["active_commands"], 1)
        self.assertEqual(
            sandboxes.json()["items"][0]["agent_tasks"],
            [],
        )
        self.assertEqual(stickers.json()["counts"]["total"], 1)
        group_rows = group_models.json()["items"]
        self.assertEqual(
            [row["group_id"] for row in group_rows],
            [201644592, 930690526],
        )
        self.assertFalse(group_rows[0]["enabled"])
        self.assertTrue(group_rows[1]["group_override"])
        self.assertEqual(group_rows[1]["overrides"][0]["profile"], "main")
        self.assertNotIn("never-return-this-secret", group_models.text)


if __name__ == "__main__":
    unittest.main()
