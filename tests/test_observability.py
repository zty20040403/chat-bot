from __future__ import annotations

import os
import unittest

import httpx
import nonebot
from fastapi import FastAPI

os.environ.setdefault("AI_ALLOW_LEGACY_SQLITE", "true")
nonebot.init()

from src.plugins.ai_chat.deepseek import DeepSeekTrace
from src.plugins.ai_chat.observability import (
    current_trace_id,
    register_metrics_endpoint,
    telemetry,
)


class EmptyTasks:
    def list_all(self) -> list[object]:
        return []


class EmptyDeliveries:
    def stats(self) -> dict[str, int]:
        return {"pending": 2, "ambiguous": 1}


class ObservabilityTests(unittest.TestCase):
    def test_trace_id_is_attached_to_archived_model_trace(self) -> None:
        telemetry.configure("kennethbot-test", service_version="test")
        with telemetry.span("test.turn") as trace_id:
            self.assertEqual(current_trace_id(), trace_id)
            payload = DeepSeekTrace(profile="test").to_payload()
        self.assertEqual(payload["trace_id"], trace_id)
        self.assertEqual(len(trace_id), 32)

    def test_prometheus_endpoint_exports_runtime_gauges(self) -> None:
        app = FastAPI()
        register_metrics_endpoint(
            app,
            path="/metrics",
            service_name="kennethbot-test",
            service_version="test",
            running_tasks=EmptyTasks(),
            delivery_store=EmptyDeliveries(),
        )

        async def run() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                return await client.get("/metrics")

        import asyncio

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertIn("kennethbot_runtime_tasks 0.0", response.text)
        self.assertIn(
            'kennethbot_outbox_deliveries{status="pending"} 2.0',
            response.text,
        )


if __name__ == "__main__":
    unittest.main()
