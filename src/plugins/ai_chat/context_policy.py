from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .context_pipeline.models import ContextTokenBudget, TurnContextPlan
from .context_pipeline.router import (
    QuestionComplexity,
    RecallDecision,
    RecallMode,
    rule_recall_route,
)


ContextMode = Literal["minimal", "focused", "expanded"]

_ROSTER_REFERENCE = re.compile(
    r"(?:@|艾特|群里谁|谁说|群友|群成员|大家|他们|她们|有人)"
)


@dataclass(frozen=True)
class ContextPolicy:
    mode: ContextMode
    route: RecallMode
    complexity: QuestionComplexity
    include_recent_group: bool
    include_roster: bool
    include_pins: bool
    include_shared_sources: bool
    include_group_memory: bool
    include_user_memory: bool
    fallback_group_memory: bool = False
    fallback_user_memory: bool = False
    max_messages: int = 0
    max_chars: int = 0
    roster_limit: int = 0
    pin_max_chars: int = 0
    memory_max_entries_per_scope: int = 4
    memory_max_chars: int = 1200
    token_budget: ContextTokenBudget = ContextTokenBudget(
        focus=0,
        timeline=0,
        group_memory=0,
        user_memory=0,
        semantic=0,
        tool_reserve=0,
    )


def choose_context_policy(
    text: str,
    plan: TurnContextPlan | None,
    *,
    is_group: bool,
    recall_decision: RecallDecision | None = None,
    configured_max_tokens: int = 6000,
    model_max_input_tokens: int = 0,
) -> ContextPolicy:
    normalized = " ".join(str(text).split())
    decision = recall_decision or rule_recall_route(
        normalized,
        plan,
        is_group=is_group,
    )
    budget = _adaptive_budget(
        decision,
        configured_max_tokens=configured_max_tokens,
        model_max_input_tokens=model_max_input_tokens,
    )
    has_focus = plan is not None and plan.focus_message_id is not None
    if decision.mode in {"direct", "follow_up"} and has_focus:
        mode: ContextMode = "focused"
    elif decision.mode == "no_recall":
        mode = "minimal"
    else:
        mode = "expanded"

    max_messages = (
        min(max(budget.timeline // 70, 4), 30)
        if decision.include_recent_timeline and budget.timeline > 0
        else 0
    )
    max_chars = min(max(budget.timeline * 4, 400), 8000) if max_messages else 0
    include_roster = bool(
        is_group
        and (
            _ROSTER_REFERENCE.search(normalized)
            or decision.mode == "recent_group" and "谁" in normalized
        )
    )
    return ContextPolicy(
        mode=mode,
        route=decision.mode,
        complexity=decision.complexity,
        include_recent_group=bool(is_group and decision.include_recent_timeline),
        include_roster=include_roster,
        include_pins=bool(is_group and decision.include_pins),
        include_shared_sources=bool(
            is_group and decision.include_shared_sources
        ),
        include_group_memory=bool(
            is_group and decision.include_group_memory
        ),
        include_user_memory=decision.include_user_memory,
        fallback_group_memory=decision.mode == "group_memory",
        fallback_user_memory=decision.mode == "user_memory",
        max_messages=max_messages,
        max_chars=max_chars,
        roster_limit=12 if include_roster else 0,
        pin_max_chars=min(max(budget.semantic * 3, 400), 2400),
        memory_max_entries_per_scope=(
            8 if decision.complexity == "complex" else 4
        ),
        memory_max_chars=max(budget.group_memory + budget.user_memory, 0) * 4,
        token_budget=budget,
    )


def proactive_context_policy() -> ContextPolicy:
    return ContextPolicy(
        mode="expanded",
        route="recent_group",
        complexity="simple",
        include_recent_group=True,
        include_roster=False,
        include_pins=False,
        include_shared_sources=False,
        include_group_memory=False,
        include_user_memory=False,
        max_messages=8,
        max_chars=1200,
        token_budget=ContextTokenBudget(
            focus=0,
            timeline=800,
            group_memory=0,
            user_memory=0,
            semantic=0,
            tool_reserve=100,
        ),
    )


def _adaptive_budget(
    decision: RecallDecision,
    *,
    configured_max_tokens: int,
    model_max_input_tokens: int,
) -> ContextTokenBudget:
    configured = min(max(int(configured_max_tokens), 600), 64000)
    if model_max_input_tokens > 0:
        configured = min(configured, max(int(model_max_input_tokens * 0.22), 600))
    target = {
        "simple": 1100,
        "normal": 2600,
        "complex": 4800,
    }[decision.complexity]
    total = min(configured, target)
    weights: dict[RecallMode, tuple[float, float, float, float, float, float]] = {
        # focus, timeline, semantic, group memory, user memory, tool reserve
        "direct": (0.85, 0.10, 0.00, 0.00, 0.00, 0.05),
        "follow_up": (0.30, 0.25, 0.20, 0.10, 0.10, 0.05),
        "recent_group": (0.00, 0.55, 0.30, 0.10, 0.00, 0.05),
        "old_topic": (0.10, 0.10, 0.65, 0.10, 0.00, 0.05),
        "user_memory": (0.15, 0.00, 0.00, 0.00, 0.80, 0.05),
        "group_memory": (0.05, 0.10, 0.30, 0.50, 0.00, 0.05),
        "no_recall": (0.00, 0.00, 0.00, 0.00, 0.00, 0.05),
    }
    focus, timeline, semantic, group, user, tool = weights[decision.mode]
    return ContextTokenBudget(
        focus=int(total * focus),
        timeline=int(total * timeline),
        semantic=int(total * semantic),
        group_memory=int(total * group),
        user_memory=int(total * user),
        tool_reserve=max(int(total * tool), 64),
    )
