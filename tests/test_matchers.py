from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from src.plugins.ai_chat.matchers import (
    _addressed_to_current_bot,
    _mentions_current_bot,
)


def _group_event(
    message: Message,
    *,
    user_id: int = 321,
    to_me: bool = False,
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=654,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={
            "user_id": user_id,
            "nickname": "Alice",
            "card": "",
            "role": "member",
        },
        group_id=789,
        to_me=to_me,
    )


class MentionRuleTests(unittest.TestCase):
    def test_bot_mention_in_middle_is_detected(self) -> None:
        message = Message(
            [
                MessageSegment.text("你觉得"),
                MessageSegment.at(999),
                MessageSegment.text("这个怎么样"),
            ]
        )

        self.assertTrue(_mentions_current_bot(_group_event(message)))
        self.assertTrue(_addressed_to_current_bot(_group_event(message)))
        self.assertEqual(message.extract_plain_text(), "你觉得这个怎么样")

    def test_original_to_me_routing_is_preserved(self) -> None:
        message = Message([MessageSegment.text("看看这个")])

        self.assertTrue(
            _addressed_to_current_bot(_group_event(message, to_me=True))
        )

    def test_other_mentions_and_self_messages_do_not_trigger(self) -> None:
        other = Message([MessageSegment.text("问"), MessageSegment.at(998)])
        own = Message([MessageSegment.at(999), MessageSegment.text("测试")])

        self.assertFalse(_mentions_current_bot(_group_event(other)))
        self.assertFalse(_mentions_current_bot(_group_event(own, user_id=999)))


if __name__ == "__main__":
    unittest.main()
