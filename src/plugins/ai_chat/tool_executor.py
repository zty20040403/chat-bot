"""Tool Executor responsibilities extracted from the plugin entrypoint."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import (
    Any,
    Awaitable,
    Callable,
)
import httpx
from src.bot_storage import (
    DatabaseError,
)
from nonebot import (
    logger,
)
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.exception import (
    ActionFailed,
)
from nonebot.exception import (
    NetworkError,
)
from .agent_tools import (
    AGENT_TOOL_PROMPT,
    AgentToolExecutor,
)
from .ai_tools import (
    CONTEXT_EXPAND_TOOL_NAME,
    CONTEXT_SEARCH_TOOL_NAME,
    FIND_STICKERS_TOOL_NAME,
    GROUP_MEMBERS_TOOL_NAME,
    INSPECT_SOURCE_TOOL_NAME,
    MEMORY_ADD_TOOL_NAME,
    MEMORY_LIST_TOOL_NAME,
    MEMORY_REMOVE_TOOL_NAME,
    PIN_MESSAGE_TOOL_NAME,
    READ_IMAGE_TEXT_TOOL_NAME,
    REPLY_WITH_VOICE_TOOL_NAME,
    SEND_QQ_FACE_TOOL_NAME,
    SEND_STICKER_TOOL_NAME,
    TRANSCRIBE_VOICE_TOOL_NAME,
    REMINDER_CANCEL_TOOL_NAME,
    REMINDER_LIST_TOOL_NAME,
    REMINDER_SET_TOOL_NAME,
    UNPIN_MESSAGE_TOOL_NAME,
    USE_SKILL_TOOL_NAME,
    VIEW_IMAGE_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    available_tools,
    force_tool,
)
from .config import (
    settings,
)
from .context_policy import (
    choose_context_policy,
)
from .context_pipeline import (
    TurnContextPlan,
    build_hybrid_recall,
    fit_token_budget,
)
from .context_pipeline.ranking import combine_budgeted_sections
from .deepseek import (
    AgentLoopEvent,
    DeepSeekTrace,
    DeepSeekConfigError,
    FinalStreamState,
    ask_deepseek_with_tools,
)
from .long_term_memory import (
    LongTermMemoryError,
)
from .media_library import (
    choose_sticker_candidate,
    requests_sticker_variation,
)
from .model_catalog import (
    ModelProfile,
)
from .observability import (
    telemetry,
)
from .onebot_codec import (
    scope_from_event,
)
from .onebot_model_output import (
    decode_group_members,
)
from .output_planner import (
    face_prompt_table,
    plan_reply,
)
from .ocr import (
    OCRError,
    recognize_images,
    reply_message_id,
)
from .stickers import (
    ai_reply_message,
    choose_ai_reply_kaomoji,
    qq_face_message,
    random_sticker_message,
)
from .turn_journal import (
    tool_catalog_fingerprint,
)
from .web_search import (
    SearchError,
    SearchResult,
    render_search_sources,
    search_freshness,
    search_web,
)
from .voice import (
    VoiceError,
    contains_voice,
    synthesize_silk_voice,
    transcribe_voice,
)


async def _ask_ai(
    bot: Bot,
    event: MessageEvent,
    user_text: str,
    force_search: bool = False,
    force_ocr: bool = False,
    force_voice_reply: bool = False,
    force_voice_transcription: bool = False,
    available_image_sources: list[str] | None = None,
    available_voice_message_id: int | None = None,
    journal_turn_id: int | None = None,
    turn_trace: DeepSeekTrace | None = None,
    turn_context: str = "",
    selected_model_override: str | None = None,
    selected_profile_override: ModelProfile | None = None,
    feedback_provider: Callable[[], Awaitable[list[str]]] | None = None,
    final_stream_sink: Callable[[str], Awaitable[None]] | None = None,
    final_stream_state: FinalStreamState | None = None,
) -> Message | str:
    if isinstance(event, GroupMessageEvent) and not _is_group_enabled(event.group_id):
        return "这个群暂时没有开启 AI。"

    if len(user_text) > settings.max_input_chars:
        return f"问题太长了，先压到 {settings.max_input_chars} 个字符以内。"

    if force_search and not settings.search_enabled:
        return "联网搜索暂时没有开启。"
    if force_ocr and not (settings.ocr_enabled or vision_worker is not None):
        return "图片理解暂时没有开启。"
    if (force_voice_reply or force_voice_transcription) and not settings.voice_enabled:
        return "语音功能暂时没有开启。"

    conversation_id = _conversation_id(event)
    sandbox_tools_enabled = (
        isinstance(event, GroupMessageEvent)
        and settings.is_sandbox_user_allowed(event.user_id)
    )
    agent_executor_enabled = isinstance(event, GroupMessageEvent)
    agent_executor = (
        AgentToolExecutor(
            bot=bot,
            event=event,
            owner=conversation_id,
            sandbox_manager=sandbox_manager,
            max_file_bytes=settings.sandbox_max_file_bytes,
            ledger=message_ledger,
            scope=_conversation_scope(event),
            turn_journal=turn_journal,
            turn_id=journal_turn_id,
            browser_manager=browser_manager,
            source_store=source_store,
            video_analyzer=video_analyzer,
        )
        if agent_executor_enabled and isinstance(event, GroupMessageEvent)
        else None
    )
    selected_profile = selected_profile_override or _preferred_model_profile(
        conversation_id
    )
    if selected_model_override:
        configured_override = model_profiles.try_resolve(selected_model_override)
        selected_profile = configured_override or selected_profile.with_model(
            selected_model_override
        )
    search_results: list[SearchResult] = []
    used_ocr_texts: list[str] = []
    used_voice_texts: list[str] = []
    voice_reply_segment: MessageSegment | None = None
    voice_reply_text = ""
    visual_reply_segment: MessageSegment | None = None
    sticker_handles_this_turn: set[str] = set()
    replay_prefix: list[dict[str, Any]] = []
    replay_covered_message_ids: tuple[int, ...] = ()
    replay_digest_prefix = ""
    replay_reason = ""
    context_plan: TurnContextPlan | None = None

    if available_image_sources is None and settings.ocr_enabled:
        available_image_sources = await _resolve_ocr_sources(bot, event)
    available_image_sources = available_image_sources or []

    should_resolve_voice = (
        force_voice_transcription
        or contains_voice(event.original_message)
        or reply_message_id(event.original_message) is not None
        or recent_voices.get(_voice_cache_key(event)) is not None
    )
    if (
        available_voice_message_id is None
        and settings.voice_enabled
        and should_resolve_voice
    ):
        available_voice_message_id = await _resolve_voice_message_id(bot, event)

    tools = available_tools(
        include_web_search=(
            settings.search_enabled
            and (force_search or settings.search_auto_enabled)
        ),
        include_image_ocr=(
            settings.ocr_enabled and bool(available_image_sources)
        ),
        include_voice_transcription=(
            settings.voice_enabled and available_voice_message_id is not None
        ),
        include_voice_reply=settings.voice_enabled,
        include_stickers=True,
        include_memory_tools=True,
        include_agent_tools=sandbox_tools_enabled,
        include_conversation_tools=isinstance(event, GroupMessageEvent),
        include_browser_tools=(
            isinstance(event, GroupMessageEvent) and browser_manager is not None
        ),
        include_turn_tools=(
            turn_journal is not None
            or context_store is not None
            or message_ledger is not None
        ),
        include_pin_tools=(pin_store is not None and message_ledger is not None),
        include_self_tools=True,
        include_group_tools=isinstance(event, GroupMessageEvent),
        include_reminder_tools=reminder_store is not None,
        include_media_tools=(
            vision_worker is not None or media_library is not None
        ),
        include_source_tools=source_store is not None,
    )
    current_tool_catalog_version = tool_catalog_fingerprint(tools)
    if turn_journal is not None and journal_turn_id is not None:
        try:
            turn_journal.update_environment(
                journal_turn_id,
                provider=selected_profile.provider_identity,
                model=selected_profile.model,
                profile=selected_profile.name,
                prompt_version=TURN_PROMPT_VERSION,
                tool_catalog_version=current_tool_catalog_version,
            )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.warning(f"Could not update the turn environment: {exc}")
        if settings.turn_replay_enabled:
            parent_turn = turn_journal.fork_parent(
                scope_from_event(event),
                journal_turn_id,
            )
            if parent_turn is not None:
                replay = turn_journal.build_replay(
                    scope_from_event(event),
                    parent_turn.turn_ordinal,
                    current_provider=selected_profile.provider_identity,
                    current_model=selected_profile.model,
                    current_profile=selected_profile.name,
                    prompt_version=TURN_PROMPT_VERSION,
                    tool_catalog_version=current_tool_catalog_version,
                    max_chars=settings.turn_replay_max_chars,
                    max_segments=settings.turn_replay_max_segments,
                )
                replay_reason = replay.reason
                replay_digest_prefix = replay.digest_prefix
                turn_context = _current_turn_context(
                    event,
                    journal_turn_id,
                    include_recent=False,
                    include_target_digest=False,
                    create_edge=False,
                )
                if replay.mode == "verbatim":
                    replay_prefix = list(replay.messages)
                    replay_covered_message_ids = (
                        replay.covered_canonical_message_ids
                    )

    try:
        with telemetry.stage("context.resolve"):
            context_plan = _group_turn_context_plan(
                event,
                user_text,
                journal_turn_id,
            )
    except (OSError, RuntimeError, ValueError, sqlite3.Error, DatabaseError) as exc:
        logger.warning(f"Group reference resolution failed softly: {exc}")
    context_policy = choose_context_policy(
        user_text,
        context_plan,
        is_group=isinstance(event, GroupMessageEvent),
    )
    hybrid_recall = None
    if message_ledger is not None:
        group_memory_scope, user_memory_scope = _memory_scopes(event)
        automatic_semantic_recall = (
            semantic_recall
            if (
                (
                    context_plan is not None
                    and context_plan.focus_message_id is not None
                )
                or context_policy.mode == "expanded"
                or context_policy.fallback_group_memory
                or context_policy.fallback_user_memory
            )
            else None
        )
        try:
            with telemetry.stage("context.rerank"):
                hybrid_recall = await build_hybrid_recall(
                    ledger=message_ledger,
                    scope=scope_from_event(event),
                    plan=context_plan,
                    user_text=user_text,
                    group_memory_scope=group_memory_scope,
                    user_memory_scope=user_memory_scope,
                    memory_store=long_term_memory,
                    context_store=context_store,
                    semantic_recall=automatic_semantic_recall,
                    include_group_memory=context_policy.include_group_memory,
                    include_user_memory=context_policy.include_user_memory,
                    fallback_group_memory=context_policy.fallback_group_memory,
                    fallback_user_memory=context_policy.fallback_user_memory,
                    budget=context_policy.token_budget,
                    now=event.time,
                )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            sqlite3.Error,
            DatabaseError,
        ) as exc:
            logger.warning(f"Hybrid context recall failed softly: {exc}")
        if (
            hybrid_recall is not None
            and context_plan is not None
            and turn_journal is not None
            and journal_turn_id is not None
        ):
            try:
                payload = context_plan.journal_payload()
                payload["candidates"] = [
                    *payload["candidates"],
                    *hybrid_recall.journal_candidates(),
                ]
                turn_journal.record_context_plan(
                    journal_turn_id,
                    payload,
                    created_at=event.time,
                )
            except (OSError, RuntimeError, ValueError, sqlite3.Error, DatabaseError):
                pass

    async def _execute_tool_impl(name: str, arguments: dict[str, object]) -> str:
        nonlocal visual_reply_segment, voice_reply_segment, voice_reply_text

        logger.info(f"LLM Tool Call: {name}")

        if name == USE_SKILL_TOOL_NAME:
            requested = str(arguments.get("name") or "").strip()
            skill = skill_registry.get(requested)
            if skill is None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "技能不存在。",
                        "available": [item.name for item in skill_registry.list()],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": True,
                    "name": skill.name,
                    "title": skill.title,
                    "instructions": skill.body,
                },
                ensure_ascii=False,
            )

        if name == INSPECT_SOURCE_TOOL_NAME:
            action = str(arguments.get("action") or "").strip()
            try:
                if action == "list":
                    paths, truncated = self_source.paths(
                        str(arguments.get("path") or ""),
                        limit=int(arguments.get("limit") or 100),
                    )
                    payload: dict[str, object] = {
                        "ok": True,
                        "paths": paths,
                        "truncated": truncated,
                    }
                elif action == "search":
                    matches = self_source.search(
                        str(arguments.get("query") or ""),
                        path_prefix=str(arguments.get("path") or ""),
                        limit=int(arguments.get("limit") or 20),
                    )
                    payload = {
                        "ok": True,
                        "matches": [
                            {
                                "path": match.path,
                                "line": match.line,
                                "text": match.text,
                            }
                            for match in matches
                        ],
                    }
                elif action == "read":
                    payload = {
                        "ok": True,
                        "slice": self_source.read(
                            str(arguments.get("path") or ""),
                            start_line=int(arguments.get("start_line") or 1),
                            end_line=int(arguments.get("end_line") or 120),
                        ),
                    }
                elif action == "identity":
                    payload = {"ok": True, "snapshot": self_source.identity()}
                else:
                    payload = {"ok": False, "error": "未知的源码自查动作。"}
            except (OSError, TypeError, ValueError) as exc:
                payload = {"ok": False, "error": str(exc)}
            return json.dumps(payload, ensure_ascii=False)

        if name in {PIN_MESSAGE_TOOL_NAME, UNPIN_MESSAGE_TOOL_NAME}:
            if pin_store is None or message_ledger is None:
                return json.dumps(
                    {"ok": False, "error": "固定消息存储暂时不可用。"},
                    ensure_ascii=False,
                )
            message_id = _canonical_message_id(arguments.get("message_handle"))
            if message_id is None:
                return json.dumps(
                    {"ok": False, "error": "message_handle 格式无效。"},
                    ensure_ascii=False,
                )
            scope = scope_from_event(event)
            if name == UNPIN_MESSAGE_TOOL_NAME:
                removed = pin_store.unpin(scope, message_id)
                return json.dumps(
                    {
                        "ok": removed,
                        "message_handle": f"msg#{message_id}",
                        "removed": removed,
                        "error": None if removed else "这条消息没有被固定。",
                    },
                    ensure_ascii=False,
                )
            principal_id = message_ledger.principal_id_for_native(
                scope.platform,
                event.user_id,
            )
            try:
                pinned, created = pin_store.pin(
                    message_ledger,
                    scope,
                    message_id,
                    pinned_by_principal_id=principal_id,
                )
            except ValueError as exc:
                return json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": True,
                    "message_handle": f"msg#{pinned.canonical_message_id}",
                    "created": created,
                },
                ensure_ascii=False,
            )

        if name in {
            REMINDER_SET_TOOL_NAME,
            REMINDER_LIST_TOOL_NAME,
            REMINDER_CANCEL_TOOL_NAME,
        }:
            if reminder_store is None:
                return json.dumps(
                    {"ok": False, "error": "持久提醒功能暂时不可用。"},
                    ensure_ascii=False,
                )
            scope = scope_from_event(event)
            if name == REMINDER_LIST_TOOL_NAME:
                return json.dumps(
                    {
                        "ok": True,
                        "reminders": [
                            _reminder_payload(item)
                            for item in reminder_store.list_pending(scope)
                        ],
                    },
                    ensure_ascii=False,
                )
            if name == REMINDER_CANCEL_TOOL_NAME:
                reminder_id = _reminder_id(
                    arguments.get("reminder_handle")
                )
                if reminder_id is None:
                    return json.dumps(
                        {"ok": False, "error": "reminder_handle 格式无效。"},
                        ensure_ascii=False,
                    )
                removed = reminder_store.cancel(scope, reminder_id)
                return json.dumps(
                    {
                        "ok": removed,
                        "handle": f"reminder#{reminder_id}",
                        "cancelled": removed,
                        "error": None if removed else "当前会话没有这个待触发提醒。",
                    },
                    ensure_ascii=False,
                )
            try:
                due_at = _parse_reminder_due_at(arguments.get("due_at"))
                reminder = reminder_store.create(
                    scope,
                    creator_native_user_id=event.user_id,
                    creator_principal_id=(
                        message_ledger.principal_id_for_native(
                            scope.platform,
                            event.user_id,
                        )
                        if message_ledger is not None
                        else None
                    ),
                    message=str(arguments.get("message") or ""),
                    scheduled_for=due_at,
                )
            except ValueError as exc:
                return json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"ok": True, "reminder": _reminder_payload(reminder)},
                ensure_ascii=False,
            )

        if name == GROUP_MEMBERS_TOOL_NAME:
            if not isinstance(event, GroupMessageEvent):
                return json.dumps(
                    {"ok": False, "error": "私聊中没有群成员名单。"},
                    ensure_ascii=False,
                )
            query = str(arguments.get("query") or "").strip().casefold()
            try:
                limit = min(max(int(arguments.get("limit") or 50), 1), 100)
                raw_members = await bot.get_group_member_list(
                    group_id=event.group_id,
                )
            except (
                ActionFailed,
                NetworkError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning(f"Fetching the QQ group roster failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "读取当前群成员失败。"},
                    ensure_ascii=False,
                )
            decoded_members = decode_group_members(raw_members)
            principal_ids = (
                message_ledger.ensure_principal_identities(
                    "onebot-v11",
                    [
                        (member.native_user_id, member.display)
                        for member in decoded_members
                    ],
                )
                if message_ledger is not None
                else {}
            )
            members: list[dict[str, object]] = []
            for member in decoded_members:
                display = member.display
                if query and query not in display.casefold():
                    continue
                principal_id = principal_ids.get(member.native_user_id)
                members.append(
                    {
                        "principal": (
                            f"[mention#{principal_id}]"
                            if principal_id is not None
                            else None
                        ),
                        "display_name": display,
                        "role": member.role,
                        "title": member.title or None,
                    }
                )
                if len(members) >= limit:
                    break
            return json.dumps(
                {"ok": True, "members": members, "count": len(members)},
                ensure_ascii=False,
            )

        if name == CONTEXT_EXPAND_TOOL_NAME:
            target = str(arguments.get("target") or "").strip()
            if target.startswith("episode#"):
                if context_store is None or message_ledger is None:
                    return json.dumps(
                        {"ok": False, "error": "分层上下文暂时没有开启。"},
                        ensure_ascii=False,
                    )
                handle = target.removeprefix("episode#").strip()
                expanded_episode = context_store.expand(
                    message_ledger,
                    scope_from_event(event),
                    handle,
                    max_chars=settings.turn_expand_max_chars,
                )
                if expanded_episode is None:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": "当前会话可见范围内找不到或无法验证这个 episode#。",
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "ok": True,
                        "target": f"episode#{handle}",
                        "record": expanded_episode,
                    },
                    ensure_ascii=False,
                )
            if turn_journal is None:
                return json.dumps(
                    {"ok": False, "error": "Turn Journal 暂时没有开启。"},
                    ensure_ascii=False,
                )
            try:
                requested_turn = int(
                    target.removeprefix("t#")
                    if target.startswith("t#")
                    else arguments.get("turn_id") or 0
                )
            except (TypeError, ValueError):
                requested_turn = 0
            expanded = turn_journal.render_turn(
                scope_from_event(event),
                requested_turn,
                max_chars=settings.turn_expand_max_chars,
            )
            if expanded is None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "当前会话可见范围内找不到这个 t#。",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {"ok": True, "turn": f"t#{requested_turn}", "record": expanded},
                ensure_ascii=False,
            )

        if name == CONTEXT_SEARCH_TOOL_NAME:
            if message_ledger is None:
                return json.dumps(
                    {"ok": False, "error": "规范消息账本暂时没有开启。"},
                    ensure_ascii=False,
                )
            query = str(arguments.get("query") or "").strip()
            try:
                limit = min(max(int(arguments.get("limit") or 5), 1), 10)
            except (TypeError, ValueError):
                limit = 5
            if not query:
                return json.dumps(
                    {"ok": False, "error": "检索词不能为空。"},
                    ensure_ascii=False,
                )
            scope = scope_from_event(event)
            messages = message_ledger.search_in_scope(scope, query, limit)
            episodes = (
                context_store.search(scope, query, limit=limit)
                if context_store is not None
                else []
            )
            pinned_matches = (
                pin_store.search(
                    message_ledger,
                    scope,
                    query,
                    limit=limit,
                )
                if pin_store is not None
                else []
            )
            folded_query = query.casefold()
            memory_matches = [
                entry
                for entry in long_term_memory.list_entries(
                    _memory_scope_keys(event)
                )
                if folded_query in entry.content.casefold()
            ][:limit]
            semantic_hits = []
            if semantic_recall is not None:
                try:
                    semantic_hits = await semantic_recall.search(
                        [scope.key, *_memory_scope_keys(event)],
                        query,
                        limit=limit * 2,
                    )
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    httpx.HTTPError,
                ) as exc:
                    logger.warning(f"Semantic recall failed softly: {exc}")
            lexical_handles = {
                *(f"msg#{message.canonical_message_id}" for message in messages),
                *(f"episode#{episode.expand_handle}" for episode in episodes),
                *(f"msg#{message.canonical_message_id}" for _pin, message in pinned_matches),
                *(f"memory#{entry.id}" for entry in memory_matches),
            }
            return json.dumps(
                {
                    "ok": True,
                    "messages": [
                        {
                            "handle": f"msg#{message.canonical_message_id}",
                            "sender": (
                                f"[mention#{message.sender_principal_id}] {message.sender_display}"
                                if message.sender_principal_id is not None
                                else message.sender_display
                            ),
                            "text": message.rendered_text[:500],
                        }
                        for message in messages
                    ],
                    "episodes": [
                        {
                            "handle": f"episode#{episode.expand_handle}",
                            "range": (
                                f"msg#{episode.start_message_id}.."
                                f"msg#{episode.end_message_id}"
                            ),
                            "summary": episode.summary_p2,
                        }
                        for episode in episodes
                    ],
                    "pins": [
                        {
                            "handle": f"msg#{message.canonical_message_id}",
                            "sender": (
                                f"[mention#{message.sender_principal_id}] {message.sender_display}"
                                if message.sender_principal_id is not None
                                else message.sender_display
                            ),
                            "text": message.rendered_text[:500],
                        }
                        for _pin, message in pinned_matches
                    ],
                    "memories": [
                        _memory_entry_payload(entry)
                        for entry in memory_matches
                    ],
                    "semantic": [
                        {
                            "handle": hit.source_handle,
                            "type": hit.source_type,
                            "text": hit.content[:500],
                            "score": round(hit.score, 4),
                            "metadata": hit.metadata,
                        }
                        for hit in semantic_hits
                        if hit.source_handle not in lexical_handles
                    ][:limit],
                },
                ensure_ascii=False,
            )

        if name == MEMORY_ADD_TOOL_NAME:
            scope_type = str(arguments.get("scope", "user")).strip().lower()
            content = str(arguments.get("content", "")).strip()
            group_scope, user_scope = _memory_scopes(event)
            if scope_type == "group":
                if group_scope is None:
                    return json.dumps(
                        {"ok": False, "error": "私聊中没有群记忆范围。"},
                        ensure_ascii=False,
                    )
                if not _can_edit_group_memory(event):
                    return json.dumps(
                        {"ok": False, "error": "只有群管理员或机器人授权用户可以修改群记忆。"},
                        ensure_ascii=False,
                    )
                scope_key = group_scope
            elif scope_type == "user":
                scope_key = user_scope
            else:
                return json.dumps(
                    {"ok": False, "error": "记忆范围必须是 user 或 group。"},
                    ensure_ascii=False,
                )
            if _looks_like_secret(content):
                return json.dumps(
                    {"ok": False, "error": "检测到可能的密码、Token 或密钥，拒绝保存。"},
                    ensure_ascii=False,
                )
            provenance = _memory_provenance(event)
            try:
                entry, created = long_term_memory.add(
                    scope_key,
                    scope_type,
                    content,
                    creator_user_id=event.user_id,
                    creator_principal_id=int(
                        provenance["actor_principal_id"] or 0
                    ),
                    source_message_id=provenance["source_message_id"],
                    reason="model memory_add tool",
                )
            except LongTermMemoryError as exc:
                return json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": True,
                    "created": created,
                    "memory": _memory_entry_payload(entry),
                },
                ensure_ascii=False,
            )

        if name == MEMORY_LIST_TOOL_NAME:
            requested_scope = str(arguments.get("scope", "all")).strip().lower()
            if requested_scope not in {"user", "group", "all"}:
                requested_scope = "all"
            entries = long_term_memory.list_entries(
                _memory_scope_keys(event, requested_scope)
            )
            return json.dumps(
                {
                    "ok": True,
                    "memories": [
                        _memory_entry_payload(entry) for entry in entries
                    ],
                },
                ensure_ascii=False,
            )

        if name == MEMORY_REMOVE_TOOL_NAME:
            try:
                memory_id = int(arguments.get("memory_id") or 0)
            except (TypeError, ValueError):
                memory_id = 0
            entry = _find_visible_memory(event, memory_id)
            if (
                entry is not None
                and entry.scope_type == "group"
                and not _can_edit_group_memory(event)
            ):
                return json.dumps(
                    {"ok": False, "error": "你没有修改群记忆的权限。"},
                    ensure_ascii=False,
                )
            removed = long_term_memory.remove(
                memory_id,
                _memory_scope_keys(event),
                **_memory_provenance(event),
                reason="model memory_remove tool",
            )
            return json.dumps(
                {
                    "ok": removed,
                    "memory_id": memory_id,
                    "error": None if removed else "当前会话中找不到这条记忆。",
                },
                ensure_ascii=False,
            )

        if name == WEB_SEARCH_TOOL_NAME:
            query = str(arguments.get("query", "")).strip() or user_text
            query = query[: settings.max_input_chars]
            requested_freshness = {
                "day": "d",
                "week": "w",
                "month": "m",
                "year": "y",
            }.get(str(arguments.get("freshness", "auto")))
            freshness = search_freshness(query) or requested_freshness
            try:
                results = await search_web(
                    query,
                    max_results=settings.search_max_results,
                    timeout_seconds=settings.search_timeout_seconds,
                    freshness=freshness,
                )
            except SearchError as exc:
                logger.warning(f"Web search tool failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "联网搜索暂时失败。"},
                    ensure_ascii=False,
                )

            known_urls = {result.url for result in search_results}
            search_results.extend(
                result for result in results if result.url not in known_urls
            )
            return json.dumps(
                {
                    "ok": True,
                    "query": query,
                    "freshness": freshness or "all",
                    "results": [
                        {
                            "title": result.title,
                            "url": result.url,
                            "snippet": result.snippet,
                        }
                        for result in results
                    ],
                },
                ensure_ascii=False,
            )

        if name == READ_IMAGE_TEXT_TOOL_NAME:
            if not available_image_sources:
                return json.dumps(
                    {"ok": False, "error": "本轮没有可读取的图片。"},
                    ensure_ascii=False,
                )
            try:
                text = await recognize_images(
                    bot,
                    available_image_sources,
                    timeout_seconds=settings.ocr_timeout_seconds,
                    max_chars=settings.ocr_max_chars,
                )
            except OCRError as exc:
                logger.warning(f"Image OCR tool failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "图片文字识别暂时失败。"},
                    ensure_ascii=False,
                )
            if not text:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "图片中没有识别到清晰文字，无法理解纯画面。",
                    },
                    ensure_ascii=False,
                )
            used_ocr_texts.append(text)
            return json.dumps(
                {"ok": True, "text": text},
                ensure_ascii=False,
            )

        if name == VIEW_IMAGE_TOOL_NAME:
            if vision_worker is None:
                return json.dumps(
                    {"ok": False, "error": "图片理解服务暂时不可用。"},
                    ensure_ascii=False,
                )
            scope = scope_from_event(event)
            native_message_id: str | int = event.message_id
            source_items: list[tuple[int, str]] = []
            requested_handle = str(arguments.get("message_handle") or "").strip()
            try:
                if requested_handle:
                    canonical_id = _canonical_message_id(requested_handle)
                    target = (
                        message_ledger.get_in_scope(scope, canonical_id)
                        if message_ledger is not None and canonical_id is not None
                        else None
                    )
                    if target is None or not target.native_message_id:
                        return json.dumps(
                            {"ok": False, "error": "当前群看不到这条图片消息。"},
                            ensure_ascii=False,
                        )
                    native_message_id = target.native_message_id
                    raw_target = await bot.get_msg(message_id=int(native_message_id))
                    source_items = _indexed_image_sources(
                        raw_target.get("message")
                        if isinstance(raw_target, dict)
                        else None
                    )
                else:
                    source_items = _indexed_image_sources(event.original_message)
                    if not source_items:
                        replied_id = reply_message_id(event.original_message)
                        if replied_id is not None:
                            native_message_id = replied_id
                            raw_target = await bot.get_msg(message_id=replied_id)
                            source_items = _indexed_image_sources(
                                raw_target.get("message")
                                if isinstance(raw_target, dict)
                                else None
                            )
                    if not source_items:
                        recent_sources = recent_images.get(_image_cache_key(event))
                        source_items = list(enumerate(recent_sources))
                raw_segment_index = arguments.get("segment_index")
                if raw_segment_index is not None:
                    requested_index = int(raw_segment_index)
                    selected = next(
                        (item for item in source_items if item[0] == requested_index),
                        None,
                    )
                else:
                    selected = source_items[0] if source_items else None
                if selected is None:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": (
                                "没有找到可识别的图片，请发送图片、回复图片，"
                                "或检查 segment_index。"
                            ),
                        },
                        ensure_ascii=False,
                    )
                segment_index, source_url = selected
                mode = str(arguments.get("mode") or "summary").strip().lower()
                question = str(arguments.get("question") or "").strip()
                result = await vision_worker.submit_and_wait(
                    scope_key=scope.key,
                    native_message_id=native_message_id,
                    segment_index=segment_index,
                    requester_native_user_id=event.user_id,
                    source_url=source_url,
                    mode=mode,
                    question=question,
                    wait_seconds=min(settings.media_timeout_seconds, 45),
                )
            except (
                ActionFailed,
                OSError,
                RuntimeError,
                ValueError,
                DatabaseError,
            ) as exc:
                logger.warning(f"Transient vision tool failed: {exc}")
                result = None
            if result is None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "这次识图没有及时完成，请重试；图片不会保存到媒体库。",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": True,
                    **result.as_dict(),
                    "stored": False,
                    "message": "本次结果已消费，仔细查看时会重新调用视觉模型。",
                },
                ensure_ascii=False,
            )

        if name == FIND_STICKERS_TOOL_NAME:
            if media_library is None:
                return json.dumps(
                    {"ok": False, "error": "媒体检索暂时不可用。"},
                    ensure_ascii=False,
                )
            query = str(arguments.get("query") or "").strip()
            try:
                limit = min(max(int(arguments.get("limit") or 5), 1), 10)
            except (TypeError, ValueError):
                limit = 5
            if not query:
                return json.dumps(
                    {"ok": False, "error": "检索词不能为空。"},
                    ensure_ascii=False,
                )
            try:
                records = await media_library.search_stickers(
                    query,
                    limit=limit,
                )
                sticker_handles_this_turn.update(item.handle for item in records)
            except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
                logger.warning(f"Media search tool failed: {exc}")
                records = []
            return json.dumps(
                {
                    "ok": True,
                    "query": query,
                    "items": [
                        {
                            "media_handle": item.handle,
                            "summary": item.summary,
                            "description": item.description,
                            "subjects": list(item.subjects),
                            "actions": list(item.actions),
                            "emotion": list(item.emotions),
                            "usage": list(item.usage),
                            "is_sticker": item.is_sticker,
                            "score": round(item.score, 4),
                        }
                        for item in records
                    ],
                },
                ensure_ascii=False,
            )

        if name == TRANSCRIBE_VOICE_TOOL_NAME:
            if available_voice_message_id is None:
                return json.dumps(
                    {"ok": False, "error": "本轮没有可读取的 QQ 语音。"},
                    ensure_ascii=False,
                )
            try:
                text = await transcribe_voice(
                    bot,
                    available_voice_message_id,
                    timeout_seconds=settings.voice_timeout_seconds,
                )
            except VoiceError as exc:
                logger.warning(f"Voice transcription tool failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "QQ 语音转文字暂时失败。"},
                    ensure_ascii=False,
                )
            if not text:
                return json.dumps(
                    {"ok": False, "error": "这条语音没有识别出清晰文字。"},
                    ensure_ascii=False,
                )
            used_voice_texts.append(text)
            return json.dumps(
                {"ok": True, "text": text},
                ensure_ascii=False,
            )

        if name == REPLY_WITH_VOICE_TOOL_NAME:
            text = str(arguments.get("text", "")).strip()
            if not text:
                return json.dumps(
                    {"ok": False, "error": "没有提供要朗读的回答。"},
                    ensure_ascii=False,
                )
            if voice_reply_segment is not None:
                return json.dumps(
                    {"ok": True, "message": "语音回复已经生成。"},
                    ensure_ascii=False,
                )
            try:
                audio, speech_text = await synthesize_silk_voice(
                    text,
                    provider=settings.voice_provider,
                    voice_name=settings.voice_name,
                    rate=settings.voice_rate,
                    pitch=settings.voice_pitch,
                    local_voice_name=settings.voice_local_name,
                    local_rate=settings.voice_local_rate,
                    max_chars=settings.voice_max_chars,
                    timeout_seconds=settings.voice_timeout_seconds,
                )
            except VoiceError as exc:
                logger.warning(f"Voice reply tool failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "本地 QQ 语音生成暂时失败。"},
                    ensure_ascii=False,
                )
            voice_reply_segment = MessageSegment.record(audio)
            voice_reply_text = speech_text
            return json.dumps(
                {"ok": True, "message": "语音回复已经生成并等待发送。"},
                ensure_ascii=False,
            )

        if name == SEND_STICKER_TOOL_NAME:
            if visual_reply_segment is not None:
                return json.dumps(
                    {"ok": True, "message": "本轮表情已经准备发送。"},
                    ensure_ascii=False,
                )
            media_handle = str(arguments.get("media_handle") or "").strip()
            query = str(arguments.get("query") or "").strip()
            require_different = requests_sticker_variation(user_text)
            record = None
            searched_stickers = False
            if media_library is not None and query:
                searched_stickers = True
                try:
                    candidates = await media_library.search_stickers(
                        query,
                        limit=10,
                    )
                    record = choose_sticker_candidate(
                        candidates,
                        allow_recent_fallback=not require_different,
                    )
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    DatabaseError,
                ) as exc:
                    logger.warning(f"Sticker search-and-send tool failed: {exc}")
            elif (
                media_library is not None
                and media_handle in sticker_handles_this_turn
                and media_handle.startswith("media#")
            ):
                try:
                    media_id = int(media_handle.removeprefix("media#"))
                    record = media_library.get_sticker(media_id)
                    if (
                        require_different
                        and record is not None
                        and record.last_sent_at is not None
                        and record.last_sent_at >= int(time.time()) - 300
                    ):
                        record = None
                except (OSError, RuntimeError, TypeError, ValueError, DatabaseError):
                    record = None
            if (
                media_library is not None
                and record is None
                and not searched_stickers
            ):
                try:
                    candidates = await media_library.search_stickers(
                        query or user_text,
                        limit=10,
                    )
                    record = choose_sticker_candidate(
                        candidates,
                        allow_recent_fallback=not require_different,
                    )
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    DatabaseError,
                ) as exc:
                    logger.warning(f"Sticker search-and-send tool failed: {exc}")
            if record is not None and record.is_sticker and record.safety == "safe":
                visual_reply_segment = MessageSegment.image(record.storage_path.read_bytes())
                media_library.mark_sent(record.media_id)
                return json.dumps(
                    {
                        "ok": True,
                        "message": "已从全局表情库找到并准备发送。",
                        "media_handle": record.handle,
                        "summary": record.summary,
                    },
                    ensure_ascii=False,
                )
            if media_library is not None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            "没有别的匹配表情。"
                            if require_different
                            else "没有这个表情。"
                        ),
                    },
                    ensure_ascii=False,
                )
            sticker_message = random_sticker_message()
            if isinstance(sticker_message, str):
                return json.dumps(
                    {"ok": False, "error": sticker_message},
                    ensure_ascii=False,
                )
            visual_reply_segment = sticker_message
            return json.dumps(
                {"ok": True, "message": "表情包已经准备发送。"},
                ensure_ascii=False,
            )

        if name == SEND_QQ_FACE_TOOL_NAME:
            if visual_reply_segment is not None:
                return json.dumps(
                    {"ok": True, "message": "本轮表情已经准备发送。"},
                    ensure_ascii=False,
                )
            expression = str(arguments.get("expression", "随机")).strip()
            face_message = qq_face_message(expression)
            if isinstance(face_message, str):
                return json.dumps(
                    {"ok": False, "error": face_message},
                    ensure_ascii=False,
                )
            visual_reply_segment = face_message
            return json.dumps(
                {"ok": True, "message": "QQ 自带表情已经准备发送。"},
                ensure_ascii=False,
            )

        if agent_executor is not None:
            result = await agent_executor.execute(name, arguments)
            if result is not None:
                return result

        return json.dumps(
            {"ok": False, "error": f"不支持的工具：{name}"},
            ensure_ascii=False,
        )

    async def execute_tool(name: str, arguments: dict[str, object]) -> str:
        async with telemetry.tool(name):
            return await _execute_tool_impl(name, arguments)

    async def record_loop_event(event_record: AgentLoopEvent) -> None:
        if journal_turn_id is not None:
            await _record_turn_loop_event(journal_turn_id, event_record)

    tool_choice = "auto"
    if force_search:
        tool_choice = force_tool(WEB_SEARCH_TOOL_NAME)
    elif force_ocr:
        tool_choice = force_tool(
            VIEW_IMAGE_TOOL_NAME
            if vision_worker is not None
            else READ_IMAGE_TEXT_TOOL_NAME
        )
    elif force_voice_transcription:
        tool_choice = force_tool(TRANSCRIBE_VOICE_TOOL_NAME)
    elif force_voice_reply:
        tool_choice = force_tool(REPLY_WITH_VOICE_TOOL_NAME)

    try:
        context_parts: list[str] = [
            "[QQ 输出协议]\n"
            "像群友一样直接说重点。空行会作为多条消息逐条发送，行内可用 "
            "[split] 强制分条，代码围栏内不会拆；一次最多 10 条。需要引用上下文中的"
            "某条消息时，在对应段开头写 [reply#<msg编号>]。确实没有必要回复时，"
            "整条只写 [silence]；需要用反应表达原因可写 [silence:表情名]。"
            "需要真正 @群成员时，只能写完整的 [mention#<principal编号>]，必须照抄"
            "成员记录或 group_members 返回的句柄；不要输出 @#编号，也绝不能把 "
            "principal 编号或 QQ 号自行填进 at。宿主会在发送前解析并校验当前群成员。"
            "要重发当前会话中的图片或表情包，可照抄 [image#消息.段] 或 "
            "[sticker#消息.段]；QQ 自带表情使用 [face#编号]。"
            "用户要求从已有表情包中发一张时，直接调用 send_sticker(query)，"
            "它会在全局安全表情库的匹配候选中兼顾相关度和多样性选择，"
            "不要先反复调用 find_stickers；"
            "query 只保留用户明确说出的核心标签，不要添加泛化形容词；"
            "用户只说随便发一个时省略 query。"
            "正经问题不能用沉默敷衍，控制标记不要放在普通句子中。\n"
            "需要贴代码时使用带语言名的 ``` 围栏，需要对比数据时使用 Markdown "
            "表格；宿主会把完整代码块和表格渲染为清晰图片。\n"
            "可用反应表：" + face_prompt_table()
        ]
        skill_index = skill_registry.prompt_index()
        if skill_index:
            context_parts.append(skill_index)
        if turn_context:
            context_parts.append(turn_context)
        if replay_prefix:
            context_parts.append(
                "[host replay status]\n"
                "已按原顺序附加先前回合的 provider 消息段；当前 system prompt "
                "仍然拥有最高优先级。不要重复已经提交的发送或写入效果。"
                f"有效性判定：{replay_reason}。"
            )
        if replay_digest_prefix:
            context_parts.append(
                "[older turn digest prefix]\n" + replay_digest_prefix
            )
        if turn_journal is not None or context_store is not None:
            context_parts.append(
                "旧聊天或旧任务细节按需先用 context_search，再用 context_expand；"
                "不要猜测不存在的句柄。"
            )
        agent_tool_context = ""
        if sandbox_tools_enabled:
            agent_tool_context = AGENT_TOOL_PROMPT
            replied_message_id = reply_message_id(event.original_message)
            if replied_message_id is not None:
                canonical_reply_id: int | None = None
                if agent_executor is not None:
                    try:
                        canonical_reply_id = (
                            await agent_executor.ensure_canonical_message(
                                replied_message_id
                            )
                        )
                    except (
                        ActionFailed,
                        OSError,
                        ValueError,
                        sqlite3.Error,
                        DatabaseError,
                    ) as exc:
                        logger.warning(
                            "Could not canonicalize the replied message: "
                            f"{exc}"
                        )
                if canonical_reply_id is not None:
                    agent_tool_context += (
                        "\n当前用户消息回复了群消息 "
                        f"msg#{canonical_reply_id}。"
                        "当任务涉及“这个文件”“这条消息”或被回复内容时，"
                        "先调用 get_message_by_id 读取它；如果返回附件，"
                        "创建沙箱后调用 import_file_to_sandbox，并传入这个 "
                        "message_handle，将附件直接导入沙箱后再继续处理。"
                    )
                else:
                    agent_tool_context += (
                        "\n当前消息带有引用，但被引用内容暂时无法读取；"
                        "不要猜测其中的文字或附件。"
                    )
        if agent_tool_context:
            context_parts.append(agent_tool_context)

        focused_message_ids: tuple[int, ...] = ()
        if context_plan is not None:
            focused_message_ids = tuple(
                message_id
                for message_id in (
                    context_plan.focus_message_id,
                    *context_plan.related_message_ids,
                )
                if message_id is not None
            )
        excluded_context_ids = tuple(
            dict.fromkeys(
                (*replay_covered_message_ids, *focused_message_ids)
            )
        )
        raw_group_context = _current_group_context(
            event,
            policy=context_policy,
            exclude_canonical_message_ids=excluded_context_ids,
        )
        group_prompt_context = combine_budgeted_sections(
            [
                (
                    "focus",
                    context_plan.rendered_context if context_plan is not None else "",
                    context_policy.token_budget.focus,
                ),
                (
                    "semantic",
                    hybrid_recall.group_context if hybrid_recall is not None else "",
                    context_policy.token_budget.semantic,
                ),
                (
                    "timeline",
                    raw_group_context,
                    context_policy.token_budget.timeline,
                ),
            ],
            total_budget=(
                context_policy.token_budget.focus
                + context_policy.token_budget.semantic
                + context_policy.token_budget.timeline
            ),
        )
        memory_prompt_context = (
            hybrid_recall.memory_context
            if hybrid_recall is not None
            else fit_token_budget(
                _current_long_term_memory(
                    event,
                    user_text,
                    context_policy,
                ),
                context_policy.token_budget.group_memory
                + context_policy.token_budget.user_memory,
            )
        )
        answer = await ask_deepseek_with_tools(
            user_text,
            (
                []
                if replay_prefix or isinstance(event, GroupMessageEvent)
                else memory.get(conversation_id)
            ),
            tools,
            execute_tool,
            group_context=group_prompt_context,
            memory_context=memory_prompt_context,
            current_user=_current_user_identity(event),
            tool_choice=tool_choice,
            profile=selected_profile,
            max_tool_rounds=(
                settings.tool_max_rounds
                if sandbox_tools_enabled
                else settings.tool_simple_max_rounds
            ),
            tool_context="\n\n".join(context_parts),
            trace=turn_trace,
            event_sink=(
                record_loop_event if journal_turn_id is not None else None
            ),
            replay_prefix=replay_prefix or None,
            feedback_provider=feedback_provider,
            final_text_sink=final_stream_sink,
            final_stream_state=final_stream_state,
        )
    except DeepSeekConfigError:
        return f"模型配置 {selected_profile.name} 缺少可用的 API Key。"
    except DatabaseError as exc:
        logger.warning(f"AI context storage request failed: {exc}")
        return "我读取聊天上下文时遇到数据库问题，等会儿再试。"
    except RuntimeError as exc:
        logger.warning(f"LLM request failed: {exc}")
        return f"{selected_profile.provider} 暂时没回上来，等会儿再试。"
    except Exception as exc:
        logger.exception(f"Unexpected AI chat error: {exc}")
        return "我这边处理消息时出错了。"
    finally:
        if agent_executor is not None:
            cleanup = await agent_executor.cleanup_task_sandboxes()
            destroyed = cleanup["destroyed"]
            failed = cleanup["failed"]
            if destroyed:
                logger.info(
                    f"Destroyed {len(destroyed)} task sandbox(es): "
                    + ", ".join(destroyed)
                )
            if failed:
                logger.warning(
                    f"Could not destroy {len(failed)} task sandbox(es): "
                    + ", ".join(failed)
                )

    if not answer and not voice_reply_text and visual_reply_segment is None:
        return "模型没有返回内容。"

    answer = _trim_reply(voice_reply_text or answer)
    memory_user_text = user_text
    if used_ocr_texts:
        memory_user_text += "\n\n[图片 OCR]\n" + "\n\n".join(used_ocr_texts)
    if used_voice_texts:
        memory_user_text += "\n\n[语音转文字]\n" + "\n\n".join(used_voice_texts)
    memory_answer = answer or "[工具动作：发送了一个表情]"
    memory.append_turn(conversation_id, memory_user_text, memory_answer)
    if isinstance(event, GroupMessageEvent) and message_ledger is None:
        group_context.append(event.group_id, "机器人", memory_answer)

    if (
        voice_reply_segment is None
        and visual_reply_segment is None
        and plan_reply(answer).silence
    ):
        return answer

    if voice_reply_segment is not None:
        return Message([voice_reply_segment])

    if visual_reply_segment is not None:
        reply = Message()
        if answer:
            reply.append(MessageSegment.text(ai_reply_message(answer, user_text)))
            reply.append(MessageSegment.text("\n"))
        reply.append(visual_reply_segment)
        return reply

    visible_answer = answer
    if (
        final_stream_state is not None
        and final_stream_state.sent_prefix
        and answer.startswith(final_stream_state.sent_prefix.rstrip())
    ):
        prefix_length = len(final_stream_state.sent_prefix.rstrip())
        visible_answer = answer[prefix_length:].lstrip("\r\n")
    if visible_answer:
        reply = ai_reply_message(visible_answer, user_text)
    elif final_stream_state is not None and final_stream_state.sent_prefix:
        reply = choose_ai_reply_kaomoji(answer, user_text)
    else:
        reply = ai_reply_message(answer, user_text)
    sources = render_search_sources(search_results)
    if sources:
        return f"{reply}\n\n{sources}"
    return reply
