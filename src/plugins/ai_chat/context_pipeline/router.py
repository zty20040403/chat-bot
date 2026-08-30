from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .models import TurnContextPlan


RecallMode = Literal[
    "direct",
    "follow_up",
    "recent_group",
    "old_topic",
    "user_memory",
    "group_memory",
    "no_recall",
]
QuestionComplexity = Literal["simple", "normal", "complex"]
RecallClassifier = Callable[[dict[str, object]], Awaitable[Mapping[str, Any]]]

_ROUTES = frozenset(
    {
        "direct",
        "follow_up",
        "recent_group",
        "old_topic",
        "user_memory",
        "group_memory",
        "no_recall",
    }
)
_USER_MEMORY = re.compile(
    r"(?:你还记得我|记得我|关于我|我的(?:偏好|习惯|喜好|身份|配置)|"
    r"我(?:之前|上次|以前|平时|通常|一直)(?:喜欢|用|说|提|设置)|"
    r"我喜欢什么|我是谁|个人记忆|忘记我)"
)
_GROUP_MEMORY = re.compile(
    r"(?:群规|群记忆|这个群(?:以前|之前)|群里(?:以前|之前)(?:决定|约定|讨论)|"
    r"大家(?:以前|之前)(?:决定|约定)|我们(?:以前|上次)(?:决定|约定)|"
    r"共同项目|固定消息|置顶消息)"
)
_OLD_TOPIC = re.compile(
    r"(?:很久以前|几天前|上周|上个月|去年|前年|历史消息|旧消息|旧话题|"
    r"之前(?:聊|讨论|提|说|问|发)|上次(?:聊|讨论|提|说|问)|还记得(?:那|这))"
)
_RECENT_GROUP = re.compile(
    r"(?:刚才|前面|上面|上一条|前[几两]条|这几条|群里|群友|大家|"
    r"他们|她们|有人说|谁说|这条消息|那条消息)"
)
_FOLLOW_UP = re.compile(
    r"^(?:你觉得(?:呢|怎么样)?|你怎么看|你认为(?:呢|怎么样)?|"
    r"这个|那个|这|那|它|他|她|上面那个|然后呢|后来呢|为什么|为啥|"
    r"怎么说|怎么办|咋办|评价一下|锐评|细说|展开说说|接着说)"
    r"[吧呢吗？?。.！!]*$",
    re.IGNORECASE,
)
_COMPLEX = re.compile(
    r"(?:仔细|详细|深入|完整|全面|逐步|对比|比较|分析|总结|规划|设计|"
    r"实现|调试|排查|评审|代码|试卷|文件|PDF|视频|帖子|报告|方案)"
)


@dataclass(frozen=True)
class RecallDecision:
    mode: RecallMode
    confidence: float
    complexity: QuestionComplexity
    reason_codes: tuple[str, ...]
    include_graph: bool
    include_recent_timeline: bool
    include_semantic: bool
    include_group_memory: bool
    include_user_memory: bool
    include_pins: bool
    include_shared_sources: bool
    include_media: bool
    used_model: bool = False

    def journal_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "confidence": round(self.confidence, 4),
            "complexity": self.complexity,
            "reason_codes": list(self.reason_codes),
            "used_model": self.used_model,
            "sources": {
                "graph": self.include_graph,
                "recent_timeline": self.include_recent_timeline,
                "semantic": self.include_semantic,
                "group_memory": self.include_group_memory,
                "user_memory": self.include_user_memory,
                "pins": self.include_pins,
                "shared_sources": self.include_shared_sources,
                "media": self.include_media,
            },
        }


