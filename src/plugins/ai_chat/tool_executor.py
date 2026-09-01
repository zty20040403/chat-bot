"""Tool Executor responsibilities extracted from the plugin entrypoint."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
import asyncio
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
    PrivateMessageEvent,
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
    QUERY_ALERTS_TOOL_NAME,
    READ_IMAGE_TEXT_TOOL_NAME,
    REPLY_WITH_VOICE_TOOL_NAME,
    RUN_SUBAGENTS_TOOL_NAME,
    SAY_TOOL_NAME,
    SEND_QQ_FACE_TOOL_NAME,
    SEND_STICKER_TOOL_NAME,
    TRANSCRIBE_VOICE_TOOL_NAME,
    REMINDER_CANCEL_TOOL_NAME,
    REMINDER_LIST_TOOL_NAME,
    REMINDER_SET_TOOL_NAME,
    UNPIN_MESSAGE_TOOL_NAME,
    USE_SKILL_TOOL_NAME,
    VIEW_IMAGE_TOOL_NAME,
    VIEW_VIDEO_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    available_tools,
    force_tool,
)
from .config import (
    settings,
)
from .context_policy import (
    chronological_projection_budget,
    choose_context_policy,
)
from .context_store import (
    estimate_tokens,
)
from .context_pipeline import (
    TurnContextPlan,
    assess_evidence,
    rule_recall_route,
)
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
from .subagents import route_subagent_request
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
    image_sources,
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
from .tool_policy import approval_from_user_text
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
from .video import (
    VideoReference,
    contains_video,
    indexed_video_sources,
    message_video_sources,
    replied_video_message_id,
)
from .video_analysis import DeepVideoAnalysisError


def _private_vision_required(
    event: MessageEvent,
    user_text: str,
    available_image_sources: list[str],
) -> bool:
    if not isinstance(event, PrivateMessageEvent) or not available_image_sources:
        return False
    if image_sources(event.original_message, max_images=1):
        return True
    return bool(
        re.search(
            r"图片|照片|截图|图里|表情|看图|看看|看下|分析|识别|"
            r"刚才|上面|这(?:个|张|是|啥|什么)|它|怎么(?:样|回事)|你觉得",
            user_text,
            flags=re.IGNORECASE,
        )
    )


def _video_analysis_required(
    event: MessageEvent,
    user_text: str,
    available_video: VideoReference | None,
) -> bool:
    if available_video is None:
        return False
    if contains_video(event.original_message):
        return True
    return bool(
        re.search(
            r"视频|录像|片段|看看|看下|分析|评价|锐评|总结|讲了什么|"
            r"刚才|上面|这(?:个|段|是|啥|什么)|它|怎么(?:样|回事)|你觉得",
            user_text,
            flags=re.IGNORECASE,
        )
    )


def _alert_query_required(user_text: str) -> bool:
    return bool(
        re.search(r"告警|报警|alertmanager|prometheus", user_text, re.IGNORECASE)
        and re.search(
            r"谁|最多|常客|当前|现在|最近|今天|本周|历史|数量|统计|"
            r"哪台|哪个|还有|状态|恢复|故障|寄了|挂了",
            user_text,
            re.IGNORECASE,
        )
    )


def _alert_tools_allowed(event: MessageEvent) -> bool:
    return bool(
        event.user_id in settings.admin_user_ids
        or (
            isinstance(event, GroupMessageEvent)
            and event.group_id == settings.alert_notify_group_id
        )
    )


async def _resolve_video_reference(
    bot: Bot,
    event: MessageEvent,
    *,
    message_handle: str = "",
    segment_index: int | None = None,
) -> VideoReference | None:
    scope = scope_from_event(event)
    requested_handle = str(message_handle).strip()
    if requested_handle:
        canonical_id = _canonical_message_id(requested_handle)
        target = (
            message_ledger.get_in_scope(scope, canonical_id)
            if message_ledger is not None and canonical_id is not None
            else None
        )
        if target is None or not target.native_message_id:
            raise ValueError("当前会话看不到这条视频消息。")
        native_message_id = int(target.native_message_id)
        source_items = await message_video_sources(bot, native_message_id)
    else:
        native_message_id = int(event.message_id)
        source_items = indexed_video_sources(event.original_message)
        if contains_video(event.original_message) and not source_items:
            source_items = await message_video_sources(bot, native_message_id)
        if not source_items:
            replied_id = replied_video_message_id(event.original_message)
            if replied_id is not None:
                replied_sources = await message_video_sources(bot, replied_id)
                if replied_sources:
                    native_message_id = replied_id
                    source_items = replied_sources
        if not source_items:
            recent_id = recent_videos.get(_video_cache_key(event))
            if recent_id is not None:
                recent_sources = await message_video_sources(bot, recent_id)
                if recent_sources:
                    native_message_id = recent_id
                    source_items = recent_sources
    if segment_index is None:
        selected = source_items[0] if source_items else None
    else:
        selected = next(
            (item for item in source_items if item[0] == int(segment_index)),
            None,
        )
    if selected is None:
        return None
    selected_index, source_url = selected
    return VideoReference(native_message_id, selected_index, source_url)


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
    task_mode: bool = False,
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
    alert_tools_enabled = bool(
        alert_store is not None and _alert_tools_allowed(event)
    )
    alert_query_required = alert_tools_enabled and _alert_query_required(user_text)
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
            job_store=job_store,
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
    subagent_task_started = False
    replay_prefix: list[dict[str, Any]] = []
    replay_covered_message_ids: tuple[int, ...] = ()
    replay_digest_prefix = ""
    replay_reason = ""
    context_plan: TurnContextPlan | None = None
    context_plan_payload: dict[str, Any] | None = None
    actual_context_candidates: list[dict[str, object]] = []
    actual_context_usage = {
        "focus": estimate_tokens(user_text),
        "timeline": 0,
        "semantic": 0,
        "group_memory": 0,
        "user_memory": 0,
    }

    if available_image_sources is None and (
        settings.ocr_enabled or vision_worker is not None
    ):
        available_image_sources = await _resolve_ocr_sources(bot, event)
    available_image_sources = available_image_sources or []
    private_vision_required = _private_vision_required(
        event,
        user_text,
        available_image_sources,
    )
    available_video: VideoReference | None = None
    if video_analyzer is not None:
        try:
            available_video = await _resolve_video_reference(bot, event)
        except (ActionFailed, OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"Could not resolve QQ video for this turn: {exc}")
    video_analysis_required = _video_analysis_required(
        event,
        user_text,
        available_video,
    )
    automatic_subagent_route = route_subagent_request(
        user_text,
        has_media=bool(available_image_sources or available_video),
    )

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
        include_alert_tools=alert_tools_enabled,
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
        include_video_analysis=video_analyzer is not None,
        include_source_tools=source_store is not None,
        include_subagents=subagent_coordinator is not None,
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
    with telemetry.stage("context.route"):
        # MAX-style context keeps the live conversation chronological.  The
        # lightweight route is retained for memory budgets and observability,
        # but it must never pre-select or remove recent group messages.
        recall_decision = rule_recall_route(
            user_text,
            context_plan,
            is_group=isinstance(event, GroupMessageEvent),
        )
    context_policy = choose_context_policy(
        user_text,
        context_plan,
        is_group=isinstance(event, GroupMessageEvent),
        recall_decision=recall_decision,
        configured_max_tokens=settings.context_input_budget_tokens,
        model_max_input_tokens=selected_profile.max_input_tokens,
    )
    group_memory_scope, user_memory_scope = _memory_scopes(event)
    evidence_assessment = assess_evidence(
        user_text,
        recall_decision,
        context_plan,
        None,
        conversation_scope=scope_from_event(event).key,
        group_memory_scope=group_memory_scope,
        user_memory_scope=user_memory_scope,
    )
    if (
        context_plan is not None
        and turn_journal is not None
        and journal_turn_id is not None
    ):
        try:
            payload = context_plan.journal_payload()
            payload["recall_route"] = recall_decision.journal_payload()
            payload["recall_route"]["context_strategy"] = (
                "chronological_projection"
            )
            payload["adaptive_budget"] = {
                "focus": context_policy.token_budget.focus,
                "timeline": context_policy.token_budget.timeline,
                "semantic": context_policy.token_budget.semantic,
                "group_memory": context_policy.token_budget.group_memory,
                "user_memory": context_policy.token_budget.user_memory,
                "tool_reserve": context_policy.token_budget.tool_reserve,
                "total": context_policy.token_budget.total,
            }
            payload["evidence_guard"] = evidence_assessment.journal_payload()
            context_plan_payload = payload
            turn_journal.record_context_plan(
                journal_turn_id,
                payload,
                created_at=event.time,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error, DatabaseError):
            pass
    if (
        settings.evidence_guard_enabled
        and not evidence_assessment.sufficient
        and "scope_violation" in evidence_assessment.reason_codes
    ):
        return "上下文范围校验没有通过，已停止使用可能串群或串用户的内容。"

    async def _execute_tool_impl(name: str, arguments: dict[str, object]) -> str:
        nonlocal visual_reply_segment, voice_reply_segment, voice_reply_text
        nonlocal subagent_task_started

        logger.info(f"LLM Tool Call: {name}")

        if name == RUN_SUBAGENTS_TOOL_NAME:
            if subagent_task_started:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "本轮已经启动过一次 Sub-Agent 任务，请使用现有结果。",
                    },
                    ensure_ascii=False,
                )
            goal = str(arguments.get("goal") or "").strip()
            if not goal:
                return json.dumps(
                    {"ok": False, "error": "Sub-Agent 任务目标不能为空。"},
                    ensure_ascii=False,
                )
            if subagent_coordinator is None:
                return json.dumps(
                    {"ok": False, "error": "Sub-Agent 任务模式暂时没有开启。"},
                    ensure_ascii=False,
                )
            subagent_task_started = True
            result = await run_subagent_goal(goal)
            return json.dumps(
                {"ok": True, "result": result},
                ensure_ascii=False,
            )

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
            allowed_context_scopes = {
                scope.key,
                *_memory_scope_keys(event),
            }
            if semantic_recall is not None:
                try:
                    semantic_hits = await semantic_recall.search(
                        sorted(allowed_context_scopes),
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
                        and str(hit.scope_key) in allowed_context_scopes
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

        if name == QUERY_ALERTS_TOOL_NAME:
            if not alert_tools_enabled or alert_store is None:
                return json.dumps(
                    {"ok": False, "error": "当前会话无权读取告警状态。"},
                    ensure_ascii=False,
                )
            try:
                days = min(max(int(arguments.get("days") or 7), 1), 365)
                limit = min(max(int(arguments.get("limit") or 10), 1), 20)
                snapshot = await asyncio.to_thread(
                    alert_store.snapshot,
                    days=days,
                    limit=100,
                )
                ranked = await asyncio.to_thread(
                    alert_store.rank_incidents,
                    days=days,
                    limit=limit,
                )
                incidents = (
                    snapshot.get("incidents")
                    if isinstance(snapshot, dict)
                    and isinstance(snapshot.get("incidents"), list)
                    else []
                )
                details_by_key = {
                    str(item.get("incident_key") or ""): item
                    for item in incidents
                    if isinstance(item, dict)
                }
                ranked_items = (
                    ranked.get("items")
                    if isinstance(ranked, dict)
                    and isinstance(ranked.get("items"), list)
                    else []
                )
                ranking: list[dict[str, object]] = []
                for item in ranked_items:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("incident_key") or "")
                    ranking.append({**details_by_key.get(key, {}), **item})
                active = sorted(
                    (
                        item
                        for item in incidents
                        if isinstance(item, dict)
                        and int(item.get("active_event_count") or 0) > 0
                    ),
                    key=lambda item: (
                        int(item.get("active_event_count") or 0),
                        int(item.get("last_seen_at") or 0),
                    ),
                    reverse=True,
                )[:limit]

                def compact(item: dict[str, object]) -> dict[str, object]:
                    return {
                        "target": str(item.get("incident_key") or ""),
                        "severity": str(item.get("severity") or ""),
                        "status": str(item.get("status") or ""),
                        "event_count": int(item.get("event_count") or 0),
                        "active_event_count": int(
                            item.get("active_event_count") or 0
                        ),
                        "summary": str(item.get("summary") or "")[:300],
                        "last_seen_at": int(item.get("last_seen_at") or 0),
                    }
                return json.dumps(
                    {
                        "ok": True,
                        "authoritative": True,
                        "timezone": str(snapshot.get("timezone") or "Asia/Shanghai"),
                        "days": days,
                        "range_start": int(ranked.get("range_start") or 0),
                        "generated_at": int(ranked.get("generated_at") or 0),
                        "summary": snapshot.get("summary") or {},
                        "ranking": [compact(item) for item in ranking],
                        "active": [compact(item) for item in active],
                        "ranking_basis": (
                            "按同一 incident_key 在统计周期内的独立告警事件数降序；"
                            "不是按群聊通知条数。"
                        ),
                    },
                    ensure_ascii=False,
                )
            except (OSError, RuntimeError, TypeError, ValueError, DatabaseError) as exc:
                logger.warning(f"Alert query tool failed: {exc}")
                return json.dumps(
                    {"ok": False, "error": "权威告警库查询失败，请稍后重试。"},
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
                            {"ok": False, "error": "当前会话看不到这条图片消息。"},
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

        if name == VIEW_VIDEO_TOOL_NAME:
            if video_analyzer is None:
                return json.dumps(
                    {"ok": False, "error": "视频分析服务暂时不可用。"},
                    ensure_ascii=False,
                )
            try:
                raw_index = arguments.get("segment_index")
                target_video = await _resolve_video_reference(
                    bot,
                    event,
                    message_handle=str(arguments.get("message_handle") or ""),
                    segment_index=(int(raw_index) if raw_index is not None else None),
                )
                if target_video is None:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": (
                                "没有找到可分析的视频，请发送视频、回复视频，"
                                "或检查 message_handle 和 segment_index。"
                            ),
                        },
                        ensure_ascii=False,
                    )
                result = await video_analyzer.analyze_qq_video(
                    target_video.source_url,
                    question=str(arguments.get("question") or "").strip(),
                )
            except (ActionFailed, OSError, RuntimeError, ValueError) as exc:
                logger.warning(f"QQ video analysis tool failed: {exc}")
                error = (
                    str(exc)
                    if isinstance(exc, (DeepVideoAnalysisError, ValueError))
                    else "视频读取或分析失败，请稍后重试。"
                )
                return json.dumps(
                    {"ok": False, "error": error},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": True,
                    "segment_index": target_video.segment_index,
                    **result,
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
    elif alert_query_required:
        tool_choice = force_tool(QUERY_ALERTS_TOOL_NAME)
    elif video_analysis_required:
        tool_choice = force_tool(VIEW_VIDEO_TOOL_NAME)
    elif force_ocr or private_vision_required:
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
        if available_image_sources:
            context_parts.append(
                "[当前会话可用图片]\n"
                f"当前消息、引用消息或该用户最近五分钟内共有 "
                f"{len(available_image_sources)} 张可读取图片。用户询问图片内容、"
                "使用‘这个/它/刚才那张’等指代，或本轮直接附带图片时，必须先调用 "
                "view_image，不能只根据文字或旧上下文猜图。未指定句柄时不要编造 "
                "msg#，直接省略 message_handle。"
            )
        if available_video is not None:
            context_parts.append(
                "[当前会话可用视频]\n"
                "当前消息、引用消息或该用户最近五分钟内有一段可读取的 QQ 视频。"
                "用户要求查看、总结、评价视频，或使用‘这个/它/刚才那个’等指代时，"
                "必须先调用 view_video；未指定句柄时不要编造 msg#，直接省略 "
                "message_handle。"
            )
        if alert_tools_enabled:
            context_parts.append(
                "[权威告警数据]\n"
                "涉及当前告警、历史次数、排名、常客、恢复情况或哪台服务器故障时，"
                "必须调用 query_alerts。它读取 PostgreSQL 告警生命周期库；不要用 "
                "search_messages 统计群通知，也不要凭近期聊天猜测。回答必须说明统计周期"
                "和口径。"
            )
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
                "当前群上下文按时间顺序给出：先读连续的近期原文，再结合当前消息"
                "判断省略的主语和对象；不要因为某条旧消息曾经 @ 过机器人就擅自把"
                "它当成当前话题。明确引用的 [quoted context] 优先级最高。"
                "近期原文里有自然延续的笑点时可以简短回扣，但不要解释梗、复读梗，"
                "也不要为了显得会聊天而把已经结束的旧话题硬拉回来。"
                "遇到 [image#消息.段] 且用户要求评价或分析这张图时，调用 "
                "view_image 并完整照抄对应 msg# 句柄。"
                "遇到 [video#消息.段] 且用户要求查看、总结或评价视频时，调用 "
                "view_video 并完整照抄对应 msg# 句柄。"
                "旧聊天或旧任务细节按需先用 "
                "context_search，再用 context_expand；不要猜测不存在的句柄。"
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

        group_prompt_context = ""
        if isinstance(event, GroupMessageEvent) and message_ledger is not None:
            scope = scope_from_event(event)
            replied_native_id = reply_message_id(event.original_message)
            replied_canonical_id = (
                message_ledger.canonical_id_for_native(scope, replied_native_id)
                if replied_native_id is not None
                else None
            )
            protected_ids = (
                (replied_canonical_id,)
                if replied_canonical_id is not None
                else ()
            )
            sections: list[str] = []
            projection_built = False
            if pin_store is not None:
                pinned = pin_store.render(
                    message_ledger,
                    scope,
                    max_chars=2400,
                )
                if pinned:
                    pinned_block = (
                        "[pinned messages - long-lived current-group facts]\n"
                        + pinned
                    )
                    sections.append(pinned_block)
                    actual_context_usage["timeline"] += estimate_tokens(
                        pinned_block
                    )
                    for _pin, message in pin_store.messages(
                        message_ledger,
                        scope,
                    ):
                        handle = f"msg#{message.canonical_message_id}"
                        if handle not in pinned:
                            continue
                        actual_context_candidates.append(
                            {
                                "handle": handle,
                                "source": "pinned_message",
                                "selected": True,
                                "raw_score": 1.0,
                                "adjusted_score": 1.0,
                                "decision_codes": ["included_in_final_prompt"],
                                "reason_codes": ["pinned_context"],
                                "content_preview": message.prompt_text[:240],
                                "scores": {},
                                "evidence_ids": [message.canonical_message_id],
                            }
                        )
            if context_store is not None:
                try:
                    projection = context_store.build_projection(
                        message_ledger,
                        scope,
                        exclude_native_message_id=event.message_id,
                        protected_message_ids=protected_ids,
                        exclude_canonical_message_ids=(
                            replay_covered_message_ids
                        ),
                        materialize=False,
                        token_budget=chronological_projection_budget(
                            settings.context_input_budget_tokens,
                            model_max_input_tokens=(
                                selected_profile.max_input_tokens
                            ),
                        ),
                    )
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    sqlite3.Error,
                    DatabaseError,
                ) as exc:
                    logger.warning(
                        "Chronological context projection failed softly: "
                        f"{exc}"
                    )
                else:
                    projection_built = True
                    if projection.text:
                        sections.append(projection.text)
                        actual_context_usage["timeline"] += int(
                            getattr(
                                projection,
                                "token_estimate",
                                estimate_tokens(projection.text),
                            )
                        )
                    for message_id in getattr(
                        projection,
                        "raw_message_ids",
                        (),
                    ):
                        message = message_ledger.get_in_scope(
                            scope,
                            int(message_id),
                        )
                        actual_context_candidates.append(
                            {
                                "handle": f"msg#{int(message_id)}",
                                "source": "group_timeline",
                                "selected": True,
                                "raw_score": 1.0,
                                "adjusted_score": 1.0,
                                "decision_codes": ["included_in_final_prompt"],
                                "reason_codes": ["chronological_live_tail"],
                                "content_preview": (
                                    message.prompt_text[:240]
                                    if message is not None
                                    else ""
                                ),
                                "scores": {},
                                "evidence_ids": [int(message_id)],
                            }
                        )
                    for handle in getattr(
                        projection,
                        "compartment_handles",
                        (),
                    ):
                        actual_context_candidates.append(
                            {
                                "handle": f"episode#{handle}",
                                "source": "historian_episode",
                                "selected": True,
                                "raw_score": 1.0,
                                "adjusted_score": 1.0,
                                "decision_codes": ["included_in_final_prompt"],
                                "reason_codes": ["chronological_compartment"],
                                "content_preview": "Historian 时间线章节",
                                "scores": {},
                                "evidence_ids": [],
                            }
                        )
            if not projection_built:
                fallback = message_ledger.render_recent(
                    scope,
                    max_messages=settings.group_context_messages,
                    max_chars=max(settings.group_context_chars, 12000),
                    exclude_native_message_id=event.message_id,
                    exclude_canonical_message_ids=(
                        replay_covered_message_ids
                    ),
                )
                if fallback:
                    fallback_block = "[protected live tail]\n" + fallback
                    sections.append(fallback_block)
                    actual_context_usage["timeline"] += estimate_tokens(
                        fallback_block
                    )
                    for matched in re.finditer(r"\bmsg#([1-9][0-9]*)", fallback):
                        message_id = int(matched.group(1))
                        message = message_ledger.get_in_scope(scope, message_id)
                        actual_context_candidates.append(
                            {
                                "handle": f"msg#{message_id}",
                                "source": "group_timeline",
                                "selected": True,
                                "raw_score": 1.0,
                                "adjusted_score": 1.0,
                                "decision_codes": ["included_in_final_prompt"],
                                "reason_codes": ["chronological_fallback"],
                                "content_preview": (
                                    message.prompt_text[:240]
                                    if message is not None
                                    else ""
                                ),
                                "scores": {},
                                "evidence_ids": [message_id],
                            }
                        )
            if source_store is not None:
                try:
                    recent_sources = source_store.render_recent(scope)
                except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
                    logger.warning(f"Recent shared-source context failed: {exc}")
                else:
                    if recent_sources:
                        source_block = (
                            "[recent shared sources - inspect with source#/msg#]\n"
                            + recent_sources
                        )
                        sections.append(source_block)
                        actual_context_usage["semantic"] += estimate_tokens(
                            source_block
                        )
                        for handle in dict.fromkeys(
                            re.findall(r"\bsource#[1-9][0-9]*", recent_sources)
                        ):
                            actual_context_candidates.append(
                                {
                                    "handle": handle,
                                    "source": "shared_source",
                                    "selected": True,
                                    "raw_score": 1.0,
                                    "adjusted_score": 1.0,
                                    "decision_codes": ["included_in_final_prompt"],
                                    "reason_codes": ["recent_shared_source"],
                                    "content_preview": "当前群近期分享内容",
                                    "scores": {},
                                    "evidence_ids": [],
                                }
                            )
            if replied_canonical_id is not None:
                replied = message_ledger.get_in_scope(
                    scope,
                    replied_canonical_id,
                )
                if replied is not None:
                    quoted_block = (
                        "[quoted context - explicit reply target, highest priority]\n"
                        f"[msg#{replied.canonical_message_id} | "
                        f"{replied.sender_display}] {replied.prompt_text}"
                    )
                    sections.append(quoted_block)
                    actual_context_usage["focus"] += estimate_tokens(quoted_block)
                    actual_context_candidates.append(
                        {
                            "handle": f"msg#{replied.canonical_message_id}",
                            "source": "relation_graph",
                            "selected": True,
                            "raw_score": 1.0,
                            "adjusted_score": 1.0,
                            "decision_codes": ["included_in_final_prompt"],
                            "reason_codes": ["explicit_reply_target"],
                            "content_preview": replied.prompt_text[:240],
                            "scores": {},
                            "evidence_ids": [replied.canonical_message_id],
                        }
                    )
            group_prompt_context = "\n\n".join(sections)
        else:
            group_prompt_context = _current_group_context(
                event,
                policy=context_policy,
                exclude_canonical_message_ids=replay_covered_message_ids,
            )
        memory_prompt_context = _current_long_term_memory(
            event,
            user_text,
            context_policy,
        )
        for memory_block in memory_prompt_context.split("\n\n"):
            if memory_block.startswith("[当前群相关长期记忆]"):
                memory_source = "group_memory"
            elif memory_block.startswith("[当前用户相关长期记忆]"):
                memory_source = "user_memory"
            else:
                continue
            actual_context_usage[memory_source] += estimate_tokens(memory_block)
            for memory_id, preview in re.findall(
                r"(?m)^- \[#([1-9][0-9]*)\] (.+)$",
                memory_block,
            ):
                actual_context_candidates.append(
                    {
                        "handle": f"memory#{memory_id}",
                        "source": memory_source,
                        "selected": True,
                        "raw_score": 1.0,
                        "adjusted_score": 1.0,
                        "decision_codes": ["included_in_final_prompt"],
                        "reason_codes": ["relevant_long_term_memory"],
                        "content_preview": preview[:240],
                        "scores": {},
                        "evidence_ids": [],
                    }
                )
        if (
            context_plan_payload is not None
            and turn_journal is not None
            and journal_turn_id is not None
        ):
            deduplicated: dict[str, dict[str, object]] = {}
            source_priority = {
                "relation_graph": 5,
                "pinned_message": 4,
                "group_timeline": 3,
                "historian_episode": 2,
                "group_memory": 2,
                "user_memory": 2,
                "shared_source": 1,
            }
            for candidate in actual_context_candidates:
                handle = str(candidate.get("handle") or "")
                current = deduplicated.get(handle)
                if current is None or source_priority.get(
                    str(candidate.get("source") or ""),
                    0,
                ) > source_priority.get(str(current.get("source") or ""), 0):
                    deduplicated[handle] = candidate
            actual_context_usage["total"] = sum(actual_context_usage.values())
            route_payload = dict(context_plan_payload.get("recall_route") or {})
            route_payload["classifier_mode"] = route_payload.get("mode", "")
            route_payload["mode"] = "chronological_projection"
            route_payload["context_strategy"] = "chronological_projection"
            context_plan_payload["recall_route"] = route_payload
            adaptive_budget = dict(
                context_plan_payload.get("adaptive_budget") or {}
            )
            adaptive_budget["used"] = dict(actual_context_usage)
            for key in (
                "focus",
                "timeline",
                "semantic",
                "group_memory",
                "user_memory",
            ):
                adaptive_budget[key] = max(
                    int(adaptive_budget.get(key) or 0),
                    int(actual_context_usage[key]),
                )
            adaptive_budget["total"] = sum(
                int(adaptive_budget.get(key) or 0)
                for key in (
                    "focus",
                    "timeline",
                    "semantic",
                    "group_memory",
                    "user_memory",
                    "tool_reserve",
                )
            )
            context_plan_payload["adaptive_budget"] = adaptive_budget
            context_plan_payload["recall_candidates"] = list(
                deduplicated.values()
            )
            context_plan_payload["related_message_ids"] = sorted(
                {
                    int(evidence_id)
                    for candidate in deduplicated.values()
                    for evidence_id in candidate.get("evidence_ids", [])
                    if str(evidence_id).isdigit()
                }
            )
            context_plan_payload["resolver_version"] = (
                "chronological-projection-v3"
            )
            context_plan_payload["context_hash"] = hashlib.sha256(
                (group_prompt_context + "\n\n" + memory_prompt_context).encode(
                    "utf-8"
                )
            ).hexdigest()[:16]
            evidence_payload = dict(
                context_plan_payload.get("evidence_guard") or {}
            )
            if deduplicated:
                evidence_payload.update(
                    {
                        "sufficient": True,
                        "reason_codes": ["chronological_projection_visible"],
                        "evidence_handles": list(deduplicated),
                    }
                )
            context_plan_payload["evidence_guard"] = evidence_payload
            try:
                turn_journal.record_context_plan(
                    journal_turn_id,
                    context_plan_payload,
                    created_at=event.time,
                )
            except (
                OSError,
                RuntimeError,
                ValueError,
                sqlite3.Error,
                DatabaseError,
            ) as exc:
                logger.warning(f"Final context projection journal failed: {exc}")
        async def run_subagent_goal(goal: str) -> str:
            if subagent_coordinator is None:
                return "Sub-Agent 任务模式暂时没有开启。"

            async def report_subagent_progress(text: str) -> None:
                if agent_executor is not None:
                    await execute_tool(SAY_TOOL_NAME, {"text": text[:200]})

            trigger_message_id = (
                message_ledger.canonical_id_for_native(
                    scope_from_event(event),
                    event.message_id,
                )
                if message_ledger is not None
                else None
            )
            return await subagent_coordinator.run(
                scope_key=scope_from_event(event).key,
                conversation_id=conversation_id,
                requester_user_id=event.user_id,
                trigger_message_id=trigger_message_id,
                objective=goal,
                context=(
                    group_prompt_context
                    + "\n\n"
                    + memory_prompt_context
                    + "\n\n"
                    + "\n\n".join(context_parts)
                ),
                selected_profile=selected_profile,
                tools=tools,
                execute_tool=execute_tool,
                parent_trace=turn_trace,
                progress=report_subagent_progress,
            )

        if subagent_coordinator is not None:
            context_parts.append(
                "[自动 Sub-Agent 编排]\n"
                "用户不需要输入 /task。当前请求如果包含多个互相依赖的步骤、需要两个"
                "以上专业角色协作，或明显是长任务，调用 run_subagents，并把用户的"
                "完整目标和交付要求放进 goal。普通闲聊、常识问答、一次搜索、单张识图"
                "或单个工具能完成的请求不要调用。Sub-Agent 系统会自己用 say 报告"
                "每个角色开始和完成的工作，主 Agent 不要重复刷进度。"
            )

        use_automatic_subagents = bool(
            subagent_coordinator is not None
            and isinstance(event, GroupMessageEvent)
            and automatic_subagent_route.delegate
            and not any(
                (
                    force_search,
                    force_ocr,
                    force_voice_reply,
                    force_voice_transcription,
                    alert_query_required,
                    video_analysis_required,
                )
            )
        )
        if use_automatic_subagents:
            logger.info(
                "Automatic Sub-Agent route: domains=%s reasons=%s",
                ",".join(automatic_subagent_route.domains),
                ",".join(automatic_subagent_route.reasons),
            )

        if task_mode or use_automatic_subagents:
            answer = await run_subagent_goal(user_text)
        else:
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
                approval_checker=(
                    lambda _policy, name, arguments: approval_from_user_text(
                        user_text,
                        name,
                        arguments,
                    )
                ),
                handoff_tool=(
                    agent_executor.handoff_tool
                    if agent_executor is not None
                    else None
                ),
                compensate_tool=(
                    agent_executor.compensate_tool
                    if agent_executor is not None
                    else None
                ),
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

    answer = voice_reply_text or answer
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
