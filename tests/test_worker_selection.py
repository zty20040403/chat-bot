from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from src.cluster_control.execution_service import ClusterExecutionService

nonebot.init()

import src.plugins.ai_chat as ai_chat


class RecordingStore:
    def __init__(self):
        self.records = []

    def submit_job(self, record):
        self.records.append(record)
        return {**record, "status": "queued"}


def service():
    return ClusterExecutionService(
        RecordingStore(),
        inventory=tuple({"host_id": host, "compute": True} for host in ("h310", "h610", "tank")),
        worker_hosts={host + "-worker": host for host in ("h310", "h610", "tank")},
        diagnostic_targets=({"target_id": "h610-worker", "url": "http://192.0.2.3/health"},),
    )


class WorkerSelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_unknown_or_ungranted_worker_is_rejected_before_enqueue(self):
        control = service()
        control.inventory["tank"]["compute"] = False
        for worker in ("not-registered", "tank-worker"):
            with self.subTest(worker=worker), self.assertRaises(PermissionError):
                control.submit_job({"kind": "probe.http", "payload": {"target_id": "h610-worker"},
                    "constraints": {"worker_id": worker}, "idempotency_key": "worker-selection"},
                    actor_id="qq:321", origin_scope="onebot-v11:group:789")
        self.assertEqual(control.store.records, [])

    async def test_model_tool_preserves_executor_separately_from_probe_target(self):
        control = service()
        calls = []

        async def submit(payload, *, actor, origin):
            calls.append((actor, origin))
            return control.submit_job(payload, actor_id=actor, origin_scope=origin)

        client = SimpleNamespace(submit_job=AsyncMock(side_effect=submit))
        requested = {"kind": "probe.http", "target_id": "h610-worker", "idempotency_key": "worker-selection"}

        async def model(user_text, history, tools, execute_tool, **kwargs):
            names = {tool["function"]["name"] for tool in tools}
            self.assertIn("cluster_job_submit", names)
            result = json.loads(await execute_tool("cluster_job_submit", requested))
            self.assertEqual(result["status"], "queued", result)
            return "queued"

        event = GroupMessageEvent(time=1, self_id=999, post_type="message", sub_type="normal",
            user_id=321, message_type="group", message_id=654, message=Message("check worker"),
            original_message=Message("check worker"), raw_message="check worker", font=0,
            sender={"user_id": 321, "nickname": "tester", "role": "member"}, group_id=789)
        with (
            patch.object(ai_chat.app_context, "fleet_client", client),
            patch.object(ai_chat.app_context, "subagent_coordinator", None),
            patch.object(ai_chat.app_context, "message_ledger", None),
            patch.object(ai_chat.app_context, "pin_store", None),
            patch.object(ai_chat.app_context, "source_store", None),
            patch.object(ai_chat.handlers.tools, "_fleet_tools_allowed", return_value=True),
            patch.object(ai_chat.handlers.commands, "_current_long_term_memory", return_value=""),
            patch.object(ai_chat.app_context.memory, "append_turn"),
            patch("src.plugins.ai_chat.tool_executor.ask_deepseek_with_tools", new=model),
        ):
            for index, worker in enumerate(("h310-worker", "h610-worker", "tank-worker", "")):
                requested["idempotency_key"] = "worker-selection-" + str(index)
                if worker:
                    requested["worker_id"] = worker
                else:
                    requested.pop("worker_id", None)
                await ai_chat.handlers.tools._ask_ai(AsyncMock(), event, "check worker",
                    available_image_sources=[])
                record = control.store.records[-1]
                self.assertEqual(record["constraints"]["worker_id"], worker)
                self.assertEqual(record["payload"]["target_id"], "h610-worker")
                self.assertEqual(record["actor_id"], "qq:321")
                self.assertEqual(record["origin_scope"], "onebot-v11:group:789")
        self.assertEqual(len(calls), 4)
