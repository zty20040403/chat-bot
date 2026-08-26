from __future__ import annotations

import json
import unittest
from pathlib import Path

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import ReferenceResolver
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.message_ir import MessageBody, TextNode


FIXTURE = Path(__file__).parent / "fixtures" / "context_accuracy_cases.json"


class ContextAccuracyEvaluationTests(unittest.TestCase):
    def test_real_follow_up_cases_reach_full_focus_accuracy(self) -> None:
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        correct = 0
        for case in cases:
            with self.subTest(case=case["name"]):
                ledger = MessageLedger(":memory:")
                self.addCleanup(ledger.close)
                scope = ConversationScope("onebot-v11", "group", "100")
                other_scope = ConversationScope("onebot-v11", "group", "200")
                for item in case.get("other_group_messages", []):
                    self._record(ledger, other_scope, item)
                native_to_canonical = {
                    item["id"]: self._record(ledger, scope, item).canonical_message_id
                    for item in case["messages"]
                }
                current = self._record(ledger, scope, case["current"])
                plan = ReferenceResolver().resolve(
                    ledger,
                    scope,
                    current_message_id=current.canonical_message_id,
                    current_text=case["current"]["text"],
                    current_native_user_id=case["current"]["user"],
                    now=current.occurred_at,
                )
                expected = native_to_canonical[case["expected_focus"]]
                if plan.focus_message_id == expected:
                    correct += 1
                self.assertEqual(plan.focus_message_id, expected)
                for required in case.get("required", []):
                    self.assertIn(required, plan.rendered_context)
                for forbidden in case.get("forbidden", []):
                    self.assertNotIn(forbidden, plan.rendered_context)
                self.assertEqual(plan.scope_key, scope.key)
        self.assertEqual(correct / len(cases), 1.0)

    @staticmethod
    def _record(
        ledger: MessageLedger,
        scope: ConversationScope,
        item: dict[str, object],
    ):
        return ledger.record_message(
            scope,
            native_message_id=str(item["id"]),
            sender_native_user_id=str(item["user"]),
            sender_display=str(item["sender"]),
            body=MessageBody((TextNode(0, str(item["text"])),)),
            occurred_at=1_000 + int(item["id"]),
            reply_to_native_message_id=(
                str(item["reply_to"]) if item.get("reply_to") is not None else None
            ),
        )


if __name__ == "__main__":
    unittest.main()
