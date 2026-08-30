from __future__ import annotations

import re
from dataclasses import dataclass

from .models import TurnContextPlan
from .recall import HybridRecallContext
from .router import RecallDecision


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    confidence: float
    reason_codes: tuple[str, ...]
    evidence_handles: tuple[str, ...]
    selected_topic: str
    clarification: str = ""
    warnings: tuple[str, ...] = ()

    def journal_payload(self) -> dict[str, object]:
        return {
            "sufficient": self.sufficient,
            "confidence": round(self.confidence, 4),
            "reason_codes": list(self.reason_codes),
            "evidence_handles": list(self.evidence_handles),
            "selected_topic": self.selected_topic[:500],
            "clarification": self.clarification,
            "warnings": list(self.warnings),
        }

    def prompt_contract(self, *, current_user: str) -> str:
        handles = "、".join(self.evidence_handles[:12]) or "无"
        topic = self.selected_topic or "当前独立问题"
        warnings = "；".join(self.warnings) or "无"
        return (
            "[回答证据边界]\n"
            f"召回话题：{topic[:500]}\n"
            f"允许使用的证据句柄：{handles}\n"
            f"当前发言人：{current_user}\n"
            f"时间或归属提醒：{warnings}\n"
            "回答必须围绕召回话题；不得把其他成员的经历、偏好或观点说成当前"
            "发言人的事实。证据没有覆盖的群聊历史不要猜；需要更多细节时可继续"
            "调用 context_search。"
        )


def assess_evidence(
    text: str,
    decision: RecallDecision,
    plan: TurnContextPlan | None,
    recall: HybridRecallContext | None,
    *,
    conversation_scope: str,
    group_memory_scope: str | None,
    user_memory_scope: str,
) -> EvidenceAssessment:
    candidates = list(recall.candidates if recall is not None else ())
    leakage = [
        item
        for item in candidates
        if not _scope_allowed(
            item.candidate.source,
            item.candidate.scope_key,
            conversation_scope=conversation_scope,
            group_memory_scope=group_memory_scope,
            user_memory_scope=user_memory_scope,
        )
    ]
    if leakage:
        return EvidenceAssessment(
            False,
            0.0,
            ("scope_violation",),
            (),
            plan.topic_query if plan is not None else "",
            "上下文范围校验没有通过，请重新引用你想讨论的那条消息。",
        )

    handles = tuple(
        dict.fromkeys(item.candidate.handle for item in candidates if item.candidate.handle)
    )
    selected_topic = (
        (plan.topic_query if plan is not None else "")
        or (recall.query if recall is not None else "")
        or " ".join(str(text).split())
    )
    if decision.mode == "no_recall":
        return EvidenceAssessment(
            True,
            decision.confidence,
            ("standalone_no_recall",),
            (),
            selected_topic,
        )

    warnings = _time_warnings(candidates)
    if decision.mode == "direct":
        enough = bool(plan and plan.focus_message_id is not None)
        clarification = "" if enough else "我没读到你引用的那条消息，可以重新引用一下吗？"
        return EvidenceAssessment(
            enough,
            1.0 if enough else 0.0,
            ("explicit_evidence",) if enough else ("missing_explicit_target",),
            handles,
            selected_topic,
            clarification,
            warnings,
        )

    if decision.mode == "follow_up":
        ambiguous = bool(
            plan is None
            or plan.focus_message_id is None
            or plan.confidence < 0.6
            or "no_reliable_focus" in plan.reason_codes
        )
        return EvidenceAssessment(
            True,
            plan.confidence if plan is not None else 0.0,
            ("topic_graph_focus",) if not ambiguous else ("best_effort_follow_up",),
            handles,
            selected_topic,
            "",
            warnings,
        )

    required_sources = {
        "recent_group": {
            "group_timeline",
            "relation_graph",
            "pinned_message",
            "shared_source",
            "media_summary",
        },
        "old_topic": {
            "raw_history",
            "historian_episode",
            "pinned_message",
            "shared_source",
            "media_summary",
        },
        "user_memory": {"user_memory"},
        "group_memory": {"group_memory", "pinned_message", "historian_episode"},
    }.get(decision.mode, set())
    matching = [
        item
        for item in candidates
        if item.candidate.source in required_sources and item.score >= 0.35
    ]
    if matching:
        confidence = min(max(matching[0].score, decision.confidence * 0.7), 1.0)
        return EvidenceAssessment(
            True,
            confidence,
            ("ranked_evidence", decision.mode),
            handles,
            selected_topic,
            warnings=warnings,
        )
    clarification = {
        "old_topic": "我没找到你说的旧话题，能给我一个关键词或引用那条消息吗？",
        "user_memory": "我没有找到对应的个人记忆，你指的是哪件事？",
        "group_memory": "我没有找到对应的群公共记录，你指的是哪项决定？",
        "recent_group": "你指的是刚才哪条消息？可以引用一下吗？",
    }.get(decision.mode, "你指的是刚才哪个问题？")
    return EvidenceAssessment(
        False,
        0.0,
        ("insufficient_evidence", decision.mode),
        handles,
        selected_topic,
        clarification,
        warnings,
    )


def _scope_allowed(
    source: str,
    scope_key: str,
    *,
    conversation_scope: str,
    group_memory_scope: str | None,
    user_memory_scope: str,
) -> bool:
    if source == "user_memory":
        return scope_key == user_memory_scope
    if source == "group_memory":
        return group_memory_scope is not None and scope_key == group_memory_scope
    return scope_key == conversation_scope


def _time_warnings(candidates: list[object]) -> tuple[str, ...]:
    years: set[str] = set()
    for ranked in candidates[:8]:
        content = str(getattr(getattr(ranked, "candidate", None), "content", ""))
        years.update(re.findall(r"(?:19|20)\d{2}", content))
    if len(years) > 1:
        return ("证据包含多个年份，回答时明确区分时间，不要合并成同一事件",)
    return ()
