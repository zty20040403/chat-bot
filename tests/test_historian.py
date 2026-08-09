from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_store import ContextStore
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.historian import (
    DreamOperation,
    DreamService,
    HistorianResult,
    HistorianService,
    MaintenanceState,
    MemoryProposal,
)
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.long_term_memory import LongTermMemoryStore
from src.plugins.ai_chat.message_ir import MessageBody, TextNode


class HistorianTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.ledger = MessageLedger(":memory:")
        self.context = ContextStore(
            ":memory:",
            input_budget_tokens=1000,
            high_watermark_tokens=180,
            low_watermark_tokens=90,
            compartment_target_tokens=70,
            raw_tail_min_messages=3,
        )
        self.memories = LongTermMemoryStore(root / "memory.json")
        self.addCleanup(self.context.close)
        self.addCleanup(self.ledger.close)
        self.scope = ConversationScope("onebot-v11", "group", "100")
        for index in range(1, 19):
            self.ledger.record_message(
                self.scope,
                native_message_id=str(index),
                sender_native_user_id="7",
                sender_display="Alice",
                body=MessageBody((TextNode(0, f"project fact {index} " + "x" * 180),)),
                occurred_at=100 + index,
            )

    def test_historian_publishes_and_adds_evidence_bound_memory(self) -> None:
        async def generate(candidate):
            return HistorianResult(
                "详细摘要",
                "中等摘要",
                "短摘要",
                (MemoryProposal("项目统一使用 Python", candidate.messages[0].canonical_message_id),),
            )

        run = asyncio.run(
            HistorianService(
                self.ledger,
                self.context,
                self.memories,
                generate,
            ).run_once()
        )
        self.assertEqual(run.published, 1)
        self.assertEqual(run.memories_added, 1)
        self.assertEqual(self.memories.all_entries()[0].scope_key, "group:100")

    def test_invalid_evidence_does_not_advance_cursor(self) -> None:
        async def generate(_candidate):
            return HistorianResult(
                "详细摘要",
                "中等摘要",
                "短摘要",
                (MemoryProposal("invalid", 999999),),
            )

        run = asyncio.run(
            HistorianService(
                self.ledger,
                self.context,
                self.memories,
                generate,
            ).run_once()
        )
        self.assertEqual(run.published, 0)
        self.assertTrue(run.failures)
        self.assertIsNotNone(self.context.capture_candidate(self.ledger, self.scope))

    def test_dream_uses_versioned_updates(self) -> None:
        entry, _ = self.memories.add(
            "group:100", "group", "旧说法", creator_user_id=1
        )

        async def consolidate(_scope, entries, _evidence):
            return [
                DreamOperation(
                    "update",
                    entries[0].id,
                    entries[0].version,
                    "新说法",
                    "newer evidence",
                )
            ]

        result = asyncio.run(
            DreamService(
                self.memories,
                consolidate,
                min_entries=2,
            ).run_once()
        )
        self.assertEqual(result["changed"], 0)
        self.memories.add("group:100", "group", "第二条", creator_user_id=1)
        result = asyncio.run(
            DreamService(
                self.memories,
                consolidate,
                min_entries=2,
            ).run_once()
        )
        self.assertEqual(result["changed"], 1)
        self.assertEqual(
            next(item for item in self.memories.all_entries() if item.id == entry.id).content,
            "新说法",
        )

    def test_maintenance_state_is_persistent(self) -> None:
        state = MaintenanceState(":memory:")
        self.addCleanup(state.close)
        self.assertFalse(state.completed("dream", "2026-08-09"))
        state.mark_completed("dream", "2026-08-09")
        self.assertTrue(state.completed("dream", "2026-08-09"))


if __name__ == "__main__":
    unittest.main()
