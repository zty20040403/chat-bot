from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot

nonebot.init()

from src.plugins.ai_chat.ai_tools import CLUSTER_JOB_SUBMIT_TOOL, HOST_INSPECT_TOOL, SERVICE_INSPECT_TOOL
from src.plugins.ai_chat.fleet_client import FleetControlError
from src.plugins.ai_chat.fleet_tools import (
    inspect_host,
    fleet_overview,
    model_status,
    requires_local_model_status,
    summarize_fleet,
)
from src.plugins.ai_chat.tool_policy import ToolCatalog


def fleet_payload(now: int) -> dict:
    hosts = []
    for name in ("h310", "h610", "r5s", "r5sjp", "r6s", "rpi4", "shanghai", "tank"):

        def metric(value: int) -> dict:
            return {
                "samples": [
                    {
                        "labels": {"mountpoint": mount},
                        "value": value,
                        "state": "available",
                    }
                    for mount in ("/", *(f"/run/{i}" for i in range(70)))
                ]
            }

        hosts.append(
            {
                "host": name,
                "agent": {"state": "reachable", "failed_units": 0},
                "exporter": {"state": "up", "sample_at_unix_seconds": now - 5},
                "pressure": {
                    "filesystem_size_bytes": metric(500 * 1024**3),
                    "filesystem_available_bytes": metric(76 * 1024**3),
                },
            }
        )
    return {
        "status": "fresh",
        "observed_at": now,
        "expires_at": now + 20,
        "data": {"hosts": hosts},
        "active_alerts": {"status": "fresh", "data": {"alerts": []}},
    }


