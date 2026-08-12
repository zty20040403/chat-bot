from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.model_output import (
    ModelFace,
    ModelMediaReference,
    ModelMention,
    ModelText,
    parse_model_output,
)


class ModelOutputParserTests(unittest.TestCase):
    def test_parses_current_and_historical_tokens(self) -> None:
        nodes = parse_model_output(
            "[mention#7] 来看 [@#8: 小李] 和 @#9 [face#14: 微笑] "
            "[image#42.1: 截图] [sticker#43.2]",
            roster=(("张三", 7), ("李四", 8), ("王五", 9)),
        )

        self.assertEqual(
            nodes,
            (
                ModelMention(7, "张三"),
                ModelText(" 来看 "),
                ModelMention(8, "小李"),
                ModelText(" 和 "),
                ModelMention(9, "王五"),
                ModelText(" "),
                ModelFace(14, "微笑"),
                ModelText(" "),
                ModelMediaReference("image", 42, 1, "截图"),
                ModelText(" "),
                ModelMediaReference("sticker", 43, 2, ""),
            ),
        )

    def test_rescues_display_name_and_uses_longest_match(self) -> None:
        nodes = parse_model_output(
            "请 @小明同学 过来，邮箱 a@小明同学.example 不处理",
            roster=(("小明", 1), ("小明同学", 2)),
        )

        self.assertEqual(nodes[1], ModelMention(2, "小明同学"))
        self.assertIn("a@小明同学.example", nodes[-1].text)  # type: ignore[union-attr]

    def test_ambiguous_display_name_stays_text(self) -> None:
        nodes = parse_model_output(
            "@小明 你们谁来",
            roster=(("小明", 1), ("小明", 2)),
        )

        self.assertEqual(nodes, (ModelText("@小明 你们谁来"),))

    def test_code_spans_do_not_execute_transport_tokens(self) -> None:
        source = "`[mention#7]`\n```text\n[face#14]\n[image#42.1]\n```"

        self.assertEqual(parse_model_output(source), (ModelText(source),))

    def test_self_mention_is_removed(self) -> None:
        nodes = parse_model_output(
            "[mention#1] 问 [mention#2] 去",
            roster=(("机器人", 1), ("张三", 2)),
            self_principal_id=1,
        )

        self.assertEqual(
            nodes,
            (ModelText(" 问 "), ModelMention(2, "张三"), ModelText(" 去")),
        )


if __name__ == "__main__":
    unittest.main()
