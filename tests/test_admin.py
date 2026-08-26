from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx
import nonebot
from fastapi import FastAPI

nonebot.init()

from src.plugins.ai_chat.admin import AdminEventBroker, AdminServices, register_admin
from src.plugins.ai_chat.delivery import DeliveryStore
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.quota import UsageStore
from src.plugins.ai_chat.tool_policy import configure_tool_overrides


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
    def __init__(self):
        self.models = {"group:930690526:user:3526452465": "main"}

    def items(self):
        return sorted(self.models.items())

    def get_group_default(self, group_id):
        return self.models.get(f"group:{group_id}:default")

    def set_group_default(self, group_id, profile):
        self.models[f"group:{group_id}:default"] = profile

    def clear_group_default(self, group_id):
        return self.clear(f"group:{group_id}:default")

    def get_group_enabled_override(self, group_id):
        stored = self.models.get(f"group:{group_id}:enabled")
        if stored == "enabled":
            return True
        if stored == "disabled":
            return False
        return None

    def set_group_enabled(self, group_id, enabled):
        self.models[f"group:{group_id}:enabled"] = (
            "enabled" if enabled else "disabled"
        )

    def get_group_vision_auto_describe_override(self, group_id):
        stored = self.models.get(f"group:{group_id}:vision-auto-describe")
        if stored == "enabled":
            return True
        if stored == "disabled":
            return False
        return None

    def set_group_vision_auto_describe(self, group_id, enabled):
        self.models[f"group:{group_id}:vision-auto-describe"] = (
            "enabled" if enabled else "disabled"
        )

    def set(self, conversation_id, profile):
        self.models[conversation_id] = profile

    def clear(self, conversation_id):
        return self.models.pop(conversation_id, None) is not None


class UserProfiles:
    def group_ids(self):
        return (930690526,)

    def members(self, group_id):
        if group_id != 930690526:
            return []
        return [
            {
                "user_id": 3526452465,
                "display_name": "Kenneth",
                "nickname": "Kenneth",
                "card": "",
                "last_seen": 200,
            },
            {
                "user_id": 2291939848,
                "display_name": "群友",
                "nickname": "群友",
                "card": "",
                "last_seen": 100,
            },
        ]


class MessageLedger:
    def list_scopes(self):
        return [
            SimpleNamespace(
                platform="onebot-v11",
                kind="group",
                native_conversation_id="930690526",
            )
        ]


class TurnJournal:
    def recent_context_plans(self, limit=100):
        del limit
        return [
            {
                "turn_handle": "t#3",
                "scope_key": "onebot-v11:group:930690526",
                "current_message_id": 12,
                "focus_message_id": 10,
                "confidence": 0.87,
                "reason_codes": ["recent_question", "same_scope"],
                "related_message_ids": [11],
                "candidates": [{"message_id": 10, "score": 89.0}],
                "resolver_version": "reference-rules-v1",
                "context_hash": "abc123",
                "objective": "你觉得呢",
                "status": "succeeded",
                "model": "model-a",
                "profile": "main",
                "created_at": 123,
            }
        ]

    def recent_trace_summaries(self, limit=50):
        del limit
        return [
            {
                "trace_id": "a" * 32,
                "turn_id": 3,
                "turn_handle": "t#3",
                "status": "succeeded",
                "provider": "test",
                "model": "model-a",
                "profile": "main",
                "started_at": 123,
                "finished_at": 125,
                "duration_seconds": 2,
                "tool_call_count": 1,
                "tool_failures": 0,
                "tools": ["web_search"],
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "model_routes": [],
                "fallback": False,
            }
        ]


class Telemetry:
    def admin_snapshot(self, **_kwargs):
        return {
            "generated_at": 123,
            "window": "process",
            "totals": {"turns": 1, "model_requests": 1},
            "models": [],
            "tools": [],
            "deliveries": [],
            "stages": [],
            "fallback_routes": [],
        }


