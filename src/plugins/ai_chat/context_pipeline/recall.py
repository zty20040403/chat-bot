from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..conversation_scope import ConversationScope
from ..ledger import MessageLedger
from .graph import topic_terms
from .models import ContextTokenBudget, TurnContextPlan
from .ranking import (
    HybridReranker,
    RankedRecall,
    RecallCandidate,
    combine_budgeted_sections,
    fit_token_budget,
)


_MESSAGE_HANDLE = re.compile(r"msg#([1-9][0-9]*)")
_MEMORY_HANDLE = re.compile(r"memory#([1-9][0-9]*)")
_EPISODE_HANDLE = re.compile(r"episode#([0-9a-fA-F-]{8,})")


@dataclass(frozen=True)
class HybridRecallContext:
    query: str
    group_context: str
    memory_context: str
    candidates: tuple[RankedRecall, ...]
    semantic_available: bool

    def journal_candidates(self) -> list[dict[str, object]]:
        return [
            {
                "handle": item.candidate.handle,
                "source": item.candidate.source,
                "score": item.score,
                "reason_codes": list(item.candidate.reason_codes),
                "lexical_score": item.candidate.lexical_score,
                "semantic_score": item.candidate.semantic_score,
                "relation_score": item.candidate.relation_score,
                "recency_score": item.candidate.recency_score,
            }
            for item in self.candidates
        ]


async def build_hybrid_recall(
    *,
    ledger: MessageLedger,
    scope: ConversationScope,
    plan: TurnContextPlan | None,
    user_text: str,
    group_memory_scope: str | None,
    user_memory_scope: str,
    memory_store: Any,
    context_store: Any = None,
    semantic_recall: Any = None,
    include_group_memory: bool = True,
    include_user_memory: bool = True,
    fallback_group_memory: bool = False,
    fallback_user_memory: bool = False,
    budget: ContextTokenBudget,
    semantic_timeout_seconds: float = 3.0,
    now: int | None = None,
) -> HybridRecallContext:
    """Retrieve only from the current group and current actor's memory scope."""

    query = " ".join(
        ((plan.topic_query if plan is not None else "") or user_text).split()
    ).strip()
    current_time = int(now or time.time())
    candidates: list[RecallCandidate] = []
    group_memories = (
        memory_store.list_entries([group_memory_scope])
        if include_group_memory and group_memory_scope is not None
        else []
    )
    user_memories = (
        memory_store.list_entries([user_memory_scope])
        if include_user_memory
        else []
    )
    for entry in group_memories:
        candidates.append(
            _memory_candidate(
                entry,
                "group_memory",
                current_time,
                fallback=fallback_group_memory,
            )
        )
    for entry in user_memories:
        candidates.append(
            _memory_candidate(
                entry,
                "user_memory",
                current_time,
                fallback=fallback_user_memory,
            )
        )

    allowed_scopes = {
        scope.key,
        *(item for item in (group_memory_scope, user_memory_scope) if item),
    }
    semantic_available = semantic_recall is not None
    semantic_hits: list[Any] = []
    if semantic_recall is not None and topic_terms(query):
        try:
            semantic_hits = await asyncio.wait_for(
                semantic_recall.search(
                    sorted(allowed_scopes),
                    query,
                    limit=18,
                ),
                timeout=max(float(semantic_timeout_seconds), 0.25),
            )
        except (
            asyncio.TimeoutError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            httpx.HTTPError,
        ):
            semantic_available = False

    memory_by_handle = {
        f"memory#{entry.id}": entry
        for entry in (*group_memories, *user_memories)
    }
    needs_episodes = any(hit.source_type == "episode" for hit in semantic_hits)
    active_episodes = {
        f"episode#{entry.expand_handle}": entry
        for entry in (
            context_store.active_compartments(limit=5000)
            if context_store is not None and needs_episodes
            else []
        )
        if entry.scope_key == scope.key
    }
    focused_handles = {
        f"msg#{message_id}"
        for message_id in (
            (
                plan.focus_message_id,
                *plan.related_message_ids,
            )
            if plan is not None
            else ()
        )
        if message_id is not None
    }
    for hit in semantic_hits:
        if str(hit.scope_key) not in allowed_scopes:
            continue
        candidate = _semantic_candidate(
            hit,
            ledger=ledger,
            scope=scope,
            memory_by_handle=memory_by_handle,
            active_episodes=active_episodes,
            focused_handles=focused_handles,
            now=current_time,
            group_memory_scope=group_memory_scope,
            user_memory_scope=user_memory_scope,
        )
        if candidate is not None:
            candidates.append(candidate)

    ranked = HybridReranker().rerank(query, candidates, limit=10)
    grouped: dict[str, list[RankedRecall]] = {}
    for item in ranked:
        grouped.setdefault(item.candidate.source, []).append(item)

    group_context = combine_budgeted_sections(
        [
            (
                "timeline",
                _render_ranked(
                    "[当前群相关历史消息]",
                    grouped.get("group_timeline", []),
                ),
                budget.semantic,
            ),
            (
                "episodes",
                _render_ranked(
                    "[当前群相关 Historian 摘要]",
                    grouped.get("historian_episode", []),
                ),
                budget.semantic,
            ),
        ],
        total_budget=budget.semantic,
    )
    memory_context = "\n\n".join(
        item
        for item in (
            fit_token_budget(
                _render_ranked(
                    "[当前群相关长期记忆]",
                    grouped.get("group_memory", []),
                ),
                budget.group_memory,
            ),
            fit_token_budget(
                _render_ranked(
                    "[当前用户相关长期记忆]",
                    grouped.get("user_memory", []),
                ),
                budget.user_memory,
            ),
        )
        if item
    )
    return HybridRecallContext(
        query=query,
        group_context=group_context,
        memory_context=memory_context,
        candidates=ranked,
        semantic_available=semantic_available,
    )


