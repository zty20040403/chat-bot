from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from src.plugins.ai_chat import (
    _ask_ai,
    _looks_like_secret,
    group_context as current_group_context,
    memory as conversation_memory,
)
from src.plugins.ai_chat.ai_tools import (
    MEMORY_ADD_TOOL_NAME,
    MEMORY_LIST_TOOL_NAME,
    MEMORY_REMOVE_TOOL_NAME,
    SEND_QQ_FACE_TOOL_NAME,
    SEND_STICKER_TOOL_NAME,
)


def _group_event(user_id: int = 321) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=654,
        message=Message("发个表情包"),
        original_message=Message("发个表情包"),
        raw_message="发个表情包",
        font=0,
        sender={
            "user_id": user_id,
            "nickname": "Alice",
            "card": "",
            "role": "member",
        },
        group_id=789,
    )


class NaturalToolRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        memory_patcher = patch.object(conversation_memory, "append_turn")
        context_patcher = patch.object(current_group_context, "append")
        memory_patcher.start()
        context_patcher.start()
        self.addCleanup(memory_patcher.stop)
        self.addCleanup(context_patcher.stop)

    async def test_consecutive_requests_are_not_rate_limited(self) -> None:
        ask_deepseek = AsyncMock(return_value="正常回答")
        event = _group_event(user_id=320)

        with patch(
            "src.plugins.ai_chat.ask_deepseek_with_tools",
            new=ask_deepseek,
        ):
            first = await _ask_ai(AsyncMock(), event, "第一个问题")
            second = await _ask_ai(AsyncMock(), event, "紧接着的第二个问题")

        self.assertIn("正常回答", first)
        self.assertIn("正常回答", second)
        self.assertEqual(ask_deepseek.await_count, 2)

    async def test_natural_request_uses_auto_tool_choice(self) -> None:
        captured_tool_names: set[str] = set()

        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, execute_tool
            captured_tool_names.update(
                tool["function"]["name"] for tool in tools
            )
            self.assertEqual(kwargs["tool_choice"], "auto")
            return "正常回答"

        with patch(
            "src.plugins.ai_chat.ask_deepseek_with_tools",
            new=fake_deepseek,
        ):
            await _ask_ai(AsyncMock(), _group_event(), "发个表情包")

        self.assertIn(SEND_STICKER_TOOL_NAME, captured_tool_names)
        self.assertIn(SEND_QQ_FACE_TOOL_NAME, captured_tool_names)
        self.assertIn(MEMORY_ADD_TOOL_NAME, captured_tool_names)
        self.assertIn(MEMORY_LIST_TOOL_NAME, captured_tool_names)
        self.assertIn(MEMORY_REMOVE_TOOL_NAME, captured_tool_names)

    def test_secret_like_content_is_not_eligible_for_memory(self) -> None:
        self.assertTrue(_looks_like_secret("API_KEY=secret-value"))
        self.assertTrue(_looks_like_secret("密码：123456"))
        self.assertTrue(_looks_like_secret("sk-" + "x" * 26))
        self.assertFalse(_looks_like_secret("我喜欢写 Python"))

    async def test_sticker_is_sent_only_after_model_tool_call(self) -> None:
        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, tools, kwargs
            await execute_tool(SEND_STICKER_TOOL_NAME, {})
            return ""

        with (
            patch(
                "src.plugins.ai_chat.ask_deepseek_with_tools",
                new=fake_deepseek,
            ),
            patch(
                "src.plugins.ai_chat.random_sticker_message",
                return_value=MessageSegment.face(14),
            ),
        ):
            answer = await _ask_ai(
                AsyncMock(),
                _group_event(user_id=322),
                "发个表情包",
            )

        self.assertIsInstance(answer, Message)
        self.assertEqual(len(answer), 1)
        self.assertEqual(answer[0].type, "face")


if __name__ == "__main__":
    unittest.main()
