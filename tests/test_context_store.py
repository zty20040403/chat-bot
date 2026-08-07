from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_store import ContextStore
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.message_ir import MessageBody, TextNode


class ContextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = MessageLedger(":memory:")
        self.context = ContextStore(
            ":memory:",
            input_budget_tokens=1000,
            high_watermark_tokens=180,
            low_watermark_tokens=90,
            compartment_target_tokens=70,
            raw_tail_min_messages=3,
            max_compartments=10,
        )
        self.addCleanup(self.context.close)
        self.addCleanup(self.ledger.close)
        self.group_a = ConversationScope("onebot-v11", "group", "100")
        self.group_b = ConversationScope("onebot-v11", "group", "200")

    def seed(self, count: int = 18) -> None:
        for index in range(1, count + 1):
            self.ledger.record_message(
                self.group_a,
                native_message_id=str(index),
                sender_native_user_id=str(1000 + index % 2),
                sender_display=f"User {index % 2}",
                body=MessageBody(
                    (TextNode(0, f"project discussion {index} " + "x" * 200),)
                ),
                occurred_at=100 + index,
            )

    def test_materializes_exact_coverage_and_keeps_raw_tail(self) -> None:
        self.seed()

        projection = self.context.build_projection(self.ledger, self.group_a)
        valid, detail = self.context.verify_scope(self.ledger, self.group_a)

        self.assertTrue(valid, detail)
        self.assertGreater(projection.materialized_count, 0)
        self.assertTrue(projection.compartment_handles)
        self.assertTrue(projection.raw_message_ids)
        self.assertEqual(
            len(projection.compartment_handles),
            len(set(projection.compartment_handles)),
        )
        self.assertIn("conversation compartments", projection.text)
        self.assertIn("protected live tail", projection.text)

    def test_expand_rechecks_scope_and_source_hash(self) -> None:
        self.seed()
        projection = self.context.build_projection(self.ledger, self.group_a)
        handle = projection.compartment_handles[0]

        expanded = self.context.expand(self.ledger, self.group_a, handle)
        self.assertIn("exact evidence", expanded)
        self.assertIsNone(self.context.expand(self.ledger, self.group_b, handle))

        first_source = int(expanded.split("msg#", 1)[1].split("..", 1)[0])
        with self.ledger._transaction() as cursor:
            cursor.execute(
                """
                UPDATE messages SET body_json = ?
                WHERE canonical_message_id = ?
                """,
                (
                    '{"v":1,"nodes":[{"type":"text","index":0,'
                    '"text":"tampered source"}]}',
                    first_source,
                ),
            )
        self.assertIsNone(self.context.expand(self.ledger, self.group_a, handle))

    def test_clear_hides_compartments_and_old_raw_messages(self) -> None:
        self.seed()
        before = self.context.build_projection(self.ledger, self.group_a)
        handle = before.compartment_handles[0]

        self.ledger.hide_history(self.group_a)
        floor = self.ledger.visible_message_floor(self.group_a)
        hidden = self.context.hide_history(self.group_a, floor)
        after = self.context.build_projection(self.ledger, self.group_a)

        self.assertGreater(hidden, 0)
        self.assertEqual(after.text, "")
        self.assertIsNone(self.context.expand(self.ledger, self.group_a, handle))

    def test_current_message_can_be_excluded_without_losing_coverage(self) -> None:
        self.seed()
        projection = self.context.build_projection(
            self.ledger,
            self.group_a,
            exclude_native_message_id="18",
            protected_message_ids=(18,),
        )

        self.assertNotIn("discussion 18", projection.text)
        valid, detail = self.context.verify_scope(self.ledger, self.group_a)
        self.assertTrue(valid, detail)


if __name__ == "__main__":
    unittest.main()