class Settings:
    enabled_groups = {930690526}
    disabled_groups = {201644592}
    group_model_profiles = {930690526: "main"}
    admin_user_ids = {3526452465}
    vision_auto_describe = True
    prometheus_url = ""
    alertmanager_url = ""

    @staticmethod
    def is_group_enabled(group_id):
        return group_id != 201644592


class Database:
    def topology_snapshot(self):
        return {
            "available": True,
            "overall": "healthy",
            "checked_at": 123,
            "writable_node": "h610",
            "nodes": [
                {
                    "name": "h610",
                    "host": "100.64.0.3",
                    "port": 55432,
                    "status": "online",
                    "role": "primary",
                    "writable": True,
                    "latency_ms": 4.2,
                    "database_size_bytes": 1024,
                    "replication_lag_seconds": 0,
                    "replication_lag_bytes": 0,
                    "server_version": "17.5",
                    "error": None,
                },
                {
                    "name": "tank",
                    "host": "100.64.0.4",
                    "port": 55432,
                    "status": "online",
                    "role": "secondary",
                    "writable": False,
                    "latency_ms": 8.1,
                    "database_size_bytes": 1024,
                    "replication_lag_seconds": 0,
                    "replication_lag_bytes": 0,
                    "server_version": "17.5",
                    "error": None,
                },
            ],
            "pool": {
                "size": 2,
                "available": 1,
                "waiting": 0,
                "min_size": 1,
                "max_size": 10,
            },
        }


