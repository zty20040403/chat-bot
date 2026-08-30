from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from ..context_store import estimate_tokens
from .graph import topic_terms


RecallSource = Literal[
    "relation_graph",
    "group_timeline",
    "raw_history",
    "group_memory",
    "user_memory",
    "historian_episode",
    "pinned_message",
    "shared_source",
    "media_summary",
]


@dataclass(frozen=True)
class RecallCandidate:
    handle: str
    source: RecallSource
    scope_key: str
    content: str
    relation_score: float = 0.0
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    recency_score: float = 0.0
    topic_score: float = 0.0
    importance_score: float = 0.0
    actor_score: float = 0.0
    topic_conflict: float = 0.0
    leakage_risk: float = 0.0
    reason_codes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedRecall:
    candidate: RecallCandidate
    score: float


@dataclass(frozen=True)
class RecallRankingDecision:
    candidate: RecallCandidate
    raw_score: float
    adjusted_score: float
    selected: bool
    decision_codes: tuple[str, ...]


@dataclass(frozen=True)
class RerankResult:
    selected: tuple[RankedRecall, ...]
    decisions: tuple[RecallRankingDecision, ...]


class HybridReranker:
    """Calibrates heterogeneous recall sources onto one relevance scale."""

    _SOURCE_PRIOR: dict[str, float] = {
        "relation_graph": 0.24,
        "group_timeline": 0.18,
        "raw_history": 0.13,
        "group_memory": 0.16,
        "user_memory": 0.12,
        "historian_episode": 0.14,
        "pinned_message": 0.20,
        "shared_source": 0.14,
        "media_summary": 0.12,
    }
    _SOURCE_THRESHOLD: dict[str, float] = {
        "relation_graph": 0.36,
        "group_timeline": 0.40,
        "raw_history": 0.40,
        "group_memory": 0.40,
        "user_memory": 0.43,
        "historian_episode": 0.42,
        "pinned_message": 0.38,
        "shared_source": 0.40,
        "media_summary": 0.42,
    }
    _SOURCE_LIMIT: dict[str, int] = {
        "relation_graph": 6,
        "group_timeline": 3,
        "raw_history": 4,
        "group_memory": 3,
        "user_memory": 3,
        "historian_episode": 3,
        "pinned_message": 3,
        "shared_source": 3,
        "media_summary": 2,
    }

    def rerank(
        self,
        query: str,
        candidates: list[RecallCandidate],
        *,
        limit: int = 8,
        relative_threshold: float = 0.58,
    ) -> tuple[RankedRecall, ...]:
        return self.rerank_with_audit(
            query,
            candidates,
            limit=limit,
            relative_threshold=relative_threshold,
        ).selected

    def rerank_with_audit(
        self,
        query: str,
        candidates: list[RecallCandidate],
        *,
        limit: int = 8,
        relative_threshold: float = 0.58,
    ) -> RerankResult:
        query_terms = topic_terms(query)
        deduplicated: dict[str, RecallCandidate] = {}
        decisions: list[RecallRankingDecision] = []
        for candidate in candidates:
            content = " ".join(candidate.content.split()).strip()
            if not content:
                decisions.append(
                    RecallRankingDecision(
                        candidate,
                        0.0,
                        0.0,
                        False,
                        ("empty_content",),
                    )
                )
                continue
            if candidate.leakage_risk >= 0.5:
                decisions.append(
                    RecallRankingDecision(
                        candidate,
                        self._score(candidate),
                        0.0,
                        False,
                        ("leakage_risk",),
                    )
                )
                continue
            key = self._deduplication_key(candidate, content)
            existing = deduplicated.get(key)
            normalized = RecallCandidate(
                handle=candidate.handle,
                source=candidate.source,
                scope_key=candidate.scope_key,
                content=content,
                relation_score=self._bounded(candidate.relation_score),
                lexical_score=max(
                    self._bounded(candidate.lexical_score),
                    self._lexical_score(query_terms, content),
                ),
                semantic_score=self._bounded(candidate.semantic_score),
                recency_score=self._bounded(candidate.recency_score),
                topic_score=max(
                    self._bounded(candidate.topic_score),
                    self._topic_score(query_terms, content),
                ),
                importance_score=self._bounded(candidate.importance_score),
                actor_score=self._bounded(candidate.actor_score),
                topic_conflict=self._bounded(candidate.topic_conflict),
                leakage_risk=self._bounded(candidate.leakage_risk),
                reason_codes=candidate.reason_codes,
                metadata=dict(candidate.metadata),
            )
            if existing is None:
                deduplicated[key] = normalized
                continue
            decisions.append(
                RecallRankingDecision(
                    normalized,
                    self._score(normalized),
                    0.0,
                    False,
                    ("merged_duplicate",),
                )
            )
            preferred = (
                existing
                if self._SOURCE_PRIOR[existing.source]
                >= self._SOURCE_PRIOR[normalized.source]
                else normalized
            )
            deduplicated[key] = RecallCandidate(
                handle=preferred.handle,
                source=preferred.source,
                scope_key=preferred.scope_key,
                content=(
                    existing.content
                    if len(existing.content) >= len(normalized.content)
                    else normalized.content
                ),
                relation_score=max(existing.relation_score, normalized.relation_score),
                lexical_score=max(existing.lexical_score, normalized.lexical_score),
                semantic_score=max(existing.semantic_score, normalized.semantic_score),
                recency_score=max(existing.recency_score, normalized.recency_score),
                topic_score=max(existing.topic_score, normalized.topic_score),
                importance_score=max(
                    existing.importance_score,
                    normalized.importance_score,
                ),
                actor_score=max(existing.actor_score, normalized.actor_score),
                topic_conflict=min(existing.topic_conflict, normalized.topic_conflict),
                leakage_risk=max(existing.leakage_risk, normalized.leakage_risk),
                reason_codes=tuple(
                    dict.fromkeys((*existing.reason_codes, *normalized.reason_codes))
                ),
                metadata={**existing.metadata, **normalized.metadata},
            )

        scored_all = [
            RankedRecall(candidate, self._score(candidate))
            for candidate in deduplicated.values()
        ]
        scored: list[RankedRecall] = []
        for item in scored_all:
            if item.score < self._SOURCE_THRESHOLD[item.candidate.source]:
                decisions.append(
                    RecallRankingDecision(
                        item.candidate,
                        item.score,
                        item.score,
                        False,
                        ("below_source_threshold",),
                    )
                )
                continue
            scored.append(item)
        scored.sort(
            key=lambda item: (
                item.score,
                item.candidate.recency_score,
                item.candidate.handle,
            ),
            reverse=True,
        )
        if not scored:
            return RerankResult((), tuple(decisions))
        relative_floor = scored[0].score * min(
            max(float(relative_threshold), 0.45),
            0.9,
        )

        selected: list[RankedRecall] = []
        source_counts: dict[str, int] = {}
        for item in scored:
            source = item.candidate.source
            if len(selected) >= min(max(int(limit), 1), 20):
                decisions.append(
                    RecallRankingDecision(
                        item.candidate,
                        item.score,
                        item.score,
                        False,
                        ("global_limit_reached",),
                    )
                )
                continue
            if source_counts.get(source, 0) >= self._SOURCE_LIMIT[source]:
                decisions.append(
                    RecallRankingDecision(
                        item.candidate,
                        item.score,
                        item.score,
                        False,
                        ("source_limit_reached",),
                    )
                )
                continue
            redundancy_penalty = self._redundancy_penalty(item, selected)
            adjusted = item.score - redundancy_penalty
            if adjusted < max(self._SOURCE_THRESHOLD[source], relative_floor):
                reason = (
                    "redundancy_penalty"
                    if redundancy_penalty > 0
                    else "below_relative_threshold"
                )
                decisions.append(
                    RecallRankingDecision(
                        item.candidate,
                        item.score,
                        round(adjusted, 4),
                        False,
                        (reason,),
                    )
                )
                continue
            selected_item = RankedRecall(item.candidate, round(adjusted, 4))
            selected.append(selected_item)
            source_counts[source] = source_counts.get(source, 0) + 1
            decisions.append(
                RecallRankingDecision(
                    item.candidate,
                    item.score,
                    selected_item.score,
                    True,
                    (
                        "selected",
                        "source_threshold_pass",
                        "relative_threshold_pass",
                    ),
                )
            )
        decisions.sort(
            key=lambda item: (
                item.selected,
                item.adjusted_score,
                item.raw_score,
                item.candidate.handle,
            ),
            reverse=True,
        )
        return RerankResult(tuple(selected), tuple(decisions))

    def _score(self, candidate: RecallCandidate) -> float:
        return round(
            min(
                1.0,
                self._SOURCE_PRIOR[candidate.source]
                + 0.24 * candidate.relation_score
                + 0.22 * candidate.semantic_score
                + 0.16 * candidate.lexical_score
                + 0.12 * candidate.topic_score
                + 0.08 * candidate.recency_score
                + 0.08 * candidate.importance_score
                + 0.06 * candidate.actor_score
                - 0.24 * candidate.topic_conflict
                - 0.70 * candidate.leakage_risk,
            ),
            4,
        )

    @staticmethod
    def _bounded(value: float) -> float:
        return min(max(float(value), 0.0), 1.0)

    @staticmethod
    def _lexical_score(query_terms: set[str], content: str) -> float:
        if not query_terms:
            return 0.0
        overlap = len(query_terms.intersection(topic_terms(content)))
        return min(overlap / min(max(len(query_terms), 1), 6), 1.0)

    @staticmethod
    def _topic_score(query_terms: set[str], content: str) -> float:
        if not query_terms:
            return 0.0
        content_terms = topic_terms(content)
        union = query_terms.union(content_terms)
        if not union:
            return 0.0
        return min(len(query_terms.intersection(content_terms)) / len(query_terms), 1.0)

    @staticmethod
    def _deduplication_key(candidate: RecallCandidate, content: str) -> str:
        if candidate.handle:
            return f"{candidate.scope_key}:{candidate.handle}"
        digest = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()
        return f"{candidate.scope_key}:{digest[:16]}"

    @staticmethod
    def _redundancy_penalty(
        item: RankedRecall,
        selected: list[RankedRecall],
    ) -> float:
        terms = topic_terms(item.candidate.content)
        if not terms:
            return 0.0
        maximum = 0.0
        for existing in selected:
            other = topic_terms(existing.candidate.content)
            union = terms.union(other)
            if not union:
                continue
            maximum = max(maximum, len(terms.intersection(other)) / len(union))
        return 0.16 if maximum >= 0.82 else 0.08 if maximum >= 0.62 else 0.0


def fit_token_budget(text: str, budget: int) -> str:
    bounded = max(int(budget), 0)
    if bounded <= 0 or not text.strip():
        return ""
    if estimate_tokens(text) <= bounded:
        return text.strip()
    lines: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = estimate_tokens(line + "\n")
        if lines and used + cost > bounded:
            break
        if not lines and cost > bounded:
            ratio = bounded / max(cost, 1)
            return line[: max(int(len(line) * ratio), 1)].rstrip()
        lines.append(line)
        used += cost
    return "\n".join(lines).strip()


def combine_budgeted_sections(
    sections: list[tuple[str, str, int]],
    *,
    total_budget: int,
) -> str:
    remaining = max(int(total_budget), 0)
    rendered: list[str] = []
    for _name, text, partition in sections:
        separator_cost = estimate_tokens("\n\n") if rendered else 0
        if remaining <= separator_cost:
            break
        available = remaining - separator_cost
        fitted = fit_token_budget(
            text,
            min(max(int(partition), 0), available),
        )
        if not fitted:
            continue
        rendered.append(fitted)
        remaining -= separator_cost + estimate_tokens(fitted)
    return "\n\n".join(rendered)
