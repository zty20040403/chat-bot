from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .context_pipeline import TurnContextPlan


ContextMode = Literal["minimal", "focused", "expanded"]

_GROUP_REFERENCE = re.compile(
    r"(?:群里|群友|大家|他们|她们|刚才|前面|上面|上一条|前[几两]条|"
    r"之前(?:说|聊|问)|有人说|谁说|这条消息|那条消息)"
)
_ROSTER_REFERENCE = re.compile(
    r"(?:@|艾特|群里谁|谁说|群友|群成员|大家|他们|她们|有人)"
)
_USER_MEMORY_REFERENCE = re.compile(
    r"(?:你还记得(?:我|我的|咱们|我们)|记得我|我(?:之前|上次|以前|一直|"
    r"通常|平时|喜欢|不喜欢|偏好)|关于我|我的(?:偏好|习惯|喜好|身份|配置)|"
    r"长期记忆|个人记忆|忘记我)"
)
_GROUP_MEMORY_REFERENCE = re.compile(
    r"(?:群规|群记忆|这个群(?:以前|之前)|群里(?:以前|之前)|"
    r"大家(?:以前|之前)|我们(?:以前|之前|上次)|固定消息|置顶消息)"
)


@dataclass(frozen=True)
class ContextPolicy:
    mode: ContextMode
    include_recent_group: bool
    include_roster: bool
    include_pins: bool
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


def choose_context_policy(
    text: str,
    plan: TurnContextPlan | None,
    *,
    is_group: bool,
) -> ContextPolicy:
    normalized = " ".join(str(text).split())
    user_memory_reference = bool(_USER_MEMORY_REFERENCE.search(normalized))
    group_memory_reference = bool(
        is_group and _GROUP_MEMORY_REFERENCE.search(normalized)
    )

    if not is_group:
        return ContextPolicy(
            mode="minimal",
            include_recent_group=False,
            include_roster=False,
            include_pins=False,
            include_group_memory=False,
            include_user_memory=True,
            fallback_user_memory=user_memory_reference,
        )

    has_focus = plan is not None and plan.focus_message_id is not None
    unresolved_follow_up = bool(
        plan is not None and "no_reliable_focus" in plan.reason_codes
    )
    group_reference = bool(_GROUP_REFERENCE.search(normalized))

    if has_focus:
        mode: ContextMode = "focused"
    elif unresolved_follow_up or group_reference:
        mode = "expanded"
    else:
        mode = "minimal"

    return ContextPolicy(
        mode=mode,
        include_recent_group=mode == "expanded",
        include_roster=bool(_ROSTER_REFERENCE.search(normalized)),
        include_pins=mode == "expanded" or group_memory_reference,
        include_group_memory=True,
        include_user_memory=True,
        fallback_group_memory=group_memory_reference,
        fallback_user_memory=user_memory_reference,
        max_messages=12 if mode == "expanded" else 0,
        max_chars=1800 if mode == "expanded" else 0,
        roster_limit=12,
        pin_max_chars=800,
    )


def proactive_context_policy() -> ContextPolicy:
    return ContextPolicy(
        mode="expanded",
        include_recent_group=True,
        include_roster=False,
        include_pins=False,
        include_group_memory=False,
        include_user_memory=False,
        max_messages=8,
        max_chars=1200,
    )
