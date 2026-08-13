from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
from src.plugins.ai_chat.media_library import MediaRecord


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

    async def test_group_chat_does_not_send_personal_bot_history_to_model(self) -> None:
        captured_history = None

        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            nonlocal captured_history
            del user_text, tools, execute_tool, kwargs
            captured_history = history
            return "基于群聊现场回答"

        with (
            patch(
                "src.plugins.ai_chat.ask_deepseek_with_tools",
                new=fake_deepseek,
            ),
            patch.object(
                conversation_memory,
                "get",
                return_value=[
                    {"role": "user", "content": "个人旧问题"},
                    {"role": "assistant", "content": "个人旧回答"},
                ],
            ),
        ):
            answer = await _ask_ai(
                AsyncMock(),
                _group_event(user_id=320),
                "你觉得呢",
            )

        self.assertIn("基于群聊现场回答", answer)
        self.assertEqual(captured_history, [])

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

    async def test_send_sticker_query_overrides_stale_media_handle(self) -> None:
        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, tools, kwargs
            await execute_tool(
                SEND_STICKER_TOOL_NAME,
                {"query": "猫娘卖萌", "media_handle": "media#46"},
            )
            return ""

        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "sticker.png"
            image_path.write_bytes(b"fake-png")
            record = MediaRecord(
                media_id=42,
                handle="media#42",
                summary="猫娘开心卖萌",
                description="猫耳少女露出开心表情。",
                extracted_text="",
                emotions=("开心",),
                usage=("卖萌",),
                is_sticker=True,
                safety="safe",
                storage_path=image_path,
                mime_type="image/png",
                score=0.9,
            )
            fake_library = SimpleNamespace(
                search_stickers=AsyncMock(return_value=[record]),
                get_sticker=Mock(),
                mark_sent=Mock(),
            )
            with (
                patch(
                    "src.plugins.ai_chat.ask_deepseek_with_tools",
                    new=fake_deepseek,
                ),
                patch("src.plugins.ai_chat.media_library", fake_library),
            ):
                answer = await _ask_ai(
                    AsyncMock(),
                    _group_event(user_id=323),
                    "发个猫娘表情",
                )

        self.assertIsInstance(answer, Message)
        self.assertEqual(len(answer), 1)
        self.assertEqual(answer[0].type, "image")
        fake_library.search_stickers.assert_awaited_once_with(
            "猫娘卖萌",
            limit=5,
        )
        fake_library.get_sticker.assert_not_called()
        fake_library.mark_sent.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