async def route_recall(
    text: str,
    plan: TurnContextPlan | None,
    *,
    is_group: bool,
    classifier: RecallClassifier | None = None,
) -> RecallDecision:
    """Route recall cheaply; consult a small model only for ambiguous turns."""

    rule = rule_recall_route(text, plan, is_group=is_group)
    if classifier is None or rule.confidence >= 0.8:
        return rule
    try:
        payload = await classifier(
            {
                "text": " ".join(str(text).split())[:1200],
                "is_group": bool(is_group),
                "rule_route": rule.mode,
                "rule_confidence": rule.confidence,
                "plan_confidence": plan.confidence if plan is not None else 0.0,
                "plan_reasons": list(plan.reason_codes) if plan is not None else [],
                "has_focus": bool(plan and plan.focus_message_id is not None),
            }
        )
    except Exception:
        return rule
    raw_mode = str(payload.get("route") or "").strip()
    if raw_mode not in _ROUTES:
        return rule
    mode: RecallMode = raw_mode  # type: ignore[assignment]
    if mode == "direct" and not (
        plan is not None and "explicit_reply" in plan.reason_codes
    ):
        mode = "follow_up" if plan and plan.focus_message_id else rule.mode
    if not is_group and mode in {"recent_group", "group_memory"}:
        mode = "user_memory" if mode == "group_memory" else "no_recall"
    complexity = _complexity(
        text,
        str(payload.get("complexity") or "").strip(),
    )
    try:
        confidence = min(max(float(payload.get("confidence") or 0.65), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.65
    return _decision(
        mode,
        confidence=max(confidence, 0.55),
        complexity=complexity,
        reasons=("small_model_route", f"rule:{rule.mode}"),
        used_model=True,
    )


def rule_recall_route(
    text: str,
    plan: TurnContextPlan | None,
    *,
    is_group: bool,
) -> RecallDecision:
    normalized = " ".join(str(text).split()).strip()
    complexity = _complexity(normalized)
    reasons = set(plan.reason_codes if plan is not None else ())
    if "explicit_reply" in reasons:
        return _decision("direct", 1.0, complexity, ("explicit_reply",))
    if _USER_MEMORY.search(normalized):
        return _decision("user_memory", 0.96, complexity, ("user_memory_reference",))
    if is_group and _GROUP_MEMORY.search(normalized):
        return _decision("group_memory", 0.96, complexity, ("group_memory_reference",))
    if _OLD_TOPIC.search(normalized):
        return _decision("old_topic", 0.93, complexity, ("old_topic_reference",))
    if plan is not None and plan.focus_message_id is not None:
        return _decision(
            "follow_up",
            max(plan.confidence, 0.82),
            complexity,
            ("resolved_follow_up", *plan.reason_codes[:3]),
        )
    if "no_reliable_focus" in reasons or _FOLLOW_UP.fullmatch(normalized):
        return _decision(
            "follow_up",
            0.92,
            complexity,
            ("ambiguous_follow_up",),
        )
    if is_group and _RECENT_GROUP.search(normalized):
        return _decision("recent_group", 0.9, complexity, ("recent_group_reference",))
    if not normalized:
        return _decision("follow_up", 0.88, "simple", ("empty_mention",))
    if len(normalized) >= 6:
        return _decision("no_recall", 0.88, complexity, ("standalone_question",))
    return _decision(
        "no_recall",
        0.55,
        complexity,
        ("ambiguous_short_message",),
    )


def _decision(
    mode: RecallMode,
    confidence: float,
    complexity: QuestionComplexity,
    reasons: tuple[str, ...],
    *,
    used_model: bool = False,
) -> RecallDecision:
    return RecallDecision(
        mode=mode,
        confidence=min(max(float(confidence), 0.0), 1.0),
        complexity=complexity,
        reason_codes=tuple(dict.fromkeys(reasons)),
        include_graph=mode in {"direct", "follow_up"},
        include_recent_timeline=mode in {"follow_up", "recent_group", "old_topic"},
        include_semantic=mode in {
            "follow_up",
            "old_topic",
            "user_memory",
            "group_memory",
        },
        include_group_memory=mode == "group_memory",
        include_user_memory=mode == "user_memory",
        include_pins=mode in {"recent_group", "old_topic", "group_memory"},
        include_shared_sources=mode in {"recent_group", "old_topic"},
        include_media=mode in {"recent_group", "old_topic"},
        used_model=used_model,
    )


def _complexity(text: str, hinted: str = "") -> QuestionComplexity:
    if hinted in {"simple", "normal", "complex"}:
        return hinted  # type: ignore[return-value]
    normalized = " ".join(str(text).split())
    question_count = normalized.count("？") + normalized.count("?")
    if len(normalized) >= 140 or question_count >= 3 or _COMPLEX.search(normalized):
        return "complex"
    if len(normalized) >= 45 or question_count >= 2:
        return "normal"
    return "simple"
