from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline.evidence import assess_evidence
from src.plugins.ai_chat.context_pipeline.models import TurnContextPlan
from src.plugins.ai_chat.context_pipeline.ranking import RankedRecall, RecallCandidate
from src.plugins.ai_chat.context_pipeline.recall import HybridRecallContext
from src.plugins.ai_chat.context_pipeline.router import rule_recall_route


def _plan(*, focus: int | None, confidence: float, reasons: tuple[str, ...]):
    return TurnContextPlan(
        scope_key="onebot-v11:group:100",
        current_message_id=10,
        current_principal_id=9,
        focus_message_id=focus,
        confidence=confidence,
        reason_codes=reasons,
        related_message_ids=(),
        candidates=(),
        rendered_context="",
        topic_query="数据库切换",
    )


def _recall(*items: RankedRecall) -> HybridRecallContext:
    return HybridRecallContext(
        query="数据库切换",
        group_context="",
        memory_context="",
        candidates=tuple(items),
        semantic_available=True,
    )


class EvidenceGuardTests(unittest.TestCase):
    def test_ambiguous_follow_up_requests_clarification(self) -> None:
        plan = _plan(focus=None, confidence=0.0, reasons=("no_reliable_focus",))
        decision = rule_recall_route("你觉得呢", plan, is_group=True)

        result = assess_evidence(
            "你觉得呢",
            decision,
            plan,
            _recall(),
            conversation_scope="onebot-v11:group:100",
            group_memory_scope="group:100",
            user_memory_scope="group:100:user:9",
        )

        self.assertFalse(result.sufficient)
        self.assertIn("哪个问题", result.clarification)

    def test_wrong_users_memory_is_blocked_even_when_high_scoring(self) -> None:
        decision = rule_recall_route("你还记得我的偏好吗", None, is_group=True)
        candidate = RankedRecall(
            RecallCandidate(
                "memory#7",
                "user_memory",
                "group:100:user:8",
                "Bob 的私人偏好",
                semantic_score=1.0,
            ),
            0.99,
        )

        result = assess_evidence(
            "你还记得我的偏好吗",
            decision,
            None,
            _recall(candidate),
            conversation_scope="onebot-v11:group:100",
            group_memory_scope="group:100",
            user_memory_scope="group:100:user:9",
        )

        self.assertFalse(result.sufficient)
        self.assertIn("scope_violation", result.reason_codes)

    def test_old_topic_with_ranked_history_is_sufficient(self) -> None:
        decision = rule_recall_route("上周讨论的数据库方案", None, is_group=True)
        candidate = RankedRecall(
            RecallCandidate(
                "msg#7",
                "raw_history",
                "onebot-v11:group:100",
                "Alice: 数据库主库放在 h610",
                semantic_score=0.9,
            ),
            0.82,
        )

        result = assess_evidence(
            "上周讨论的数据库方案",
            decision,
            None,
            _recall(candidate),
            conversation_scope="onebot-v11:group:100",
            group_memory_scope="group:100",
            user_memory_scope="group:100:user:9",
        )

        self.assertTrue(result.sufficient)
        self.assertEqual(result.evidence_handles, ("msg#7",))
