from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot

os.environ.setdefault("AI_ALLOW_LEGACY_SQLITE", "true")
os.environ.setdefault("AI_SUBAGENTS_ENABLED", "false")
nonebot.init()

from src.plugins.ai_chat.agent import AgentResult, ContextPacket, DEFAULT_AGENT_REGISTRY
from src.plugins.ai_chat.agent.execution import EntryDecision
from src.plugins.ai_chat.agent.model_routing import agent_profile_names, choose_agent_profile
from src.plugins.ai_chat.agent.sessions import read_upstream_result
from src.plugins.ai_chat.deepseek import DeepSeekTrace, ask_deepseek_with_tools
from src.plugins.ai_chat.llm_gateway import LLMGateway, completion_profile_scope
from src.plugins.ai_chat.model_catalog import ModelCatalog, ModelProfile
from src.plugins.ai_chat.subagents import SubAgentCoordinator, SubAgentStore, TaskStep


def profile(name="test"):
    return ModelProfile(name=name, model=name, provider="test", protocol="openai-chat",
                        base_url="http://127.0.0.1", api_key_required=False)


def decision(mode="direct", answer="你好"):
    return {"mode": mode, "reason": "根据交付内容分工", "answer": answer if mode == "direct" else "",
            "task_type": {"direct": "conversation", "delegate": "execution", "workflow": "project"}[mode],
            "delivery_required": False,
            "objective": "精心写个谷粒商城", "deliverables": [] if mode == "direct" else ["可运行的商城"],
            "constraints": [], "acceptance": [] if mode == "direct" else ["完成集成测试"],
            "steps": [] if mode == "direct" else [
                {"id": "frontend", "agent": "coder", "objective": "实现前端", "deliverable": "前端源码", "depends_on": []},
                *([] if mode == "delegate" else [
                    {"id": "backend", "agent": "coder", "objective": "实现后端", "deliverable": "后端源码", "depends_on": []},
                    {"id": "integration", "agent": "coder", "objective": "集成和测试", "deliverable": "集成测试记录", "depends_on": ["frontend", "backend"]},
                ]),
            ]}


def completion(payload=None, content=""):
    calls = [] if payload is None else [SimpleNamespace(
        id="entry-1", type="function", function=SimpleNamespace(name="decide_execution", arguments=json.dumps(payload))) ]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))], usage=None), ""


TOOL = {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}}


class EntryContractTests(unittest.TestCase):
    def test_three_modes_and_same_role_steps(self):
        for mode in ("direct", "delegate", "workflow"):
            parsed = EntryDecision.parse(decision(mode))
            self.assertEqual(parsed.mode, mode)
        self.assertEqual([s["agent"] for s in EntryDecision.parse(decision("workflow")).steps], ["coder"] * 3)

    def test_invalid_plans_never_downgrade_to_direct(self):
        invalid = []
        raw = decision("workflow"); raw["steps"][0]["depends_on"] = ["integration"]; invalid.append(raw)
        raw = decision("workflow"); raw["steps"][0]["id"] = "backend"; invalid.append(raw)
        raw = decision("workflow"); raw["steps"][2]["depends_on"] = ["missing"]; invalid.append(raw)
        raw = decision("direct"); raw["deliverables"] = ["源码"]; invalid.append(raw)
        raw = decision("direct"); raw["task_type"] = "project"; invalid.append(raw)
        raw = decision("delegate"); raw["acceptance"] = []; invalid.append(raw)
        invalid.append({})
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                EntryDecision.parse(raw)

    def test_missing_result_status_is_not_success(self):
        self.assertEqual(AgentResult.from_payload({}).status, "partial")
        self.assertEqual(AgentResult.from_payload({"summary": "done"}).status, "partial")
        self.assertEqual(AgentResult.from_payload({"status": "success", "summary": ""}).status, "partial")

    def test_handoff_is_valid_json_and_can_read_tail_without_cross_task_access(self):
        upstream = {"frontend": {"status": "success", "summary": "长结果" * 6000, "facts": [f"fact-{i}" for i in range(100)]}}
        packet = ContextPacket("group:1", "group:1:user:2", 2, 3, "集成", supporting_context="正文" * 5000)
        rendered = packet.for_agent(DEFAULT_AGENT_REGISTRY.worker("coder"), upstream=upstream).rendered_context
        capsule = json.loads(rendered.split("[上游结构化结果索引]\n")[1])
        self.assertEqual(capsule["frontend"]["sections"]["facts"], 100)
        tail = json.loads(read_upstream_result(upstream, {"step_id": "frontend", "section": "facts", "offset": 99}))
        self.assertEqual(tail["data"], ["fact-99"])
        denied = json.loads(read_upstream_result(upstream, {"step_id": "other-task", "section": "facts"}))
        self.assertFalse(denied["ok"])


class EntryLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_uses_one_model_call_and_current_context(self):
        trace, handler = DeepSeekTrace(), AsyncMock(return_value=None)
        with patch("src.plugins.ai_chat.deepseek._completion_with_optional_stream", new=AsyncMock(return_value=completion(decision()))) as model:
            result = await ask_deepseek_with_tools("你好", [], [TOOL], AsyncMock(), profile=profile(),
                                                  group_context="本群当前话题", entry_handler=handler, trace=trace)
        self.assertEqual(result, "你好")
        self.assertEqual(model.await_count, 1)
        self.assertIn("本群当前话题", str(model.call_args.kwargs["messages"]))
        self.assertEqual(trace.execution_decisions[0]["mode"], "direct")

    async def test_workflow_uses_host_result_without_redundant_main_call(self):
        handler = AsyncMock(return_value="task#1 成品")
        execute = AsyncMock()
        with patch("src.plugins.ai_chat.deepseek._completion_with_optional_stream", new=AsyncMock(return_value=completion(decision("workflow")))) as model:
            result = await ask_deepseek_with_tools("精心写个谷粒商城", [], [TOOL], execute,
                                                  profile=profile(), entry_handler=handler)
        self.assertEqual(result, "task#1 成品")
        self.assertEqual(model.await_count, 1)
        self.assertEqual(handler.call_args.args[0].mode, "workflow")
        execute.assert_not_awaited()

    async def test_invalid_entry_gets_one_repair_without_side_effects(self):
        handler, execute = AsyncMock(), AsyncMock()
        with patch("src.plugins.ai_chat.deepseek._completion_with_optional_stream", new=AsyncMock(return_value=completion({}))) as model:
            with self.assertRaisesRegex(RuntimeError, "no task was started"):
                await ask_deepseek_with_tools("做商城", [], [TOOL], execute, profile=profile(), entry_handler=handler)
        self.assertEqual(model.await_count, 2)
        handler.assert_not_awaited(); execute.assert_not_awaited()

    async def test_delegate_returns_control_to_parent(self):
        with patch("src.plugins.ai_chat.deepseek._completion_with_optional_stream", new=AsyncMock(side_effect=[
            completion(decision("delegate")), completion(content="脚本运行通过"),
        ])) as model:
            result = await ask_deepseek_with_tools("运行脚本", [], [TOOL], AsyncMock(), profile=profile(),
                                                  entry_handler=AsyncMock(return_value='{"status":"success"}'))
        self.assertEqual(result, "脚本运行通过")
        self.assertEqual(model.await_count, 2)


class ModelPolicyTests(unittest.TestCase):
    def test_roles_select_different_models_and_sol_is_not_a_hidden_fallback(self):
        profiles = {name: profile(name) for name in ("qwen-local", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "codex-auto-review")}
        catalog = ModelCatalog(profiles, default_profile="qwen-local")
        self.assertEqual(choose_agent_profile("coder", catalog.default, catalog, {}).name, "gpt-5.6-terra")
        self.assertEqual(choose_agent_profile("researcher", catalog.default, catalog, {}).name, "gpt-5.6-luna")
        names = agent_profile_names(catalog, {})
        self.assertNotIn("gpt-5.6-sol", names); self.assertNotIn("codex-auto-review", names)
        gateway = LLMGateway(catalog=catalog)
        with completion_profile_scope(names):
            self.assertNotIn("gpt-5.6-sol", {p.name for p in gateway._completion_candidates(catalog.default, {})})
        self.assertIn("gpt-5.6-sol", {p.name for p in gateway._completion_candidates(catalog.default, {})})
        override = {"coder": "gpt-5.6-sol"}
        self.assertEqual(choose_agent_profile("coder", catalog.default, catalog, override).name, "gpt-5.6-sol")
        self.assertFalse(profiles["qwen-local"].thinking == "disabled")


class SessionAndSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SubAgentStore(":memory:")
        self.profile = profile()
        self.coordinator = SubAgentCoordinator(self.store, ModelCatalog({"test": self.profile}, default_profile="test"), logger=Mock())

    def tearDown(self):
        self.store.close()

    def task(self):
        return self.store.create_task(scope_key="group:1", conversation_id="group:1:user:2", requester_user_id=2,
                                      trigger_message_id=3, objective="demo", max_parallelism=3, max_steps=8)

    async def test_reuses_entry_plan_and_same_role_workers_really_overlap(self):
        started, barrier = set(), asyncio.Event()
        async def worker(text, history, tools, execute_tool, **kwargs):
            current = "frontend" if "实现前端" in text else "backend" if "实现后端" in text else "integration"
            self.assertEqual(history, [])
            if current in {"frontend", "backend"}:
                started.add(current)
                if len(started) == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), timeout=1)
            else:
                self.assertTrue(barrier.is_set())
            kwargs["transcript_sink"]([{"role": "user", "content": current}])
            return json.dumps({"status": "success", "summary": current})
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_json", new=AsyncMock()) as planner, \
             patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=worker), \
             patch("src.plugins.ai_chat.subagents.ask_deepseek", new=AsyncMock(return_value="已集成")):
            result = await self.coordinator.run(scope_key="group:1", conversation_id="group:1:user:2", requester_user_id=2,
                trigger_message_id=3, objective="商城", context="", selected_profile=self.profile,
                tools=[TOOL], execute_tool=AsyncMock(), entry_decision=EntryDecision.parse(decision("workflow")))
        planner.assert_not_awaited()
        self.assertIn("已集成", result)
        task = self.store.recent(limit=1)[0]
        histories = [self.store.agent_session(task.task_id, run.run_id, scope_key="group:1", requester_user_id=2)["messages"] for run in self.store.runs(task.task_id)]
        self.assertEqual([items[0]["content"] for items in histories], ["frontend", "backend", "integration"])

    async def test_sessions_are_versioned_and_scoped(self):
        task = self.task()
        run = self.store.create_run(task.task_id, TaskStep("code", "coder", "code", "files"), allowed_tools=[], model_profile="test")
        kwargs = dict(scope_key="group:1", requester_user_id=2)
        self.store.save_agent_session(task.task_id, run.run_id, [{"role": "user", "content": "first"}],
                                     model_profile="test", expected_version=0, **kwargs)
        with self.assertRaises(RuntimeError):
            self.store.save_agent_session(task.task_id, run.run_id, [], model_profile="test", expected_version=0, **kwargs)
        with self.assertRaises(PermissionError):
            self.store.agent_session(task.task_id, run.run_id, scope_key="group:2", requester_user_id=2)
        with self.assertRaises(PermissionError):
            self.store.agent_session(task.task_id, run.run_id, scope_key="group:1", requester_user_id=9)
        self.assertEqual(self.store.agent_session(task.task_id, run.run_id, **kwargs)["version"], 1)

    async def test_retry_keeps_its_own_transcript(self):
        attempts = 0
        async def worker(text, history, tools, execute_tool, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                kwargs["transcript_sink"]([{"role": "user", "content": "own saved work"}])
                raise ConnectionError("temporary model outage")
            self.assertEqual(history, [{"role": "user", "content": "own saved work"}])
            return '{"status":"success","summary":"continued"}'
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=worker):
            result = await self.coordinator.delegate(role="coder", scope_key="group:1", conversation_id="group:1:user:2",
                requester_user_id=2, trigger_message_id=3, objective="code", context="", selected_profile=self.profile,
                tools=[], execute_tool=AsyncMock())
        self.assertEqual(attempts, 2)
        self.assertEqual(result["result"]["summary"], "continued")

    async def test_foreign_context_is_rejected_before_creating_task(self):
        with self.assertRaises(PermissionError):
            await self.coordinator.delegate(role="coder", scope_key="group:1", conversation_id="group:1:user:2",
                requester_user_id=2, trigger_message_id=3, objective="code", context="", selected_profile=self.profile,
                tools=[], execute_tool=AsyncMock(), context_packet=ContextPacket("group:9", "group:9:user:2", 2, 3, "foreign"))
        self.assertEqual(self.store.recent(), [])

    async def test_required_delivery_without_artifact_cannot_complete(self):
        payload = decision("delegate")
        payload["delivery_required"] = True
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", new=AsyncMock(return_value='{"status":"success","summary":"done"}')):
            result = await self.coordinator.delegate(role="coder", scope_key="group:1", conversation_id="group:1:user:2",
                requester_user_id=2, trigger_message_id=3, objective="make it", context="", selected_profile=self.profile,
                tools=[], execute_tool=AsyncMock(), entry_decision=EntryDecision.parse(payload))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["delivery_state"], "failed_or_unknown")
        self.assertEqual(result["deliveries"][0]["state"], "missing_artifact")

    async def test_transcript_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite3"
            store = SubAgentStore(path)
            task = store.create_task(scope_key="group:1", conversation_id="group:1:user:2", requester_user_id=2,
                                     trigger_message_id=3, objective="test", max_parallelism=1, max_steps=1)
            run = store.create_run(task.task_id, TaskStep("code", "coder", "code", "files"), allowed_tools=[], model_profile="test")
            store.save_agent_session(task.task_id, run.run_id, [{"role": "assistant", "content": "durable"}],
                                     scope_key="group:1", requester_user_id=2, model_profile="test", expected_version=0)
            store.close()
            reopened = SubAgentStore(path)
            try:
                session = reopened.agent_session(task.task_id, run.run_id, scope_key="group:1", requester_user_id=2)
                self.assertEqual(session["messages"][0]["content"], "durable")
            finally:
                reopened.close()
