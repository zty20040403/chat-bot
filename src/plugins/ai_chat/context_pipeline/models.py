from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextCandidate:
    message_id: int
    score: float
    reason_codes: tuple[str, ...]


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
    resolver_version: str = "reference-rules-v1"

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
            "candidates": [
                {
                    "message_id": item.message_id,
                    "score": item.score,
                    "reason_codes": list(item.reason_codes),
                }
                for item in self.candidates
            ],
            "resolver_version": self.resolver_version,
            "context_hash": self.context_hash,
        }
