from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from types import FunctionType
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx
from src.bot_storage import DatabaseError

from nonebot import (
    get_app,
    get_bots,
    get_driver,
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
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.exception import FinishedException, IgnoredException, NetworkError
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg

from .agent_tools import AGENT_TOOL_PROMPT, AgentToolExecutor
from .alert_notifier import AlertNotificationService
from .adapters import OneBotIngestAdapter
from .bootstrap import register_http_surfaces
from .bridges import (
    BridgeError,
    BridgeOutcomeUnknown,
    BridgePermanentError,
    BridgeRetryableError,
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
from .config import settings
from .context_policy import (
    ContextPolicy,
    choose_context_policy,
    proactive_context_policy,
)
from .context_store import CaptureCandidate
from .context_pipeline import (
    ReferenceResolver,
    TurnContextPlan,
    build_hybrid_recall,
    fit_token_budget,
)
from .context_pipeline.ranking import combine_budgeted_sections
from .conversation_scope import ConversationScope
from .deepseek import (
    AgentLoopEvent,
    DeepSeekTrace,
    DeepSeekConfigError,
    FinalStreamState,
    ask_deepseek,
    ask_deepseek_json,
    ask_deepseek_with_tools,
    configure_llm_runtime,
)
from .delivery import Delivery
from .historian import (
    DreamOperation,
    HistorianResult,
    parse_dream_payload,
    parse_historian_payload,
    render_capture,
)
from .ledger import MessageLedger
from .long_term_memory import LongTermMemoryError, MemoryEntry
from .media_library import choose_sticker_candidate, requests_sticker_variation
from .model_catalog import ModelCatalogError, ModelProfile
from .message_ir import MessageBody, TextNode, render_fallback_text
from .observability import observed_ai_turn, telemetry
from .onebot_codec import (
    compose_onebot_reply,
    decode_onebot_message,
    record_onebot_event,
    record_onebot_outgoing,
    render_onebot_body,
    scope_from_event,
)
from .onebot_model_output import (
    OneBotModelOutputResolver,
    decode_group_members,
)
from .output_planner import (
    ACK_FACE_ID,
    FAILURE_FACE_ID,
    PROCESSING_FACE_ID,
    PlannedChunk,
    face_prompt_table,
    plan_reply,
)
from .proactive import (
    ProactiveCheckGate,
    ProactiveDecision,
    is_candidate_message,
    parse_proactive_decision,
    should_use_proactive_voice,
)
from .runtime_clock import runtime_clock_prompt
from .paths import CACHE_DIR, PROJECT_ROOT, STATE_DIR
from .ocr import (
    OCRError,
    image_sources,
    recognize_images,
    replied_image_sources,
    reply_message_id,
)
from .reminders import Reminder
from .runtime import build_app_context
from .application import ChatOrchestrator, ChatPorts, ChatTurnResult
from .semantic_recall import (
    SemanticDocument,
)
from .stickers import (
    ai_reply_message,
    choose_ai_reply_kaomoji,
    clear_learned_stickers,
    learn_stickers_from_message,
    learned_sticker_count,
    list_stickers,
    qq_face_message,
    random_local_sticker_message,
    random_sticker_message,
)
from .turn_journal import (
    tool_catalog_fingerprint,
    tool_effect_labels,
)
from .tool_policy import approval_from_user_text
from .web_search import (
    SearchError,
    SearchResult,
    render_direct_search_results,
    render_search_sources,
    search_freshness,
    search_web,
)
from .voice import (
    VoiceError,
    contains_voice,
    replied_voice_message_id,
    synthesize_silk_voice,
    transcribe_voice,
)
from .video_analysis import DeepVideoAnalysisError, DeepVideoAnalyzer
from .matchers import (
    ai,
    ai_reset,
    canonical_ingest_tracker,
    clear_data,
    group_activity_tracker,
    group_context_recorder,
    image_auto_description,
    image_ocr,
    max_style_command,
    memory_command,
    mention_ai,
    model_command,
    pin_command,
    pins_command,
    proactive_chat,
    qq_face,
    sticker,
    sticker_status,
    task_status,
    task_stop,
    unpin_command,
    usage_command,
    voice_answer,
    voice_transcription,
    web_search,
)
from . import command_handlers as _command_handlers
from . import message_ingest as _message_ingest
from . import trigger_service as _trigger_service
from . import chat_orchestrator as _chat_orchestrator
from . import tool_executor as _tool_executor
from . import reply_service as _reply_service
from . import onebot_delivery as _onebot_delivery

SEND_RETRY_DELAY_SECONDS = 2.0
SEND_RETRY_MAX_CHARS = 800
TURN_PROMPT_VERSION = "qqbot-turn-v11"
BOT_VERSION = "0.8.1"
EMPTY_MENTION_FOLLOW_UP = "你觉得呢"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
proactive_check_gate = ProactiveCheckGate()

app_context = build_app_context(
    settings,
    state_dir=STATE_DIR,
    cache_dir=CACHE_DIR,
    project_root=PROJECT_ROOT,
    logger=logger,
    historian_generator=lambda candidate: _generate_historian(candidate),
    dream_generator=lambda scope_key, entries, evidence: _generate_dream(
        scope_key,
        list(entries),
        evidence,
    ),
    evidence_provider=lambda entry: _dream_evidence(entry),
)
configure_llm_runtime(app_context.model_catalog, app_context.llm_gateway)

# Compatibility aliases keep the existing handlers and external tests stable
# while construction and ownership live in one explicit application context.
memory = app_context.memory
group_context = app_context.group_context
long_term_memory = app_context.long_term_memory
running_tasks = app_context.running_tasks
user_profiles = app_context.user_profiles
model_preferences = app_context.model_preferences
model_profiles = app_context.model_catalog
model_gateway = app_context.llm_gateway
message_ledger = app_context.message_ledger
context_store = app_context.context_store
pin_store = app_context.pin_store
self_source = app_context.self_source
skill_registry = app_context.skill_registry
reminder_store = app_context.reminder_store
delivery_store = app_context.delivery_store
job_store = app_context.job_store
job_worker = app_context.job_worker
bridge_router = app_context.bridge_router
mirror_state = app_context.mirror_state
bridge_manager = app_context.bridge_manager
usage_store = app_context.usage_store
semantic_recall = app_context.semantic_recall
semantic_index_state = app_context.semantic_index_state
maintenance_state = app_context.maintenance_state
historian_service = app_context.historian_service
dream_service = app_context.dream_service
turn_journal = app_context.turn_journal
recent_images = app_context.recent_images
recent_voices = app_context.recent_voices
reference_resolver = ReferenceResolver()
sandbox_manager = app_context.sandbox_manager
browser_manager = app_context.browser_manager
rich_renderer = app_context.rich_renderer
media_library = app_context.media_library
source_store = app_context.source_store
vision_worker = app_context.vision_worker
video_analyzer: DeepVideoAnalyzer | None = None
if (
    settings.video_deep_enabled
    and source_store is not None
    and vision_worker is not None
):
    try:
        video_analyzer = DeepVideoAnalyzer(
            source_store,
            vision_worker,
            whisper_model_path=settings.video_whisper_model_path,
            frame_count=settings.video_frame_count,
            max_download_bytes=settings.video_max_download_bytes,
            max_duration_seconds=settings.video_max_duration_seconds,
            timeout_seconds=settings.video_timeout_seconds,
            whisper_threads=settings.video_whisper_threads,
            cache_seconds=settings.video_cache_seconds,
        )
    except DeepVideoAnalysisError as exc:
        logger.warning(f"Deep video analysis is unavailable: {exc}")
cold_archive = app_context.cold_archive
background_tasks = app_context.background_tasks
alert_notifier = AlertNotificationService(
    alertmanager_url=settings.alertmanager_url,
    group_id=settings.alert_notify_group_id,
    check_seconds=settings.alert_notify_check_seconds,
    state_path=app_context.state_dir / "alert-notifier.json",
    logger=logger,
)
BOT_STARTED_AT = app_context.started_at
driver = get_driver()


def _is_group_enabled(group_id: int) -> bool:
    override = model_preferences.get_group_enabled_override(group_id)
    if override is not None:
        return override
    return settings.is_group_enabled(group_id)


def _is_group_vision_auto_describe_enabled(group_id: int) -> bool:
    override = model_preferences.get_group_vision_auto_describe_override(group_id)
    if override is not None:
        return override
    return settings.vision_auto_describe


@event_preprocessor
async def ignore_disabled_group_event(event: MessageEvent) -> None:
    if (
        isinstance(event, GroupMessageEvent)
        and not _is_group_enabled(event.group_id)
    ):
        raise IgnoredException("QQ group is disabled for this bot")

register_http_surfaces(
    get_app(),
    app_context,
    settings=settings,
    version=BOT_VERSION,
    logger=logger,
)


TrackedAIResult = ChatTurnResult
_GROUP_CONVERSATION_ID_PATTERN = re.compile(r"^group:(\d+):user:\d+$")


_IMPLEMENTATION_MODULES = (
    _command_handlers, _message_ingest, _trigger_service,
    _chat_orchestrator, _tool_executor, _reply_service, _onebot_delivery,
)


def _bind_implementation_module(module: Any) -> None:
    """Bind extracted functions to this live composition namespace."""
    for name, implementation in vars(module).items():
        if not (
            (name.startswith("_") or name.startswith("handle_"))
            and callable(implementation)
            and getattr(implementation, "__module__", "") == module.__name__
            and hasattr(implementation, "__code__")
        ):
            continue
        rebound = FunctionType(
            implementation.__code__, globals(), name,
            implementation.__defaults__, implementation.__closure__,
        )
        rebound.__kwdefaults__ = implementation.__kwdefaults__
        rebound.__annotations__ = implementation.__annotations__
        rebound.__doc__ = implementation.__doc__
        globals()[name] = rebound


for _implementation_module in _IMPLEMENTATION_MODULES:
    _bind_implementation_module(_implementation_module)

# Observability is an entrypoint concern, so the extracted use case stays
# independent from the metrics decorator.
_run_tracked_ai = observed_ai_turn(_run_tracked_ai)

if vision_worker is not None:
    vision_worker.set_source_resolver(_refresh_vision_source_url)


onebot_ingest_adapter = OneBotIngestAdapter(
    group_enabled=_is_group_enabled, canonical_scope=_conversation_scope,
    image_cache_key=_image_cache_key, voice_cache_key=_voice_cache_key,
    ocr_max_images=settings.ocr_max_images, logger=logger,
    message_ledger=message_ledger, delivery_store=delivery_store,
    bridge_router=bridge_router, mirror_state=mirror_state,
    bridge_manager=bridge_manager, media_library=media_library,
    source_store=source_store, user_profiles=user_profiles,
    recent_images=recent_images, recent_voices=recent_voices,
)

@driver.on_startup
async def start_background_tasks() -> None:
    if (
        settings.alert_notify_enabled
        and settings.alertmanager_url
        and settings.alert_notify_group_id > 0
        and background_tasks.start("alert-notifier", alert_notifier.run_forever)
    ):
        logger.info(
            "Activity alert QQ notifier enabled for group "
            f"{settings.alert_notify_group_id}."
        )
    if (
        job_store is not None
        and media_library is not None
        and semantic_recall is not None
    ):
        try:
            _job, created = await asyncio.to_thread(
                job_store.enqueue,
                kind="media.index_stickers",
                idempotency_key=(
                    "media.index_stickers:"
                    f"{settings.embedding_model}:{settings.embedding_dimensions}"
                ),
                scope_key="system",
            )
            if created:
                logger.info("Scheduled restart-safe sticker search indexing.")
        except (OSError, RuntimeError, DatabaseError) as exc:
            logger.warning(f"Could not schedule global sticker search indexing: {exc}")
    if job_worker is not None and background_tasks.start(
        "durable-jobs",
        job_worker.run_forever,
    ):
        logger.info(
            "PostgreSQL durable application worker enabled for "
            f"{', '.join(job_worker.registered_kinds) or 'registered jobs'}."
        )
    if media_library is not None and background_tasks.start(
        "media-library",
        media_library.run_forever,
    ):
        logger.info(
            "Durable media worker enabled with vision profile "
            f"{settings.vision_profile}."
        )
    if vision_worker is not None and background_tasks.start(
        "vision-worker",
        vision_worker.run_forever,
    ):
        logger.info(
            "Transient image understanding worker enabled with vision profile "
            f"{settings.vision_profile}."
        )
    if cold_archive is not None and background_tasks.start(
        "cold-archive",
        cold_archive.run_forever,
    ):
        logger.info(
            "Automatic cold archive enabled; h610 remains the hot cache."
        )
    if reminder_store is not None and background_tasks.start(
        "reminders",
        _reminder_loop,
    ):
        logger.info("Persistent reminder scheduler enabled.")
    if delivery_store is not None and background_tasks.start(
        "delivery",
        _delivery_loop,
    ):
        logger.info("Durable outbound delivery worker enabled.")
    if (
        bridge_manager is not None
        and bridge_manager.matrix is not None
        and background_tasks.start("matrix-sync", _matrix_sync_loop)
    ):
        logger.info("Matrix durable sync bridge enabled.")
    if semantic_recall is not None and background_tasks.start(
        "semantic-index",
        _semantic_index_loop,
    ):
        logger.info("PostgreSQL/pgvector semantic recall worker enabled.")
    if historian_service is not None and background_tasks.start(
        "historian",
        _historian_loop,
    ):
        logger.info("Model-backed episode Historian enabled.")
    if dream_service is not None and background_tasks.start(
        "memory-dream",
        _dream_loop,
    ):
        logger.info(
            f"Nightly memory dream pass enabled at {settings.dream_hour:02d}:00."
        )


@driver.on_shutdown
async def shutdown_app_context() -> None:
    await app_context.shutdown()



for _matcher, _handler in (
    (ai, handle_ai),
    (web_search, handle_web_search),
    (image_ocr, handle_image_ocr),
    (voice_answer, handle_voice_answer),
    (voice_transcription, handle_voice_transcription),
    (model_command, handle_model_command),
    (memory_command, handle_memory_command),
    (max_style_command, handle_max_style_command),
    (pin_command, handle_pin_command),
    (unpin_command, handle_unpin_command),
    (pins_command, handle_pins_command),
    (task_status, handle_task_status),
    (usage_command, handle_usage_command),
    (task_stop, handle_task_stop),
    (mention_ai, handle_mention_ai),
    (canonical_ingest_tracker, handle_canonical_ingest),
    (group_activity_tracker, handle_group_activity),
    (image_auto_description, handle_image_auto_description),
    (proactive_chat, handle_proactive_chat),
    (sticker, handle_sticker),
    (qq_face, handle_qq_face),
    (sticker_status, handle_sticker_status),
    (group_context_recorder, handle_group_context_recorder),
    (ai_reset, handle_ai_reset),
    (clear_data, handle_clear_data),
):
    _matcher.handle()(_handler)
