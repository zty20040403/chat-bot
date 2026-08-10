from __future__ import annotations

import asyncio
import json
import unittest

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
                return page, denied, allowed

        page, denied, allowed = asyncio.run(run())
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


if __name__ == "__main__":
    unittest.main()
