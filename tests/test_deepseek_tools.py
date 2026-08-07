from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from src.plugins.ai_chat.deepseek import DeepSeekTrace, ask_deepseek_with_tools
from src.plugins.ai_chat.config import settings


class DeepSeekToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_fallback_keeps_replay_and_tool_context(self) -> None:
        final_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(return_value=final_response),
        ) as create_completion:
            answer = await ask_deepseek_with_tools(
                "continue",
                [],
                [],
                AsyncMock(),
                tool_context="host continuity facts",
                replay_prefix=[{"role": "user", "content": "old task"}],
            )

        self.assertEqual(answer, "ok")
        messages = create_completion.await_args.kwargs["messages"]
        self.assertEqual([item["role"] for item in messages], ["system", "user", "user"])
        self.assertIn("host continuity facts", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "old task")

    async def test_replay_prefix_is_inserted_after_current_system_prompt(self) -> None:
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="continued", tool_calls=[])
                )
            ]
        )
        replay_prefix = [
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old result"},
        ]
        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(return_value=final_response),
        ) as create_completion:
            answer = await ask_deepseek_with_tools(
                "continue",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "noop",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                AsyncMock(return_value='{"ok":true}'),
                replay_prefix=replay_prefix,
            )

        self.assertEqual(answer, "continued")
        roles = [
            message["role"]
            for message in create_completion.await_args.kwargs["messages"]
        ]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(
            create_completion.await_args.kwargs["messages"][1]["content"],
            "old task",
        )

    async def test_invalid_tool_arguments_are_rejected_before_execution(self) -> None:
        tool_call = SimpleNamespace(
            id="call-invalid",
            function=SimpleNamespace(name="search", arguments='{"limit":3}'),
        )
        tool_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="", tool_calls=[tool_call])
                )
            ]
        )
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="参数不完整。", tool_calls=[])
                )
            ]
        )
        execute_tool = AsyncMock(return_value='{"ok":true}')
        events = []

        async def record_event(event) -> None:
            events.append(event)

        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(side_effect=[tool_response, final_response]),
        ):
            answer = await ask_deepseek_with_tools(
                "搜索",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "search",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                },
                                "required": ["query"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                execute_tool,
                event_sink=record_event,
            )

        self.assertEqual(answer, "参数不完整。")
        execute_tool.assert_not_awaited()
        self.assertEqual([event.kind for event in events], ["tool_rejected"])
        self.assertIn("必填", events[0].result)

    async def test_trace_and_event_sink_capture_normalized_loop_order(self) -> None:
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="sandbox_list",
                arguments='{"limit":1}',
            ),
        )
        tool_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="先查看现有沙盒",
                        tool_calls=[tool_call],
                    )
                )
            ]
        )
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="完成。", tool_calls=[])
                )
            ]
        )
        events = []

        async def record_event(event) -> None:
            events.append(event)

        trace = DeepSeekTrace()
        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(side_effect=[tool_response, final_response]),
        ):
            answer = await ask_deepseek_with_tools(
                "检查沙盒",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "sandbox_list",
                            "description": "list",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                AsyncMock(return_value='{"ok":true}'),
                trace=trace,
                event_sink=record_event,
            )

        self.assertEqual(answer, "完成。")
        self.assertEqual(
            [event.kind for event in events],
            ["model_note", "tool_started", "tool_finished"],
        )
        self.assertEqual(events[-1].state, "succeeded")
        self.assertEqual(
            [message["role"] for message in trace.messages],
            ["user", "assistant", "tool", "assistant"],
        )

    async def test_unlimited_tool_rounds_finish_when_model_answers(self) -> None:
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="sandbox_list", arguments="{}"),
        )
        tool_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="", tool_calls=[tool_call])
                )
            ]
        )
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="任务完成。",
                        tool_calls=[],
                    )
                )
            ]
        )
        execute_tool = AsyncMock(return_value='{"ok": true}')

        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(
                side_effect=[tool_response, tool_response, final_response]
            ),
        ) as create_completion:
            answer = await ask_deepseek_with_tools(
                "完成任务",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "sandbox_list",
                            "description": "列出沙箱",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    }
                ],
                execute_tool,
                max_tool_rounds=None,
            )

        self.assertEqual(answer, "任务完成。")
        self.assertEqual(create_completion.await_count, 3)

    async def test_tool_limit_requests_a_final_answer_without_tools(self) -> None:
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="sandbox_list", arguments="{}"),
        )
        tool_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[tool_call],
                    )
                )
            ]
        )
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="任务还没完成，需要继续处理。",
                        tool_calls=[],
                    )
                )
            ]
        )
        execute_tool = AsyncMock(
            return_value=json.dumps({"ok": True, "sandboxes": []})
        )

        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(side_effect=[tool_response, final_response]),
        ) as create_completion:
            answer = await ask_deepseek_with_tools(
                "完成任务",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "sandbox_list",
                            "description": "列出沙箱",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    }
                ],
                execute_tool,
                max_tool_rounds=1,
            )

        self.assertEqual(answer, "任务还没完成，需要继续处理。")
        self.assertEqual(create_completion.await_count, 2)
        self.assertNotIn("tools", create_completion.await_args_list[-1].kwargs)

    async def test_long_tool_result_is_bounded_before_next_round(self) -> None:
        tool_call = SimpleNamespace(
            id="call-long",
            function=SimpleNamespace(name="sandbox_exec", arguments="{}"),
        )
        tool_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="", tool_calls=[tool_call])
                )
            ]
        )
        final_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="完成。", tool_calls=[])
                )
            ]
        )
        oversized_result = "x" * (settings.tool_max_result_chars + 100)

        with patch(
            "src.plugins.ai_chat.deepseek._create_completion",
            new=AsyncMock(side_effect=[tool_response, final_response]),
        ) as create_completion:
            answer = await ask_deepseek_with_tools(
                "运行命令",
                [],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "sandbox_exec",
                            "description": "执行命令",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                AsyncMock(return_value=oversized_result),
                memory_context="[#1] 用户偏好简短回答",
                max_tool_rounds=2,
            )

        self.assertEqual(answer, "完成。")
        second_messages = create_completion.await_args_list[1].kwargs["messages"]
        tool_message = next(
            message for message in second_messages if message["role"] == "tool"
        )
        self.assertLessEqual(
            len(tool_message["content"]),
            settings.tool_max_result_chars,
        )
        self.assertIn("已截断", tool_message["content"])
        self.assertIn(
            "用户偏好简短回答",
            create_completion.await_args_list[0].kwargs["messages"][0]["content"],
        )


if __name__ == "__main__":
    unittest.main()
