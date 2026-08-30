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
    VIEW_IMAGE_TOOL_NAME,
)
from src.plugins.ai_chat.context_pipeline import TurnContextPlan
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
                "PostgreSQL 主库怎么选",
            )

        self.assertIn("基于群聊现场回答", answer)
        self.assertEqual(captured_history, [])

    async def test_group_prompt_uses_unranked_chronological_projection(self) -> None:
        captured: dict[str, object] = {}
        ledger = Mock()
        ledger.principal_label_for_native.return_value = None
        context_store = Mock()
        context_store.build_projection.return_value = SimpleNamespace(
            text=(
                "[protected live tail]\n"
                "[msg#11 | Alice] 我看 h310 一直寄\n"
                "[msg#12 | Bob] 今晚吃什么\n"
                "[msg#13 | Alice] 这机器网络还是不稳定"
            )
        )

        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, tools, execute_tool
            captured.update(kwargs)
            return "按上面的 h310 现场锐评"

        import src.plugins.ai_chat as ai_chat

        with (
            patch.object(ai_chat, "message_ledger", ledger),
            patch.object(ai_chat, "context_store", context_store),
            patch.object(ai_chat, "pin_store", None),
            patch.object(ai_chat, "source_store", None),
            patch.object(ai_chat, "_group_turn_context_plan", return_value=None),
            patch.object(ai_chat, "_current_long_term_memory", return_value=""),
            patch.object(ai_chat, "ask_deepseek_with_tools", new=fake_deepseek),
            patch.object(ai_chat.memory, "append_turn"),
        ):
            answer = await ai_chat._ask_ai(
                AsyncMock(),
                _group_event(),
                "你锐评一下",
                available_image_sources=[],
            )

        group_prompt = str(captured["group_context"])
        self.assertIn("我看 h310 一直寄", group_prompt)
        self.assertIn("这机器网络还是不稳定", group_prompt)
        self.assertLess(
            group_prompt.index("我看 h310 一直寄"),
            group_prompt.index("这机器网络还是不稳定"),
        )
        self.assertIn("按上面的 h310 现场锐评", answer)

    async def test_chronological_projection_is_written_to_context_debug(self) -> None:
        ledger = Mock()
        ledger.principal_label_for_native.return_value = None
        ledger.canonical_id_for_native.return_value = None
        ledger.get_in_scope.return_value = SimpleNamespace(
            canonical_message_id=11,
            prompt_text="我看 h310 一直寄",
        )
        context_store = Mock()
        context_store.build_projection.return_value = SimpleNamespace(
            text="[protected live tail]\n[msg#11 | Alice] 我看 h310 一直寄",
            token_estimate=77,
            raw_message_ids=(11,),
            compartment_handles=("chapter-1",),
        )
        journal = Mock()
        journal.fork_parent.return_value = None
        plan = TurnContextPlan(
            scope_key="onebot-v11:group:789",
            current_message_id=12,
            current_principal_id=1,
            focus_message_id=None,
            confidence=0.0,
            reason_codes=("standalone_message",),
            related_message_ids=(),
            candidates=(),
            rendered_context="",
            topic_query="你锐评一下",
        )

        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, tools, execute_tool, kwargs
            return "按时间线回答"

        import src.plugins.ai_chat as ai_chat

        with (
            patch.object(ai_chat, "message_ledger", ledger),
            patch.object(ai_chat, "context_store", context_store),
            patch.object(ai_chat, "pin_store", None),
            patch.object(ai_chat, "source_store", None),
            patch.object(ai_chat, "turn_journal", journal),
            patch.object(ai_chat, "_group_turn_context_plan", return_value=plan),
            patch.object(ai_chat, "_current_long_term_memory", return_value=""),
            patch.object(ai_chat, "ask_deepseek_with_tools", new=fake_deepseek),
            patch.object(ai_chat.memory, "append_turn"),
        ):
            answer = await ai_chat._ask_ai(
                AsyncMock(),
                _group_event(),
                "你锐评一下",
                available_image_sources=[],
                journal_turn_id=44,
            )

        self.assertIn("按时间线回答", answer)
        self.assertEqual(journal.record_context_plan.call_count, 2)
        final_payload = journal.record_context_plan.call_args_list[-1].args[1]
        self.assertEqual(
            final_payload["recall_route"]["mode"],
            "chronological_projection",
        )
        self.assertEqual(final_payload["adaptive_budget"]["used"]["timeline"], 77)
        self.assertEqual(
            [item["handle"] for item in final_payload["recall_candidates"]],
            ["msg#11", "episode#chapter-1"],
        )
        self.assertTrue(final_payload["context_hash"])

    async def test_recent_image_handle_reaches_model_and_view_tool(self) -> None:
        captured: dict[str, object] = {}
        ledger = Mock()
        ledger.principal_label_for_native.return_value = None
        context_store = Mock()
        context_store.build_projection.return_value = SimpleNamespace(
            text=(
                "[protected live tail]\n"
                "[msg#21 | Alice] [image#21.0: 一张待分析图片]"
            )
        )

        async def fake_deepseek(
            user_text,
            history,
            tools,
            execute_tool,
            **kwargs,
        ) -> str:
            del user_text, history, execute_tool
            captured["group_context"] = kwargs["group_context"]
            captured["tool_names"] = {
                tool["function"]["name"] for tool in tools
            }
            return "已看图"

        import src.plugins.ai_chat as ai_chat

        with (
            patch.object(ai_chat, "message_ledger", ledger),
            patch.object(ai_chat, "context_store", context_store),
            patch.object(ai_chat, "pin_store", None),
            patch.object(ai_chat, "source_store", None),
            patch.object(ai_chat, "vision_worker", object()),
            patch.object(ai_chat, "_group_turn_context_plan", return_value=None),
            patch.object(ai_chat, "_current_long_term_memory", return_value=""),
            patch.object(ai_chat, "ask_deepseek_with_tools", new=fake_deepseek),
            patch.object(ai_chat.memory, "append_turn"),
        ):
            await ai_chat._ask_ai(
                AsyncMock(),
                _group_event(),
                "锐评下这个",
                available_image_sources=[],
            )

        self.assertIn("[image#21.0", str(captured["group_context"]))
        self.assertIn(VIEW_IMAGE_TOOL_NAME, captured["tool_names"])

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
            limit=10,
        )
        fake_library.get_sticker.assert_not_called()
        fake_library.mark_sent.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