class AdminTests(unittest.TestCase):
    def test_admin_event_broker_coalesces_resource_names(self) -> None:
        broker = AdminEventBroker()
        queue = broker.subscribe()
        broker.publish("groups", "overview", "groups")
        event = queue.get_nowait()
        broker.unsubscribe(queue)

        self.assertEqual(event["type"], "resources.changed")
        self.assertEqual(event["sequence"], 1)
        self.assertEqual(event["resources"], ["groups", "overview"])

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
                user_profiles=UserProfiles(),
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
                media_library=SimpleNamespace(
                    admin_snapshot=lambda limit=100: {
                        "counts": {"total": 1, "bytes": 123},
                        "items": [{"media_id": 1, "summary": "测试图片"}],
                        "jobs": [],
                        "vision_profile": "gpt-5.6-luna",
                        "root": "/var/lib/qq-deepseek-bot/media",
                    }
                ),
                source_store=SimpleNamespace(
                    admin_snapshot=lambda limit=100: {
                        "counts": {"total": 1, "ready": 1, "failed": 0},
                        "platforms": [{"platform": "bilibili", "total": 1}],
                        "items": [
                            {
                                "source_id": 3,
                                "platform": "bilibili",
                                "content_kind": "video",
                                "title": "测试视频",
                                "status": "ready",
                            }
                        ],
                    }
                ),
                turn_journal=TurnJournal(),
                database=Database(),
                telemetry=Telemetry(),
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
                favicon = await client.get("/bot-admin/favicon.svg")
                denied = await client.get("/bot-admin/api/overview")
                allowed = await client.get(
                    "/bot-admin/api/overview",
                    headers={"Authorization": "Bearer secret"},
                )
                v1_allowed = await client.get(
                    "/bot-admin/api/v1/overview",
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
                media = await client.get(
                    "/bot-admin/api/media",
                    headers={"Authorization": "Bearer secret"},
                )
                sources = await client.get(
                    "/bot-admin/api/sources",
                    headers={"Authorization": "Bearer secret"},
                )
                context_plans = await client.get(
                    "/bot-admin/api/context-plans",
                    headers={"Authorization": "Bearer secret"},
                )
                databases = await client.get(
                    "/bot-admin/api/databases",
                    headers={"Authorization": "Bearer secret"},
                )
                observability = await client.get(
                    "/bot-admin/api/observability",
                    headers={"Authorization": "Bearer secret"},
                )
                return (
                    page,
                    favicon,
                    denied,
                    allowed,
                    v1_allowed,
                    sandboxes,
                    stickers,
                    group_models,
                    media,
                    sources,
                    context_plans,
                    databases,
                    observability,
                )

        (
            page,
            favicon,
            denied,
            allowed,
            v1_allowed,
            sandboxes,
            stickers,
            group_models,
            media,
            sources,
            context_plans,
            databases,
            observability,
        ) = asyncio.run(run())
        self.assertEqual(page.status_code, 200)
        self.assertIn("window.__KENNETHBOT_ADMIN__", page.text)
        self.assertIn('"apiBase":"/bot-admin/api/v1"', page.text)
        self.assertIn('id="root"', page.text)
        self.assertEqual(favicon.status_code, 200)
        self.assertTrue(favicon.headers["content-type"].startswith("image/svg+xml"))
        self.assertIn("<svg", favicon.text)
        self.assertIn("#22c55e", favicon.text)
        self.assertIn("Kennethbot", page.text)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(v1_allowed.status_code, 200)
        self.assertEqual(v1_allowed.json()["version"], "test")
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
        self.assertIn("Kennethbot Control", page.text)
        self.assertIn('/bot-admin/favicon.svg?v=test', page.text)
        self.assertNotIn("unpkg.com", page.text)
        self.assertEqual(sandboxes.json()["active_commands"], 1)
        self.assertEqual(
            sandboxes.json()["items"][0]["agent_tasks"],
            [],
        )
        self.assertEqual(stickers.json()["counts"]["total"], 1)
        self.assertEqual(media.json()["counts"]["total"], 1)
        self.assertEqual(media.json()["vision_profile"], "gpt-5.6-luna")
        self.assertEqual(sources.json()["items"][0]["source_id"], 3)
        self.assertEqual(context_plans.json()["items"][0]["focus_message_id"], 10)
        self.assertEqual(databases.json()["writable_node"], "h610")
        self.assertEqual(
            [node["name"] for node in databases.json()["nodes"]],
            ["h610", "tank"],
        )
        self.assertNotIn("password", databases.text.lower())
        self.assertNotIn("rendered_context", context_plans.text)
        self.assertEqual(observability.status_code, 200)
        self.assertEqual(observability.json()["process"]["totals"]["turns"], 1)
        self.assertEqual(
            observability.json()["traces"]["items"][0]["trace_id"],
            "a" * 32,
        )
        self.assertNotIn("scope_key", observability.text)
        self.assertNotIn("objective", observability.text)
        group_rows = group_models.json()["items"]
        self.assertEqual(
            [row["group_id"] for row in group_rows],
            [201644592, 930690526],
        )
        self.assertFalse(group_rows[0]["enabled"])
        self.assertTrue(group_rows[1]["group_override"])
        self.assertEqual(group_rows[1]["overrides"][0]["profile"], "main")
        self.assertEqual(group_rows[1]["admins"][0]["user_id"], 3526452465)
        self.assertEqual(group_rows[1]["members"][0]["user_id"], 2291939848)
        self.assertNotIn("never-return-this-secret", group_models.text)

    def test_admin_can_change_group_and_member_models(self) -> None:
        models = ModelCatalog.from_json(
            json.dumps(
                {
                    "main": {
                        "provider": "test",
                        "model": "model-a",
                        "api_key_required": False,
                    },
                    "alternate": {
                        "provider": "test",
                        "model": "model-b",
                        "api_key_required": False,
                    }
                }
            ),
            default_profile="main",
            environ={},
        )
        preferences = ModelPreferences()
        preferences.models.clear()
        app = FastAPI()
        register_admin(
            app,
            AdminServices(
                version="test",
                started_at=1,
                model_catalog=models,
                model_preferences=preferences,
                user_profiles=UserProfiles(),
                message_ledger=MessageLedger(),
                settings=Settings(),
            ),
            token="secret",
        )

        async def run():
            transport = httpx.ASGITransport(app=app)
            headers = {"Authorization": "Bearer secret"}
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                group = await client.put(
                    "/bot-admin/api/group-models/930690526/default",
                    headers=headers,
                    json={"profile": "alternate"},
                )
                member = await client.put(
                    "/bot-admin/api/group-models/930690526/users/2291939848",
                    headers=headers,
                    json={"profile": "main"},
                )
                toggle = await client.put(
                    "/bot-admin/api/group-models/930690526/enabled",
                    headers=headers,
                    json={"enabled": False},
                )
                vision_toggle = await client.put(
                    "/bot-admin/api/group-models/930690526/vision-auto-describe",
                    headers=headers,
                    json={"enabled": False},
                )
                snapshot = await client.get(
                    "/bot-admin/api/group-models",
                    headers=headers,
                )
                reset = await client.put(
                    "/bot-admin/api/group-models/930690526/users/2291939848",
                    headers=headers,
                    json={"profile": None},
                )
                invalid = await client.put(
                    "/bot-admin/api/group-models/930690526/default",
                    headers=headers,
                    json={"profile": "missing"},
                )
                return (
                    group,
                    member,
                    toggle,
                    vision_toggle,
                    snapshot,
                    reset,
                    invalid,
                )

        group, member, toggle, vision_toggle, snapshot, reset, invalid = asyncio.run(
            run()
        )
        self.assertEqual(group.status_code, 200)
        self.assertEqual(member.status_code, 200)
        self.assertEqual(toggle.status_code, 200)
        self.assertEqual(vision_toggle.status_code, 200)
        row = next(
            item
            for item in snapshot.json()["items"]
            if item["group_id"] == 930690526
        )
        self.assertEqual(row["dynamic_group_profile"], "alternate")
        self.assertFalse(row["enabled"])
        self.assertFalse(row["enabled_override"])
        self.assertEqual(row["enabled_source"], "dashboard")
        self.assertFalse(row["vision_auto_describe"])
        self.assertEqual(row["vision_auto_describe_source"], "dashboard")
        selected_admin = next(
            item for item in row["admins"] if item["user_id"] == 3526452465
        )
        self.assertEqual(selected_admin["explicit_profile"], "main")
        self.assertEqual(selected_admin["effective_profile"], "main")
        selected_member = next(
            item for item in row["members"] if item["user_id"] == 2291939848
        )
        self.assertEqual(selected_member["explicit_profile"], "main")
        self.assertEqual(reset.status_code, 200)
        self.assertNotIn(
            "group:930690526:user:2291939848",
            preferences.models,
        )
        self.assertEqual(invalid.status_code, 422)

    def test_admin_v1_versions_audits_and_rejects_stale_tool_updates(self) -> None:
        self.addCleanup(configure_tool_overrides, {})
        app = FastAPI()
        register_admin(
            app,
            AdminServices(version="test", started_at=1),
            token="secret",
        )

        async def run():
            transport = httpx.ASGITransport(app=app)
            headers = {
                "Authorization": "Bearer secret",
                "If-Match": '"0"',
                "X-Admin-Actor": "Kenneth",
            }
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                before = await client.get(
                    "/bot-admin/api/v1/tools",
                    headers={"Authorization": "Bearer secret"},
                )
                changed = await client.put(
                    "/bot-admin/api/v1/tools/web_search/enabled",
                    headers=headers,
                    json={"enabled": False},
                )
                stale = await client.put(
                    "/bot-admin/api/v1/tools/web_search/enabled",
                    headers=headers,
                    json={"enabled": True},
                )
                audit = await client.get(
                    "/bot-admin/api/v1/audit",
                    headers={"Authorization": "Bearer secret"},
                )
                versions = await client.get(
                    "/bot-admin/api/v1/resource-versions",
                    headers={"Authorization": "Bearer secret"},
                )
                return before, changed, stale, audit, versions

        before, changed, stale, audit, versions = asyncio.run(run())
        self.assertEqual(before.json()["resource_version"], 0)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["resource_version"], 1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "resource_version_conflict",
        )
        self.assertEqual(versions.json()["versions"]["tools"], 1)
        self.assertEqual(audit.json()["items"][0]["actor"], "Kenneth")
        self.assertEqual(audit.json()["items"][0]["action"], "tool.enabled.set")


if __name__ == "__main__":
    unittest.main()
