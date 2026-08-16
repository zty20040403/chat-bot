from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import ReferenceResolver
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.message_ir import MessageBody, TextNode


class ReferenceResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = MessageLedger(":memory:")
        self.addCleanup(self.ledger.close)
        self.resolver = ReferenceResolver()
        self.group_a = ConversationScope("onebot-v11", "group", "100")
        self.group_b = ConversationScope("onebot-v11", "group", "200")

    def record(
        self,
        scope: ConversationScope,
        native_id: int,
        user_id: int,
        sender: str,
        text: str,
        *,
        reply_to: int | None = None,
    ):
        return self.ledger.record_message(
            scope,
            native_message_id=str(native_id),
            sender_native_user_id=str(user_id),
            sender_display=sender,
            body=MessageBody((TextNode(0, text),)),
            occurred_at=1_000 + native_id,
            reply_to_native_message_id=(
                str(reply_to) if reply_to is not None else None
            ),
        )

    def test_follow_up_prefers_recent_question_from_other_member(self) -> None:
        question = self.record(
            self.group_a,
            1,
            7,
            "Alice",
            "PostgreSQL 和 SQLite 哪个适合机器人？",
        )
        comment = self.record(
            self.group_a,
            2,
            8,
            "Bob",
            "我觉得 PostgreSQL 更稳",
        )
        current = self.record(self.group_a, 3, 9, "Kenneth", "你觉得呢")

        plan = self.resolver.resolve(
            self.ledger,
            self.group_a,
            current_message_id=current.canonical_message_id,
            current_text="你觉得呢",
            current_native_user_id=9,
            now=current.occurred_at,
        )

        self.assertEqual(plan.focus_message_id, question.canonical_message_id)
        self.assertIn(comment.canonical_message_id, plan.related_message_ids)
        self.assertIn("recent_question", plan.reason_codes)
        self.assertIn("PostgreSQL", plan.rendered_context)

    def test_explicit_reply_has_absolute_priority(self) -> None:
        target = self.record(self.group_a, 1, 7, "Alice", "晚上吃面吗")
        self.record(self.group_a, 2, 8, "Bob", "服务器怎么配？")
        current = self.record(
            self.group_a,
            3,
            9,
            "Kenneth",
            "你觉得呢",
            reply_to=1,
        )

        plan = self.resolver.resolve(
            self.ledger,
            self.group_a,
            current_message_id=current.canonical_message_id,
            current_text="你觉得呢",
            current_native_user_id=9,
            now=current.occurred_at,
        )

        self.assertEqual(plan.focus_message_id, target.canonical_message_id)
        self.assertEqual(plan.confidence, 1.0)
        self.assertIn("explicit_reply", plan.reason_codes)

    def test_other_group_is_never_a_candidate(self) -> None:
        secret = self.record(
            self.group_b,
            1,
            7,
            "Alice",
            "另一个群的秘密问题是什么？",
        )
        local = self.record(self.group_a, 2, 8, "Bob", "本群今晚吃什么？")
        current = self.record(self.group_a, 3, 9, "Kenneth", "你觉得呢")

        plan = self.resolver.resolve(
            self.ledger,
            self.group_a,
            current_message_id=current.canonical_message_id,
            current_text="你觉得呢",
            current_native_user_id=9,
            now=current.occurred_at,
        )

        candidate_ids = {item.message_id for item in plan.candidates}
        self.assertEqual(plan.focus_message_id, local.canonical_message_id)
        self.assertNotIn(secret.canonical_message_id, candidate_ids)
        self.assertNotIn("秘密问题", plan.rendered_context)

    def test_standalone_question_does_not_force_a_focus(self) -> None:
        self.record(self.group_a, 1, 7, "Alice", "你们吃什么？")
        current = self.record(
            self.group_a,
            2,
            9,
            "Kenneth",
            "NixOS 怎么更新？",
        )

        plan = self.resolver.resolve(
            self.ledger,
            self.group_a,
            current_message_id=current.canonical_message_id,
            current_text="NixOS 怎么更新？",
            current_native_user_id=9,
            now=current.occurred_at,
        )

        self.assertIsNone(plan.focus_message_id)
        self.assertEqual(plan.reason_codes, ("standalone_message",))
        self.assertEqual(plan.rendered_context, "")

    def test_question_with_its_own_subject_is_not_a_follow_up(self) -> None:
        self.record(self.group_a, 1, 7, "Alice", "今晚吃什么？")
        current = self.record(
            self.group_a,
            2,
            9,
            "Kenneth",
            "你觉得 PostgreSQL 怎么样？",
        )

        plan = self.resolver.resolve(
            self.ledger,
            self.group_a,
            current_message_id=current.canonical_message_id,
            current_text="你觉得 PostgreSQL 怎么样？",
            current_native_user_id=9,
            now=current.occurred_at,
        )

        self.assertIsNone(plan.focus_message_id)
        self.assertEqual(plan.reason_codes, ("standalone_message",))


if __name__ == "__main__":
    unittest.main()
