from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.output_planner import (
    DEFAULT_SILENCE_FACE_ID,
    MAX_REPLY_CHUNKS,
    plan_reply,
)


class OutputPlannerTests(unittest.TestCase):
    def test_splits_blank_lines_and_inline_marker(self) -> None:
        plan = plan_reply("第一段\n\n第二段 [split] 第三段")
        self.assertEqual(
            [chunk.text for chunk in plan.chunks],
            ["第一段", "第二段", "第三段"],
        )

    def test_code_fence_is_its_own_unsplit_chunk(self) -> None:
        plan = plan_reply(
            "看这里\n```python\nx = 1\n\n[split]\ny = 2\n```\n结束"
        )
        self.assertEqual(
            [chunk.text for chunk in plan.chunks],
            [
                "看这里",
                "```python\nx = 1\n\n[split]\ny = 2\n```",
                "结束",
            ],
        )

    def test_caps_chunks_without_dropping_tail(self) -> None:
        source = "\n\n".join(f"p{i}" for i in range(15))
        plan = plan_reply(source)
        self.assertEqual(len(plan.chunks), MAX_REPLY_CHUNKS)
        self.assertEqual(plan.chunks[-1].text, "\n\n".join(f"p{i}" for i in range(9, 15)))

    def test_parses_quote_handle_per_chunk(self) -> None:
        plan = plan_reply("[reply#42] 第一条\n\n[↩#51] 第二条")
        self.assertEqual(
            [(chunk.reply_message_id, chunk.text) for chunk in plan.chunks],
            [(42, "第一条"), (51, "第二条")],
        )

    def test_accepts_accidental_msg_prefix_in_reply_handle(self) -> None:
        plan = plan_reply("[reply#msg7674] 内容")

        self.assertEqual(plan.chunks[0].reply_message_id, 7674)
        self.assertEqual(plan.chunks[0].text, "内容")

    def test_strips_bold_markers_but_preserves_inline_code(self) -> None:
        plan = plan_reply("这是 **重点**，保留 `**literal**`")

        self.assertEqual(plan.chunks[0].text, "这是 重点，保留 `**literal**`")

    def test_extracts_reply_token_anywhere_but_not_from_code(self) -> None:
        plan = plan_reply(
            "先看 [reply#42: 原消息] 这一条\n\n"
            "`[reply#77]` 保持原样\n\n"
            "```text\n[reply#88]\n```"
        )

        self.assertEqual(plan.chunks[0].reply_message_id, 42)
        self.assertEqual(plan.chunks[0].text, "先看 这一条")
        self.assertEqual(plan.chunks[1].reply_message_id, None)
        self.assertEqual(plan.chunks[1].text, "`[reply#77]` 保持原样")
        self.assertEqual(plan.chunks[2].reply_message_id, None)
        self.assertIn("[reply#88]", plan.chunks[2].text)

    def test_exact_silence_can_name_a_reaction(self) -> None:
        plan = plan_reply("[reply#42] [silence:吃瓜]")
        self.assertTrue(plan.silence)
        self.assertEqual(plan.silence_reply_message_id, 42)
        self.assertEqual(plan.silence_face_id, 271)

    def test_unknown_silence_face_uses_default(self) -> None:
        plan = plan_reply("[silence:量子纠缠]")
        self.assertEqual(plan.silence_face_id, DEFAULT_SILENCE_FACE_ID)

    def test_marker_inside_real_answer_does_not_mute_it(self) -> None:
        plan = plan_reply("我不应该在句子中发 [silence]")
        self.assertFalse(plan.silence)


if __name__ == "__main__":
    unittest.main()