class FleetProjectionTests(unittest.IsolatedAsyncioTestCase):
    def test_job_tool_accepts_exact_worker_selection(self) -> None:
        catalog = ToolCatalog([CLUSTER_JOB_SUBMIT_TOOL])
        args = {"kind": "probe.http", "target_id": "h610-worker", "idempotency_key": "worker-selection"}
        self.assertTrue(catalog.validate("cluster_job_submit", args).ok)
        for host in ("h310", "h610", "tank"):
            self.assertTrue(catalog.validate("cluster_job_submit", {**args, "worker_id": host + "-worker"}).ok)
        for invalid in ("", "../tank", "tank;reboot", "*", "x" * 65):
            self.assertFalse(catalog.validate("cluster_job_submit", {**args, "worker_id": invalid}).ok)

    async def test_worker_overview_distinguishes_stale_and_draining(self) -> None:
        workers = [{"worker_id": host + "-worker", "host_id": host, "last_seen_at": seen,
                    "availability": "available", "capabilities": ["probe.http"],
                    "capacity": {"cpu_millis": 2000, "secret": "not-for-model"}}
                   for host, seen in (("h310", 995), ("h610", 900), ("tank", 995))]
        policies = [{"worker_id": host + "-worker", "desired_availability": state}
                    for host, state in (("h310", "available"), ("h610", "available"), ("tank", "draining"))]
        client = SimpleNamespace(fleet=AsyncMock(return_value=fleet_payload(1000)),
            workers=AsyncMock(return_value={"items": workers}),
            resource_policies=AsyncMock(return_value={"items": policies}))
        result = await fleet_overview(client, now=1000)
        items = result["workers"]["items"]
        self.assertEqual([x["ready_for_scheduling"] for x in items], [True, False, False])
        self.assertEqual([x["host_id"] for x in items], ["h310", "h610", "tank"])
        self.assertNotIn("not-for-model", json.dumps(result))
        client.resource_policies.side_effect = FleetControlError("unavailable", "not available")
        result = await fleet_overview(client, now=1000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["workers"]["status"], "partial")
        self.assertTrue(all(not x["ready_for_scheduling"] for x in result["workers"]["items"]))
        client.workers.side_effect = FleetControlError("timeout", "not available")
        result = await fleet_overview(client, now=1000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["workers"]["status"], "unavailable")
        self.assertEqual(result["workers"]["items"], [])

    def test_qwen_status_follow_up_requires_a_fresh_read(self) -> None:
        self.assertTrue(requires_local_model_status("现在千问呢"))
        self.assertTrue(requires_local_model_status("看下千问寄了吗"))
        self.assertFalse(requires_local_model_status("千问模型是谁研发的"))

    def test_all_eight_hosts_survive_tool_budget(self) -> None:
        payload = fleet_payload(1000)
        self.assertGreater(len(json.dumps(payload)), 12000)
        summary = summarize_fleet(payload, now=1000)
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 8000)
        tank = summary["hosts"][-1]
        self.assertEqual(tank["host_id"], "tank")
        self.assertEqual(tank["status"], "online")
        self.assertEqual(tank["root_disk"]["available_gib"], 76)
        self.assertEqual(tank["failed_service_count"], 0)

    def test_stale_data_cannot_be_reported_as_online(self) -> None:
        summary = summarize_fleet(fleet_payload(1000), now=1100)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["hosts"][-1]["status"], "stale")
        self.assertIsNone(summary["hosts"][-1]["root_disk"])
        self.assertIsNone(summary["hosts"][-1]["failed_service_count"])

    def test_unavailable_alerts_are_not_zero_alerts(self) -> None:
        payload = fleet_payload(1000)
        payload["active_alerts"] = {"status": "unavailable"}
        summary = summarize_fleet(payload, host_id="tank", now=1000)
        self.assertEqual(summary["hosts"][0]["status"], "online")
        self.assertIsNone(summary["hosts"][0]["active_alert_count"])

    def test_host_query_has_no_dummy_service_argument(self) -> None:
        registry = ToolCatalog([HOST_INSPECT_TOOL, SERVICE_INSPECT_TOOL])
        self.assertTrue(registry.validate("host_inspect", {"host_id": "tank"}).ok)
        self.assertFalse(
            registry.validate("host_inspect", {"host_id": "tank", "unit": "q"}).ok
        )
        self.assertFalse(
            registry.validate(
                "service_inspect", {"host_id": "tank", "unit": "systemd"}
            ).ok
        )
        self.assertTrue(
            registry.validate(
                "service_inspect", {"host_id": "tank", "unit": "nginx.service"}
            ).ok
        )

    async def test_system_query_failure_does_not_erase_online_evidence(self) -> None:
        client = SimpleNamespace(
            host=AsyncMock(side_effect=FleetControlError("timeout", "query timed out")),
            fleet=AsyncMock(return_value=fleet_payload(int(time.time()))),
        )
        result = await inspect_host(client, "tank")
        self.assertTrue(result["ok"])
        self.assertEqual([h["host_id"] for h in result["hosts"]], ["tank"])
        self.assertEqual(result["hosts"][0]["status"], "online")
        self.assertEqual(result["system"]["status"], "unavailable")

    async def test_fleet_failure_does_not_erase_live_system_response(self) -> None:
        client = SimpleNamespace(
            host=AsyncMock(
                return_value={
                    "status": "fresh",
                    "data": {"host": "tank", "facts": {"kernel": "test"}},
                }
            ),
            fleet=AsyncMock(
                side_effect=FleetControlError("timeout", "query timed out")
            ),
        )
        result = await inspect_host(client, "tank")
        self.assertTrue(result["ok"])
        self.assertIn("tank 在线", result["summary"])

    async def test_qwen_listing_and_generation_reported_separately(self) -> None:
        profile = SimpleNamespace(
            name="qwen-local",
            model="qwen-test",
            circuit_breaker_enabled=False,
            api_key="SECRET",
        )
        runtime = SimpleNamespace(
            profile=profile,
            probe_once=AsyncMock(),
            snapshot=lambda: {
                "ready": True,
                "reason": "就绪",
                "state": "ready",
                "control_configured": False,
            },
        )
        context = SimpleNamespace(
            local_model=runtime,
            model_catalog=SimpleNamespace(profiles=[profile]),
            settings=SimpleNamespace(model_simple_chat_profile="qwen-local"),
            llm_gateway=SimpleNamespace(
                health_snapshot=lambda: {
                    "qwen-local": {
                        "last_failure_at": 1000,
                        "last_success_at": 900,
                        "last_error_kind": "empty_response",
                        "last_error": "SECRET",
                    }
                }
            ),
        )
        result = await model_status(context)
        runtime.probe_once.assert_awaited_once()
        self.assertTrue(result["readiness"]["ready"])
        self.assertEqual(result["requests"]["latest_result"], "failed")
        self.assertIn("最近一次实际请求失败", result["summary"])
        self.assertNotIn("SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
