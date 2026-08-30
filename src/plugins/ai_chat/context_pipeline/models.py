from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextCandidate:
    message_id: int
    score: float
    reason_codes: tuple[str, ...]
    source: str = "group_timeline"
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    relation_score: float = 0.0
    recency_score: float = 0.0


@dataclass(frozen=True)
class ContextTokenBudget:
    """Hard partitions prevent one context source from crowding out the rest."""

    focus: int
    timeline: int
    group_memory: int
    user_memory: int
    semantic: int
    tool_reserve: int = 0

    @property
    def total(self) -> int:
        return (
            self.focus
            + self.timeline
            + self.group_memory
            + self.user_memory
            + self.semantic
            + self.tool_reserve
        )


@dataclass(frozen=True)
class TurnContextPlan:
    scope_key: str
    current_message_id: int
    current_principal_id: int | None
    focus_message_id: int | None
    confidence: float
    reason_codes: tuple[str, ...]
    related_message_ids: tuple[int, ...]
    candidates: tuple[ContextCandidate, ...]
    rendered_context: str
    topic_id: int | None = None
    topic_message_ids: tuple[int, ...] = ()
    topic_query: str = ""
    resolver_version: str = "reference-graph-v2"

    @property
    def context_hash(self) -> str:
        return hashlib.sha256(
            self.rendered_context.encode("utf-8")
        ).hexdigest()[:16]

    def journal_payload(self) -> dict[str, object]:
        return {
            "scope_key": self.scope_key,
            "current_message_id": self.current_message_id,
            "current_principal_id": self.current_principal_id,
            "focus_message_id": self.focus_message_id,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "related_message_ids": list(self.related_message_ids),
            "topic_id": self.topic_id,
            "topic_message_ids": list(self.topic_message_ids),
            "topic_query": self.topic_query[:1000],
            "candidates": [
                {
                    "message_id": item.message_id,
                    "score": item.score,
                    "reason_codes": list(item.reason_codes),
                    "source": item.source,
                    "lexical_score": item.lexical_score,
                    "semantic_score": item.semantic_score,
                    "relation_score": item.relation_score,
                    "recency_score": item.recency_score,
                    "topic_id": (
                        self.topic_id
                        if item.message_id == self.focus_message_id
                        else None
                    ),
                    "topic_message_ids": (
                        list(self.topic_message_ids)
                        if item.message_id == self.focus_message_id
                        else []
                    ),
                    "topic_query": (
                        self.topic_query[:1000]
                        if item.message_id == self.focus_message_id
                        else ""
                    ),
                }
                for item in self.candidates
            ],
            "resolver_version": self.resolver_version,
            "context_hash": self.context_hash,
        }
