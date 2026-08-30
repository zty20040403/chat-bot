from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import TurnContextPlan
from src.plugins.ai_chat.context_pipeline.router import route_recall


def _plan(*, focus: int | None, reasons: tuple[str, ...], confidence: float = 0.9):
    return TurnContextPlan(
        scope_key="onebot-v11:group:100",
        current_message_id=10,
        current_principal_id=1,
        focus_message_id=focus,
        confidence=confidence if focus is not None else 0.0,
        reason_codes=reasons,
        related_message_ids=(7, 8) if focus is not None else (),
        candidates=(),
        rendered_context="focus" if focus is not None else "",
        topic_query="数据库切换",
    )


class RecallRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_rules_cover_all_high_value_routes_without_model(self) -> None:
        calls = 0

        async def classifier(_payload):
            nonlocal calls
            calls += 1
            return {"route": "no_recall", "confidence": 0.9}

        cases = (
            ("仔细看这个", _plan(focus=7, reasons=("explicit_reply",)), "direct"),
            ("你觉得呢", _plan(focus=7, reasons=("recent_question",)), "follow_up"),
            ("刚才群里说了什么", None, "recent_group"),
            ("上周讨论的数据库方案呢", None, "old_topic"),
            ("你还记得我的偏好吗", None, "user_memory"),
            ("群规之前怎么定的", None, "group_memory"),
            ("快速排序怎么写", None, "no_recall"),
        )
        for text, plan, expected in cases:
            decision = await route_recall(
                text,
                plan,
                is_group=True,
                classifier=classifier,
            )
            self.assertEqual(decision.mode, expected)
        self.assertEqual(calls, 0)

    async def test_ambiguous_short_message_uses_small_model(self) -> None:
        async def classifier(payload):
            self.assertEqual(payload["rule_route"], "no_recall")
            return {
                "route": "recent_group",
                "confidence": 0.84,
                "complexity": "simple",
            }

        decision = await route_recall(
            "咋样",
            None,
            is_group=True,
            classifier=classifier,
        )

        self.assertEqual(decision.mode, "recent_group")
        self.assertTrue(decision.used_model)

    async def test_model_cannot_invent_direct_without_explicit_reply(self) -> None:
        async def classifier(_payload):
            return {"route": "direct", "confidence": 0.99}

        decision = await route_recall(
            "咋样",
            None,
            is_group=True,
            classifier=classifier,
        )

        self.assertNotEqual(decision.mode, "direct")