def _memory_candidate(
    entry: Any,
    source: str,
    now: int,
    *,
    fallback: bool,
) -> RecallCandidate:
    updated_at = int(entry.updated_at or entry.created_at or 0)
    return RecallCandidate(
        handle=f"memory#{entry.id}",
        source=source,  # type: ignore[arg-type]
        scope_key=str(entry.scope_key),
        content=str(entry.content),
        relation_score=0.9 if fallback else 0.0,
        recency_score=_recency(updated_at, now, horizon=90 * 86400),
        reason_codes=("explicit_memory_reference",) if fallback else ("lexical",),
        metadata={"memory_id": int(entry.id)},
    )


def _semantic_candidate(
    hit: Any,
    *,
    ledger: MessageLedger,
    scope: ConversationScope,
    memory_by_handle: dict[str, Any],
    active_episodes: dict[str, Any],
    focused_handles: set[str],
    now: int,
    group_memory_scope: str | None,
    user_memory_scope: str,
) -> RecallCandidate | None:
    handle = str(hit.source_handle)
    if hit.source_type == "message":
        matched = _MESSAGE_HANDLE.fullmatch(handle)
        if matched is None or handle in focused_handles or hit.scope_key != scope.key:
            return None
        message = ledger.get_in_scope(scope, int(matched.group(1)))
        if message is None:
            return None
        return RecallCandidate(
            handle=handle,
            source="group_timeline",
            scope_key=scope.key,
            content=message.prompt_text,
            semantic_score=float(hit.score),
            recency_score=_recency(message.occurred_at, now, horizon=7 * 86400),
            reason_codes=("semantic_recall", "same_scope"),
        )
    if hit.source_type == "episode":
        matched = _EPISODE_HANDLE.fullmatch(handle)
        episode = active_episodes.get(handle) if matched is not None else None
        if episode is None or hit.scope_key != scope.key:
            return None
        return RecallCandidate(
            handle=handle,
            source="historian_episode",
            scope_key=scope.key,
            content=episode.summary_p2 or episode.summary_p1,
            semantic_score=float(hit.score),
            recency_score=0.5,
            reason_codes=("semantic_recall", "active_episode", "same_scope"),
        )
    if hit.source_type == "memory":
        matched = _MEMORY_HANDLE.fullmatch(handle)
        entry = memory_by_handle.get(handle) if matched is not None else None
        if entry is None or hit.scope_key != entry.scope_key:
            return None
        if entry.scope_key == group_memory_scope:
            source = "group_memory"
        elif entry.scope_key == user_memory_scope:
            source = "user_memory"
        else:
            return None
        return RecallCandidate(
            handle=handle,
            source=source,  # type: ignore[arg-type]
            scope_key=str(entry.scope_key),
            content=str(entry.content),
            semantic_score=float(hit.score),
            recency_score=_recency(
                int(entry.updated_at or entry.created_at or 0),
                now,
                horizon=90 * 86400,
            ),
            reason_codes=("semantic_recall", "exact_memory_scope"),
        )
    return None


def _recency(timestamp: int, now: int, *, horizon: int) -> float:
    if timestamp <= 0 or now <= 0:
        return 0.0
    age = max(now - timestamp, 0)
    return max(1.0 - age / max(int(horizon), 1), 0.0)


def _render_ranked(title: str, items: list[RankedRecall]) -> str:
    if not items:
        return ""
    lines = [title]
    lines.extend(
        f"- [{item.candidate.handle}] {item.candidate.content}"
        for item in items
    )
    return "\n".join(lines)
