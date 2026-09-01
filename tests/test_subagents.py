from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

import nonebot

os.environ.setdefault("AI_ALLOW_LEGACY_SQLITE", "true")
os.environ.setdefault("AI_SUBAGENTS_ENABLED", "false")
nonebot.init()

from src.plugins.ai_chat.subagents import (
    AGENT_SPECS,
    SubAgentCoordinator,
    SubAgentStore,
    TaskStep,
    _validate_plan,
    parse_profile_overrides,
    route_subagent_request,
)
from src.plugins.ai_chat.model_catalog import ModelCatalog, ModelProfile
from src.plugins.ai_chat.ai_tools import available_tools


class SubAgentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SubAgentStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_task_run_events_and_result_are_durable(self) -> None:
        task = self.store.create_task(
            scope_key="qq:group:123",
            conversation_id="group:123:user:456",
            requester_user_id=456,
            trigger_message_id=12,
            objective="查资料并写报告",
            max_parallelism=3,
            max_steps=8,
            now=100,
        )
        step = TaskStep(
            key="research",
            role="researcher",
            objective="核实资料",
            deliverable="带来源的事实",
        )
        run = self.store.create_run(
            task.task_id,
            step,
            allowed_tools=["web_search"],
            model_profile="deepseek",
            now=101,
        )
        self.assertTrue(self.store.start_run(run.run_id, now=102))
        self.assertTrue(
            self.store.finish_run(
                run.run_id,
                "succeeded",
                result={"summary": "完成"},
                now=103,
            )
        )
        self.assertTrue(
            self.store.set_task_state(
                task.task_id,
                "completed",
                result={"answer": "报告"},
                now=104,
            )
        )

        stored = self.store.get(task.task_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.result["answer"], "报告")
        self.assertEqual(self.store.runs(task.task_id)[0].status, "succeeded")
        self.assertGreaterEqual(len(self.store.events(task.task_id)), 4)

    def test_cancel_marks_active_task(self) -> None:
        task = self.store.create_task(
            scope_key="qq:group:1",
            conversation_id="group:1:user:2",
            requester_user_id=2,
            trigger_message_id=None,
            objective="长任务",
            max_parallelism=2,
            max_steps=4,
        )
        self.store.set_task_state(task.task_id, "running")
        self.assertTrue(self.store.request_cancel(task.task_id))
        self.assertTrue(self.store.cancellation_requested(task.task_id))
        self.assertEqual(self.store.get(task.task_id).status, "cancelling")  # type: ignore[union-attr]


class SubAgentPlanTests(unittest.TestCase):
    def test_validates_fixed_role_dag(self) -> None:
        steps = _validate_plan(
            {
                "steps": [
                    {
                        "id": "read",
                        "agent": "media",
                        "depends_on": [],
                        "objective": "读取视频",
                        "deliverable": "字幕",
                    },
                    {
                        "id": "report",
                        "agent": "document",
                        "depends_on": ["read"],
                        "objective": "生成报告",
                        "deliverable": "PDF",
                    },
                ]
            },
            "分析视频并生成报告",
            8,
        )
        self.assertEqual([step.role for step in steps], ["media", "document"])
        self.assertEqual(steps[1].dependencies, ("read",))

    def test_rejects_cycle(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "循环依赖"):
            _validate_plan(
                {
                    "steps": [
                        {
                            "id": "a",
                            "agent": "researcher",
                            "depends_on": ["b"],
                            "objective": "A",
                        },
                        {
                            "id": "b",
                            "agent": "analyst",
                            "depends_on": ["a"],
                            "objective": "B",
                        },
                    ]
                },
                "循环",
                8,
            )

    def test_profiles_only_accept_known_roles(self) -> None:
        self.assertEqual(
            parse_profile_overrides('{"coder":"gpt","media":"luna"}'),
            {"coder": "gpt", "media": "luna"},
        )
        with self.assertRaisesRegex(ValueError, "Unknown Sub-Agent role"):
            parse_profile_overrides('{"random":"gpt"}')

    def test_first_version_has_seven_fixed_roles(self) -> None:
        self.assertEqual(
            set(AGENT_SPECS),
            {
                "supervisor",
                "researcher",
                "coder",
                "document",
                "media",
                "analyst",
                "operator",
            },
        )

    def test_automatic_subagent_tool_is_exposed_on_request(self) -> None:
        tools = available_tools(
            include_web_search=False,
            include_image_ocr=False,
            include_subagents=True,
        )
        names = {tool["function"]["name"] for tool in tools}
        self.assertIn("run_subagents", names)

    def test_routes_multi_source_media_report_automatically(self) -> None:
        decision = route_subagent_request(
            "查一下这款产品的详细参数，对比三个来源，再整理成 PDF 发到群里",
            has_media=True,
        )
        self.assertTrue(decision.delegate)
        self.assertIn("multi_source_artifact", decision.reasons)
        self.assertEqual(
            set(decision.domains),
            {"analysis", "document", "media", "research"},
        )

    def test_does_not_route_single_tool_requests(self) -> None:
        for request, has_media in (
            ("看看这张图片", True),
            ("查一下今天天气", False),
            ("分析这段话", False),
            ("帮我写一份 PDF", False),
        ):
            with self.subTest(request=request):
                self.assertFalse(
                    route_subagent_request(request, has_media=has_media).delegate
                )


class SubAgentCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_plans_runs_and_synthesizes_fixed_agents(self) -> None:
        store = SubAgentStore(":memory:")
        profile = ModelProfile(
            name="test",
            provider="test",
            protocol="openai-chat",
            model="test-model",
            base_url="http://127.0.0.1",
            api_key_required=False,
        )
        catalog = ModelCatalog({"test": profile}, default_profile="test")
        coordinator = SubAgentCoordinator(store, catalog, logger=AsyncMock())
        plan = {
            "goal": "完成报告",
            "steps": [
                {
                    "id": "research",
                    "agent": "researcher",
                    "depends_on": [],
                    "objective": "查资料",
                    "deliverable": "事实",
                },
                {
                    "id": "write",
                    "agent": "document",
                    "depends_on": ["research"],
                    "objective": "写报告",
                    "deliverable": "报告",
                },
            ],
        }
        worker_result = '{"status":"success","summary":"完成","artifacts":[]}'
        with (
            patch(
                "src.plugins.ai_chat.subagents.ask_deepseek_json",
                new=AsyncMock(return_value=plan),
            ),
            patch(
                "src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                new=AsyncMock(return_value=worker_result),
            ) as worker,
            patch(
                "src.plugins.ai_chat.subagents.ask_deepseek",
                new=AsyncMock(return_value="最终报告"),
            ),
        ):
            answer = await coordinator.run(
                scope_key="qq:group:1",
                conversation_id="group:1:user:2",
                requester_user_id=2,
                trigger_message_id=3,
                objective="完成报告",
                context="必要上下文",
                selected_profile=profile,
                tools=[],
                execute_tool=AsyncMock(return_value='{"ok":true}'),
            )
        self.assertIn("最终报告", answer)
        self.assertEqual(worker.await_count, 2)
        task = store.recent(limit=1)[0]
        self.assertEqual(task.status, "completed")
        self.assertEqual([run.role for run in store.runs(task.task_id)], ["researcher", "document"])
        store.close()


if __name__ == "__main__":
    unittest.main()
