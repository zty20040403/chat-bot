"""Offline scoring or an explicitly requested, bounded live context benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.context_evaluation import case_variants, evaluation_report


async def live_predictions(cases, profile_name, embeddings):
    # Import the bot only inside isolated state, never against the production database.
    import nonebot
    nonebot.init()
    from src.plugins.ai_chat.config import settings
    from src.plugins.ai_chat.context_store import ContextStore
    from src.plugins.ai_chat.conversation_scope import ConversationScope
    from src.plugins.ai_chat.deepseek import ask_deepseek_json, DeepSeekTrace, _runtime
    from src.plugins.ai_chat.ledger import MessageLedger
    from src.plugins.ai_chat.message_ir import MessageBody, TextNode
    from src.plugins.ai_chat.semantic_recall import EmbeddingClient

    catalog, gateway = _runtime()
    profile = catalog.resolve_preference(profile_name)
    if profile_name != profile.name or not profile.configured:
        raise ValueError("Select an explicitly configured evaluation model profile")
    embedder = EmbeddingClient(
        base_url=settings.embedding_base_url, api_key=settings.embedding_api_key,
        model=settings.embedding_model, dimensions=settings.embedding_dimensions,
    ) if embeddings else None
    results = []
    try:
        for case in cases:
            ledger = MessageLedger(":memory:")
            store = ContextStore(":memory:", historian_managed=True, input_budget_tokens=6000)
            scope = ConversationScope("onebot-v11", "group", "100")
            ids = {}
            try:
                other_scope = ConversationScope("onebot-v11", "group", "200")
                for item in case.get("other_group_messages", []):
                    ledger.record_message(
                        other_scope, native_message_id=str(item["id"]),
                        sender_native_user_id=str(item["user"]), sender_display=item["sender"],
                        body=MessageBody((TextNode(0, item["text"]),)),
                        occurred_at=int(item.get("at") or 1000 + item["id"]),
                    )
                for item in case["messages"]:
                    record = ledger.record_message(
                        scope, native_message_id=str(item["id"]),
                        sender_native_user_id=str(item["user"]), sender_display=item["sender"],
                        body=MessageBody((TextNode(0, item["text"]),)),
                        occurred_at=int(item.get("at") or 1000 + item["id"]),
                        reply_to_native_message_id=str(item["reply_to"]) if item.get("reply_to") is not None else None,
                    )
                    ids[str(record.canonical_message_id)] = item["id"]
                projection = store.build_projection(ledger, scope, materialize=False).text
                vectors = await embedder.embed([item["text"] for item in case["messages"]]) if embedder else []
                for key, _, prompt in case_variants([case]):
                    retrieved = []
                    if embedder:
                        query = (await embedder.embed([prompt]))[0]
                        def cosine(vector):
                            norm = math.sqrt(sum(x*x for x in vector) * sum(x*x for x in query))
                            return sum(x*y for x,y in zip(vector, query)) / norm if norm else 0.0
                        retrieved = [case["messages"][index]["id"] for index in sorted(range(len(vectors)), key=lambda i: cosine(vectors[i]), reverse=True)[:5]]
                    trace = DeepSeekTrace()
                    try:
                        response = await ask_deepseek_json(
                            settings.system_prompt + "\n请正常回答当前群友。为评测另附结构化依据，只输出 JSON："
                            "answer（回答正文）、focus_id（关联的原始消息编号，无需历史则 null）、"
                            "evidence_ids（原始消息编号列表）、personal_facts（如涉及个人事实，记录 user_id、evidence_id）。"
                            "不要把其他群友的信息当作当前用户的信息。",
                            json.dumps({
                                "scope_key": scope.key, "timeline": projection,
                                "canonical_to_native_ids": ids,
                                "current": {**case["current"], "text": prompt},
                                "retrieved_messages": [item for item in case["messages"] if item["id"] in retrieved],
                            }, ensure_ascii=False),
                            profile=profile, trace=trace,
                        )
                        response.update(case_id=key, scope_key=scope.key, retrieved_ids=retrieved,
                                        actual_profile=trace.profile, total_tokens=trace.total_tokens)
                    except Exception as exc:
                        response = {"case_id": key, "error": type(exc).__name__}
                    results.append(response)
            finally:
                store.close()
                ledger.close()
    finally:
        await gateway.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT/"tests/fixtures/context_accuracy_cases.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--predictions", type=Path)
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--limit", type=int, default=5, help="Maximum fixture scenarios, not prompt variants")
    parser.add_argument("--embeddings", action="store_true", help="Call the configured BGE-M3 embedding endpoint")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    total_fixtures = len(cases)
    output = args.output.resolve()
    if args.live:
        if not args.profile or args.limit < 1:
            parser.error("--live requires --profile and a positive --limit")
        cases = cases[:args.limit]
        with tempfile.TemporaryDirectory(prefix="kennethbot-context-eval-") as temporary:
            os.chdir(temporary)
            os.environ.update(AI_POSTGRES_DSN="", AI_ALLOW_LEGACY_SQLITE="true", AI_STATE_DIR=temporary, AI_CACHE_DIR=temporary)
            predictions = asyncio.run(live_predictions(cases, args.profile, args.embeddings))
    else:
        captured = json.loads(args.predictions.read_text())
        predictions = captured["predictions"] if isinstance(captured, dict) else captured
    report = evaluation_report(cases, predictions)
    report.update(predictions=predictions, mode="live" if args.live else "captured",
                  embedding_evaluated=args.embeddings if args.live else None,
                  requested_profile=args.profile if args.live else None,
                  full_suite=len(cases) == total_fixtures,
                  total_fixtures=total_fixtures,
                  release_ready=report["passed"] and len(cases) == total_fixtures,
                  fixture_sha256=hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
                  created_at=datetime.now(timezone.utc).isoformat(), evaluation_version=1)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
