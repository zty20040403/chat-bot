from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.context_pipeline import TurnContextPlan
from src.plugins.ai_chat.context_policy import choose_context_policy


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
    def test_standalone_group_question_uses_minimal_context(self) -> None:
        policy = choose_context_policy(
            "NixOS 怎么更新？",
            _plan(focus_message_id=None, reason_codes=("standalone_message",)),
            is_group=True,
        )

        self.assertEqual(policy.mode, "minimal")
        self.assertTrue(policy.include_recent_group)
        self.assertEqual(policy.max_messages, 6)
        self.assertEqual(policy.max_chars, 900)
        self.assertFalse(policy.include_roster)
        self.assertFalse(policy.include_pins)

    def test_resolved_follow_up_uses_only_focused_context(self) -> None:
        policy = choose_context_policy(
            "你觉得呢",
            _plan(focus_message_id=7, reason_codes=("recent_question",)),
            is_group=True,
        )

        self.assertEqual(policy.mode, "focused")
        self.assertFalse(policy.include_recent_group)

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
        self.assertEqual(unresolved.max_messages, 12)
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


if __name__ == "__main__":
    unittest.main()
