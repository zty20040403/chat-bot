from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import TurnContextPlan
from src.plugins.ai_chat.context_policy import (
    chronological_projection_budget,
    choose_context_policy,
)


def _plan(
    *,
    focus_message_id: int | None,
    reason_codes: tuple[str, ...],
) -> TurnContextPlan:
    return TurnContextPlan(
        scope_key="onebot-v11:group:100",
        current_message_id=10,
        current_principal_id=1,
        focus_message_id=focus_message_id,
        confidence=0.9 if focus_message_id else 0.0,
        reason_codes=reason_codes,
        related_message_ids=(8, 9) if focus_message_id else (),
        candidates=(),
        rendered_context="focus" if focus_message_id else "",
    )


class ContextPolicyTests(unittest.TestCase):
    def test_standalone_group_question_skips_recall(self) -> None:
        policy = choose_context_policy(
            "NixOS 怎么更新？",
            _plan(focus_message_id=None, reason_codes=("standalone_message",)),
            is_group=True,
        )

        self.assertEqual(policy.mode, "minimal")
        self.assertEqual(policy.route, "no_recall")
        self.assertFalse(policy.include_recent_group)
        self.assertEqual(policy.max_messages, 0)
        self.assertEqual(policy.max_chars, 0)
        self.assertFalse(policy.include_roster)
        self.assertFalse(policy.include_pins)

    def test_resolved_follow_up_uses_only_focused_context(self) -> None:
        policy = choose_context_policy(
            "你觉得呢",
            _plan(focus_message_id=7, reason_codes=("recent_question",)),
            is_group=True,
        )

        self.assertEqual(policy.mode, "focused")
        self.assertEqual(policy.route, "follow_up")
        self.assertTrue(policy.include_recent_group)
        self.assertGreater(
            policy.token_budget.focus,
            policy.token_budget.timeline,
        )

    def test_unresolved_or_group_reference_expands_recent_context(self) -> None:
        unresolved = choose_context_policy(
            "这个呢",
            _plan(focus_message_id=None, reason_codes=("no_reliable_focus",)),
            is_group=True,
        )
        roster = choose_context_policy(
            "刚才群里谁说要升级数据库？",
            _plan(focus_message_id=None, reason_codes=("standalone_message",)),
            is_group=True,
        )

        self.assertEqual(unresolved.mode, "expanded")
        self.assertGreaterEqual(unresolved.max_messages, 4)
        self.assertEqual(roster.mode, "expanded")
        self.assertTrue(roster.include_roster)

    def test_memory_fallback_is_explicit_and_scope_aware(self) -> None:
        personal = choose_context_policy(
            "你还记得我吗？",
            None,
            is_group=True,
        )
        group = choose_context_policy(
            "这个群之前定的群规是什么？",
            None,
            is_group=True,
        )

        self.assertTrue(personal.fallback_user_memory)
        self.assertFalse(personal.fallback_group_memory)
        self.assertTrue(group.fallback_group_memory)

    def test_complex_question_gets_more_budget_but_respects_model_window(self) -> None:
        simple = choose_context_policy(
            "刚才说啥",
            None,
            is_group=True,
            configured_max_tokens=6000,
        )
        complex_policy = choose_context_policy(
            "仔细分析刚才大家讨论的数据库方案，对比风险并给出完整实施步骤",
            None,
            is_group=True,
            configured_max_tokens=6000,
            model_max_input_tokens=8000,
        )

        self.assertGreater(complex_policy.token_budget.total, simple.token_budget.total)
        self.assertLessEqual(complex_policy.token_budget.total, 1760)

    def test_chronological_projection_uses_model_window_ceiling(self) -> None:
        self.assertEqual(
            chronological_projection_budget(
                64000,
                model_max_input_tokens=20000,
            ),
            8000,
        )
        self.assertEqual(
            chronological_projection_budget(
                64000,
                model_max_input_tokens=200000,
            ),
            32768,
        )
        self.assertEqual(chronological_projection_budget(12000), 12000)


if __name__ == "__main__":
    unittest.main()
