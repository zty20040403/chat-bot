"""Score captured answers against held-out conversation labels, without a model call."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


def case_variants(cases: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    return [
        (f"{case['name']}:{index}", case, prompt)
        for case in cases
        for index, prompt in enumerate(case.get("prompts", [case["current"]["text"]]))
    ]


@dataclass(frozen=True)
class AnswerScore:
    case_id: str
    category: str
    missing: bool
    focus_correct: bool | None
    recall_at_five: bool | None
    evidence_grounded: bool
    scope_leak: bool
    identity_error: bool
    answer_pass: bool
    reasons: tuple[str, ...]


def score_answer(case_id: str, case: dict[str, Any], prediction: dict[str, Any] | None) -> AnswerScore:
    prediction = prediction or {}
    reasons: list[str] = []
    answer = str(prediction.get("answer") or "").strip()
    missing = not answer or bool(prediction.get("error"))
    scope = str(case.get("scope_key", "onebot-v11:group:100"))
    expected = case.get("expected_focus")
    focus = prediction.get("focus_id")
    focus_evaluated = "expected_focus" in case and case.get("evaluation") != "recall"
    focus_correct = not missing and focus == expected if focus_evaluated else None
    if focus_correct is False:
        reasons.append("wrong_topic")
    visible_ids = {str(item["id"]) for item in case["messages"]}
    evidence = prediction.get("evidence_ids", [])
    valid_evidence = isinstance(evidence, list) and all(str(item) in visible_ids for item in evidence)
    grounded = valid_evidence and (expected is None or str(expected) in {str(item) for item in evidence})
    if not grounded:
        reasons.append("missing_or_unknown_evidence")
    scope_leak = prediction.get("scope_key", scope) != scope or any(
        text in answer for text in case.get("leak_markers", ["另一个群的机密"])
    )
    if scope_leak:
        reasons.append("cross_scope")
    identities = prediction.get("personal_facts", [])
    identity_error = not isinstance(identities, list)
    if isinstance(identities, list):
        messages = {str(item["id"]): item for item in case["messages"]}
        for fact in identities:
            if not isinstance(fact, dict):
                identity_error = True
                continue
            source = messages.get(str(fact.get("evidence_id")))
            if source is None or str(fact.get("user_id")) != str(source["user"]):
                identity_error = True
    if identity_error:
        reasons.append("wrong_personal_fact_owner")
    if missing:
        reasons.append("missing_answer")
    if any(str(marker) not in answer for marker in case.get("required", [])):
        reasons.append("missing_required_answer_content")
    if any(str(marker) in answer for marker in case.get("forbidden", [])):
        reasons.append("unrelated_answer_content")
    recall = None
    if case.get("evaluation") == "recall":
        hits = prediction.get("retrieved_ids", [])
        recall = isinstance(hits, list) and case["expected_recall"] in hits[:5]
        if not recall:
            reasons.append("recall_miss")
    return AnswerScore(
        case_id, str(case.get("category", "unknown")), missing, focus_correct,
        recall, grounded, scope_leak, identity_error, not reasons, tuple(reasons),
    )


def evaluation_report(cases: list[dict[str, Any]], predictions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    indexed: dict[str, dict[str, Any]] = {}
    variants = case_variants(cases)
    known = {key for key, _, _ in variants}
    for prediction in predictions:
        key = str(prediction.get("case_id", ""))
        if key not in known or key in indexed:
            raise ValueError(f"Unknown or duplicate evaluation case: {key}")
        indexed[key] = prediction
    scores = [score_answer(key, case, indexed.get(key)) for key, case, _ in variants]
    def rate(values):
        values = [value for value in values if value is not None]
        return sum(values) / len(values) if values else None
    focus = rate(item.focus_correct for item in scores)
    recall = rate(item.recall_at_five for item in scores)
    answer = rate(item.answer_pass for item in scores)
    metrics = {
        "fixture_count": len(cases), "prompt_count": len(scores),
        "answered": sum(not item.missing for item in scores),
        "focus_accuracy": focus, "recall_at_five": recall,
        "strict_answer_pass_rate": answer,
        "cross_scope_count": sum(item.scope_leak for item in scores),
        "wrong_personal_fact_count": sum(item.identity_error for item in scores),
        "unrelated_answer_rate": rate("unrelated_answer_content" in item.reasons for item in scores),
    }
    passed = (
        bool(scores) and metrics["answered"] == len(scores)
        and focus is not None and focus >= .9
        and (recall is None or recall >= .9)
        and answer is not None and answer >= .9
        and metrics["cross_scope_count"] == metrics["wrong_personal_fact_count"] == 0
        and metrics["unrelated_answer_rate"] < .05
    )
    return {"metrics": metrics, "passed": passed, "cases": [asdict(item) for item in scores]}
