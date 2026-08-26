from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from ..context_store import estimate_tokens
from .graph import topic_terms


RecallSource = Literal[
    "group_timeline",
    "group_memory",
    "user_memory",
    "historian_episode",
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
    reason_codes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedRecall:
    candidate: RecallCandidate
    score: float


class HybridReranker:
    """Calibrates heterogeneous recall sources onto one relevance scale."""

    _SOURCE_PRIOR: dict[str, float] = {
        "group_timeline": 0.18,
        "group_memory": 0.16,
        "user_memory": 0.12,
        "historian_episode": 0.14,
    }
    _SOURCE_THRESHOLD: dict[str, float] = {
        "group_timeline": 0.40,
        "group_memory": 0.40,
        "user_memory": 0.43,
        "historian_episode": 0.48,
    }
    _SOURCE_LIMIT: dict[str, int] = {
        "group_timeline": 3,
        "group_memory": 3,
        "user_memory": 3,
        "historian_episode": 2,
    }

    def rerank(
        self,
        query: str,
        candidates: list[RecallCandidate],
        *,
        limit: int = 8,
    ) -> tuple[RankedRecall, ...]:
        query_terms = topic_terms(query)
        deduplicated: dict[str, RecallCandidate] = {}
        for candidate in candidates:
            content = " ".join(candidate.content.split()).strip()
            if not content:
                continue
            key = self._deduplication_key(candidate, content)
            existing = deduplicated.get(key)
            if existing is None or candidate.semantic_score > existing.semantic_score:
                deduplicated[key] = RecallCandidate(
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
                    reason_codes=candidate.reason_codes,
                    metadata=dict(candidate.metadata),
                )

        scored = [
            RankedRecall(candidate, self._score(candidate))
            for candidate in deduplicated.values()
        ]
        scored = [
            item
            for item in scored
            if item.score >= self._SOURCE_THRESHOLD[item.candidate.source]
        ]
        scored.sort(
            key=lambda item: (
                item.score,
                item.candidate.recency_score,
                item.candidate.handle,
            ),
            reverse=True,
        )

        selected: list[RankedRecall] = []
        source_counts: dict[str, int] = {}
        for item in scored:
            source = item.candidate.source
            if source_counts.get(source, 0) >= self._SOURCE_LIMIT[source]:
                continue
            adjusted = item.score - self._redundancy_penalty(item, selected)
            if adjusted < self._SOURCE_THRESHOLD[source]:
                continue
            selected.append(RankedRecall(item.candidate, round(adjusted, 4)))
            source_counts[source] = source_counts.get(source, 0) + 1
            if len(selected) >= min(max(int(limit), 1), 20):
                break
        return tuple(selected)

    def _score(self, candidate: RecallCandidate) -> float:
        return round(
            min(
                1.0,
                self._SOURCE_PRIOR[candidate.source]
                + 0.32 * candidate.relation_score
                + 0.30 * candidate.lexical_score
                + 0.28 * candidate.semantic_score
                + 0.10 * candidate.recency_score,
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
