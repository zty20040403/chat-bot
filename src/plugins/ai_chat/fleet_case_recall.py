from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .semantic_recall import (
    SemanticDocument,
    SemanticIndexState,
    SemanticRecallService,
)


RUNBOOK_SCOPE = "fleet:runbooks"


def _case_document(item: Mapping[str, Any]) -> SemanticDocument | None:
    case_id = str(item.get("case_id") or "").strip()
    if not case_id or item.get("status") != "verified":
        return None
    resolution = item.get("resolution")
    if not isinstance(resolution, list):
        resolution = []
    content = "\n".join(
        part
        for part in (
            str(item.get("title") or "").strip(),
            str(item.get("symptoms") or "").strip(),
            str(item.get("confirmed_cause") or "").strip(),
            json.dumps(resolution, ensure_ascii=False, sort_keys=True),
            str(item.get("host_id") or "").strip(),
            str(item.get("service_ref") or "").strip(),
        )
        if part
    )
    if not content:
        return None
    return SemanticDocument(
        scope_key=RUNBOOK_SCOPE,
        source_type="fleet_runbook_case",
        source_handle=case_id,
        content=content,
        metadata={
            "case_id": case_id,
            "revision": int(item.get("revision") or 1),
            "host_id": str(item.get("host_id") or ""),
            "service_ref": str(item.get("service_ref") or ""),
        },
    )


async def semantic_runbook_scores(
    recall: SemanticRecallService | None,
    index_state: SemanticIndexState | None,
    cases: Sequence[Mapping[str, Any]],
    query: str,
    *,
    limit: int = 20,
) -> dict[str, float]:
    """Index changed verified cases and return BGE-ranked case handles."""
    if recall is None or index_state is None or not query.strip():
        return {}
    documents = [document for item in cases if (document := _case_document(item))]
    changed = index_state.changed(documents)
    for start in range(0, len(changed), 32):
        batch = changed[start:start + 32]
        await recall.index(batch)
        index_state.mark(batch)
    hits = await recall.search([RUNBOOK_SCOPE], query, limit=min(max(limit, 1), 50))
    return {
        hit.source_handle: float(hit.score)
        for hit in hits
        if hit.source_type == "fleet_runbook_case"
    }
