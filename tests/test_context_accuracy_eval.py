from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import (
    ContextTokenBudget,
    ReferenceResolver,
    build_hybrid_recall,
)
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.message_ir import MessageBody, MentionNode, TextNode
from src.plugins.ai_chat.semantic_recall import SemanticHit


FIXTURE = Path(__file__).parent / "fixtures" / "context_accuracy_cases.json"


class _NoMemories:
    @staticmethod
    def list_entries(_scopes):
        return []


class _FixtureSemanticRecall:
    def __init__(self, hits: list[SemanticHit]) -> None:
        self.hits = hits
        self.embedder = SimpleNamespace(model="BAAI/bge-m3")

    async def search(self, _scope_keys, _query, *, limit):
        return self.hits[:limit]


class ContextAccuracyEvaluationTests(unittest.TestCase):
    def test_context_release_gates(self) -> None:
        seeds = json.loads(FIXTURE.read_text(encoding="utf-8"))
        expanded = [
            (seed, prompt)
            for seed in seeds
            for prompt in seed.get("prompts", [seed["current"]["text"]])
        ]
        self.assertGreaterEqual(len(expanded), 100)

        focus_total = 0
        focus_correct = 0
        recall_total = 0
        recall_hits = 0
        irrelevant_injections = 0
        leakage_count = 0

        for seed, prompt in expanded:
            with self.subTest(case=seed["name"], prompt=prompt):
                result = self._evaluate(seed, prompt)
                focus_total += 1
                focus_correct += int(result["focus_correct"])
                recall_total += int(result["recall_evaluated"])
                recall_hits += int(result["recall_hit"])
                irrelevant_injections += int(result["irrelevant"])
                leakage_count += int(result["leaked"])

        focus_accuracy = focus_correct / max(focus_total, 1)
        recall_at_five = recall_hits / max(recall_total, 1)
        irrelevant_rate = irrelevant_injections / max(len(expanded), 1)
        self.assertGreaterEqual(focus_accuracy, 0.90)
        self.assertGreaterEqual(recall_at_five, 0.90)
        self.assertEqual(leakage_count, 0)
        self.assertLess(irrelevant_rate, 0.05)

    def _evaluate(self, seed: dict[str, object], prompt: str) -> dict[str, bool]:
        ledger = MessageLedger(":memory:")
        self.addCleanup(ledger.close)
        scope = ConversationScope("onebot-v11", "group", "100")
        other_scope = ConversationScope("onebot-v11", "group", "200")
        other_ids: list[int] = []
        for item in seed.get("other_group_messages", []):
            other_ids.append(
                self._record(ledger, other_scope, item).canonical_message_id
            )
        native_to_canonical = {
            item["id"]: self._record(ledger, scope, item).canonical_message_id
            for item in seed["messages"]
        }
        current_item = dict(seed["current"])
        current_item["text"] = prompt
        current = self._record(ledger, scope, current_item)
        plan = ReferenceResolver().resolve(
            ledger,
            scope,
            current_message_id=current.canonical_message_id,
            current_text=prompt,
            current_native_user_id=current_item["user"],
            now=current.occurred_at,
        )
        expected_native = seed.get("expected_focus")
        expected_focus = (
            native_to_canonical[int(expected_native)]
            if expected_native is not None
            else None
        )
        rendered = plan.rendered_context
        forbidden = [str(item) for item in seed.get("forbidden", [])]
        irrelevant = any(item in rendered for item in forbidden)
        leaked = any(
            marker in rendered
            for marker in seed.get("leak_markers", ["另一个群的机密"])
        )

        recall_evaluated = seed.get("evaluation") == "recall"
        recall_hit = False
        if recall_evaluated:
            expected_recall = native_to_canonical[int(seed["expected_recall"])]
            semantic_order = [
                native_to_canonical[int(item)] for item in seed["semantic_order"]
            ]
            hits = [
                SemanticHit(
                    scope.key,
                    "message",
                    f"msg#{message_id}",
                    "fixture",
                    max(0.97 - index * 0.03, 0.55),
                    {},
                )
                for index, message_id in enumerate(semantic_order)
            ]
            hits.insert(
                0,
                SemanticHit(
                    other_scope.key,
                    "message",
                    f"msg#{other_ids[0] if other_ids else 999999}",
                    "cross-scope decoy",
                    1.0,
                    {},
                ),
            )
            recalled = asyncio.run(
                build_hybrid_recall(
                    ledger=ledger,
                    scope=scope,
                    plan=plan,
                    user_text=prompt,
                    group_memory_scope="group:100",
                    user_memory_scope="group:100:user:9",
                    memory_store=_NoMemories(),
                    semantic_recall=_FixtureSemanticRecall(hits),
                    budget=ContextTokenBudget(500, 500, 200, 200, 800),
                    now=current.occurred_at,
                )
            )
            top_five = [item.candidate.handle for item in recalled.candidates[:5]]
            recall_hit = f"msg#{expected_recall}" in top_five
            leaked = leaked or any(
                item.candidate.scope_key == other_scope.key
                for item in recalled.candidates
            )
            irrelevant = irrelevant or any(
                item in (recalled.group_context + recalled.memory_context)
                for item in forbidden
            )

        for required in seed.get("required", []):
            if str(required) not in rendered and not recall_evaluated:
                return {
                    "focus_correct": False,
                    "recall_evaluated": bool(recall_evaluated),
                    "recall_hit": recall_hit,
                    "irrelevant": True,
                    "leaked": leaked,
                }
        return {
            "focus_correct": plan.focus_message_id == expected_focus,
            "recall_evaluated": bool(recall_evaluated),
            "recall_hit": recall_hit,
            "irrelevant": irrelevant,
            "leaked": leaked,
        }

    @staticmethod
    def _record(
        ledger: MessageLedger,
        scope: ConversationScope,
        item: dict[str, object],
    ):
        nodes = [TextNode(0, str(item["text"]))]
        for index, mention in enumerate(item.get("mentions", []), start=1):
            nodes.append(
                MentionNode(
                    index,
                    str(mention["user"]),
                    str(mention["display"]),
                )
            )
        return ledger.record_message(
            scope,
            native_message_id=str(item["id"]),
            sender_native_user_id=str(item["user"]),
            sender_display=str(item["sender"]),
            body=MessageBody(tuple(nodes)),
            occurred_at=int(item.get("at") or 1_000 + int(item["id"])),
            direction=str(item.get("direction") or "inbound"),
            reply_to_native_message_id=(
                str(item["reply_to"]) if item.get("reply_to") is not None else None
            ),
        )


if __name__ == "__main__":
    unittest.main()
