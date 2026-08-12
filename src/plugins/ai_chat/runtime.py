from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from src.bot_storage import DatabaseError, PostgresDatabase
from src.bot_storage.schema import HEAD_REVISION

from .bridges import (
    BlueBubblesClient,
    BridgeManager,
    MatrixClient,
    MirrorRouter,
    MirrorStateStore,
)
from .browser_tools import BrowserManager, RichMessageRenderer
from .config import Settings
from .context_store import CaptureCandidate, ContextStore
from .delivery import DeliveryStore
from .historian import (
    DreamOperation,
    DreamService,
    HistorianResult,
    HistorianService,
    MaintenanceState,
)
from .identity import GroupUserProfileStore
from .ledger import MessageLedger
from .lifecycle import BackgroundTaskSupervisor
from .long_term_memory import LongTermMemoryStore, MemoryEntry
from .llm_gateway import LLMGateway
from .memory import ConversationMemory, GroupContextMemory
from .model_catalog import ModelCatalog
from .model_preferences import ModelPreferenceStore
from .ocr import RecentImageStore
from .pins import PinStore
from .quota import UsageStore
from .reminders import ReminderStore
from .sandbox import DockerSandboxManager
from .self_source import SelfSource
from .semantic_recall import (
    EmbeddingClient,
    PgVectorBackend,
    SemanticIndexState,
    SemanticRecallService,
)
from .skills import SkillRegistry
from .stickers import configure_learned_sticker_state
from .tasks import RunningTaskRegistry
from .turn_journal import TurnJournal
from .voice import RecentVoiceStore


class RuntimeLogger(Protocol):
    def error(self, message: object, *args: object, **kwargs: object) -> object: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> object: ...

    def info(self, message: object, *args: object, **kwargs: object) -> object: ...


HistorianGenerator = Callable[[CaptureCandidate], Awaitable[HistorianResult]]
DreamGenerator = Callable[
    [str, Sequence[MemoryEntry], str],
    Awaitable[list[DreamOperation]],
]
EvidenceProvider = Callable[[MemoryEntry], str]


@dataclass
class AppContext:
    settings: Settings
    state_dir: Path
    project_root: Path
    started_at: int
    logger: RuntimeLogger = field(repr=False)
    background_tasks: BackgroundTaskSupervisor = field(repr=False)
    database: PostgresDatabase | None = field(repr=False)
    memory: ConversationMemory
    group_context: GroupContextMemory
    long_term_memory: LongTermMemoryStore
    running_tasks: RunningTaskRegistry
    user_profiles: GroupUserProfileStore
    model_preferences: ModelPreferenceStore
    model_catalog: ModelCatalog
    llm_gateway: LLMGateway = field(repr=False)
    self_source: SelfSource
    skill_registry: SkillRegistry
    recent_images: RecentImageStore
    recent_voices: RecentVoiceStore
    sandbox_manager: DockerSandboxManager
    bridge_router: MirrorRouter
    message_ledger: MessageLedger | None = None
    context_store: ContextStore | None = None
    pin_store: PinStore | None = None
    reminder_store: ReminderStore | None = None
    delivery_store: DeliveryStore | None = None
    mirror_state: MirrorStateStore | None = None
    bridge_manager: BridgeManager | None = None
    usage_store: UsageStore | None = None
    semantic_recall: SemanticRecallService | None = None
    semantic_index_state: SemanticIndexState | None = None
    maintenance_state: MaintenanceState | None = None
    historian_service: HistorianService | None = None
    dream_service: DreamService | None = None
    turn_journal: TurnJournal | None = None
    browser_manager: BrowserManager | None = None
    rich_renderer: RichMessageRenderer | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    async def shutdown(self) -> None:
        if self._closed:
            return

        stopped = await self.background_tasks.stop_all()
        if stopped:
            self.logger.info(f"Stopped {stopped} background task(s).")

        cancelled = self.running_tasks.cancel_all()
        if cancelled:
            self.logger.info(
                f"Cancelled {cancelled} running AI task(s) during shutdown."
            )

        for name, resource in (
            ("LLM gateway", self.llm_gateway),
            ("bridge manager", self.bridge_manager),
            ("browser manager", self.browser_manager),
            ("rich renderer", self.rich_renderer),
        ):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception as exc:
                self.logger.error(f"Could not close {name}: {exc}")

        for name, resource in (
            ("mirror state", self.mirror_state),
            ("message ledger", self.message_ledger),
            ("context store", self.context_store),
            ("pin store", self.pin_store),
            ("reminder store", self.reminder_store),
            ("delivery store", self.delivery_store),
            ("usage store", self.usage_store),
            ("semantic index state", self.semantic_index_state),
            ("maintenance state", self.maintenance_state),
            ("turn journal", self.turn_journal),
        ):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception as exc:
                self.logger.error(f"Could not close {name}: {exc}")
        if self.database is not None:
            try:
                self.database.close()
            except Exception as exc:
                self.logger.error(f"Could not close PostgreSQL pool: {exc}")
        self._closed = True


def build_app_context(
    settings: Settings,
    *,
    state_dir: Path,
    project_root: Path,
    logger: RuntimeLogger,
    historian_generator: HistorianGenerator,
    dream_generator: DreamGenerator,
    evidence_provider: EvidenceProvider,
    started_at: int | None = None,
) -> AppContext:
    state_dir.mkdir(parents=True, exist_ok=True)

    database: PostgresDatabase | None = None
    if settings.postgres_dsn:
        database = PostgresDatabase(
            settings.postgres_dsn,
            schema=settings.postgres_schema,
            min_size=settings.postgres_pool_min_size,
            max_size=max(
                settings.postgres_pool_max_size,
                settings.postgres_pool_min_size,
            ),
            timeout_seconds=settings.postgres_pool_timeout_seconds,
        )
        database.require_revision(HEAD_REVISION)
        logger.info(
            f"PostgreSQL storage ready in schema {settings.postgres_schema}."
        )
    elif not settings.legacy_sqlite_allowed:
        raise RuntimeError(
            "AI_POSTGRES_DSN is required; SQLite fallback is disabled. "
            "Run the migration or explicitly set AI_ALLOW_LEGACY_SQLITE=true "
            "for a temporary rollback."
        )

    def store_source(filename: str):
        return database if database is not None else state_dir / filename

    configure_learned_sticker_state(store_source("learned_stickers.json"))

    memory = ConversationMemory(
        settings.max_context_turns,
        store_source("conversation_history.json"),
    )
    group_context = GroupContextMemory(
        settings.group_context_messages,
        settings.group_context_chars,
        store_source("group_context.json"),
    )
    long_term_memory = LongTermMemoryStore(
        store_source("long_term_memory.json"),
        max_entries_per_scope=settings.memory_max_entries,
        max_content_chars=settings.memory_max_chars,
    )

    running_tasks = RunningTaskRegistry()
    user_profiles = GroupUserProfileStore(store_source("user_profiles.json"))
    model_preferences = ModelPreferenceStore(
        store_source("model_preferences.json")
    )
    model_catalog = ModelCatalog.from_settings(settings)
    for group_id, profile_name in settings.group_model_profiles.items():
        try:
            model_catalog.resolve(profile_name)
        except ValueError as exc:
            raise RuntimeError(
                "AI_GROUP_MODEL_PROFILES_JSON maps QQ group "
                f"{group_id} to unknown model profile {profile_name!r}"
            ) from exc
    if settings.historian_enabled and settings.historian_profile:
        model_catalog.resolve(settings.historian_profile)
    if settings.dream_enabled and settings.dream_profile:
        model_catalog.resolve(settings.dream_profile)
    llm_gateway = LLMGateway()

    message_ledger: MessageLedger | None = None
    if settings.ledger_enabled:
        try:
            message_ledger = MessageLedger(store_source("bot_state.sqlite3"))
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Canonical message ledger could not be opened: {exc}")

    context_store: ContextStore | None = None
    if settings.context_lifecycle_enabled and message_ledger is not None:
        try:
            context_store = ContextStore(
                store_source("context_store.sqlite3"),
                input_budget_tokens=settings.context_input_budget_tokens,
                high_watermark_tokens=settings.context_high_watermark_tokens,
                low_watermark_tokens=settings.context_low_watermark_tokens,
                compartment_target_tokens=(
                    settings.context_compartment_target_tokens
                ),
                raw_tail_min_messages=settings.context_raw_tail_min_messages,
                max_compartments=settings.context_max_compartments,
            )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Context store could not be opened: {exc}")

    pin_store: PinStore | None = None
    if message_ledger is not None:
        try:
            pin_store = PinStore(store_source("pins.sqlite3"))
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Pinned message store could not be opened: {exc}")

    reminder_store: ReminderStore | None = None
    if settings.reminders_enabled:
        try:
            reminder_store = ReminderStore(
                store_source("reminders.sqlite3"),
                max_per_scope=settings.reminder_max_per_scope,
            )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Reminder store could not be opened: {exc}")

    delivery_store: DeliveryStore | None = None
    if settings.outbox_enabled:
        try:
            delivery_store = DeliveryStore(
                store_source("delivery_outbox.sqlite3"),
                max_attempts=settings.outbox_max_attempts,
                lease_seconds=settings.outbox_lease_seconds,
            )
            if delivery_store.recovered_ambiguous:
                logger.warning(
                    f"Parked {delivery_store.recovered_ambiguous} interrupted "
                    "delivery attempt(s) as ambiguous pending echo review."
                )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Durable delivery outbox could not be opened: {exc}")

    try:
        bridge_router = MirrorRouter.from_json(settings.mirror_routes_json)
    except ValueError as exc:
        bridge_router = MirrorRouter()
        logger.error(f"Cross-platform mirror configuration is invalid: {exc}")

    mirror_state: MirrorStateStore | None = None
    bridge_manager: BridgeManager | None = None
    if bridge_router.bundles:
        if message_ledger is None or delivery_store is None:
            logger.error(
                "Cross-platform mirrors require both the canonical ledger and outbox."
            )
        else:
            try:
                mirror_state = MirrorStateStore(
                    store_source("bridge_state.sqlite3")
                )
                matrix_client: MatrixClient | None = None
                imessage_client: BlueBubblesClient | None = None
                if settings.matrix_enabled:
                    if not (
                        settings.matrix_homeserver
                        and settings.matrix_access_token
                        and settings.matrix_user_id
                    ):
                        logger.error(
                            "Matrix is enabled but homeserver, access token, "
                            "or user id is missing."
                        )
                    else:
                        matrix_client = MatrixClient(
                            settings.matrix_homeserver,
                            settings.matrix_access_token,
                            user_id=settings.matrix_user_id,
                            sync_timeout_ms=settings.matrix_sync_timeout_ms,
                        )
                if settings.imessage_enabled:
                    if not (
                        settings.imessage_base_url
                        and settings.imessage_password
                        and settings.imessage_chat_guid
                    ):
                        logger.error(
                            "iMessage is enabled but BlueBubbles URL, password, "
                            "or chat GUID is missing."
                        )
                    else:
                        imessage_client = BlueBubblesClient(
                            settings.imessage_base_url,
                            settings.imessage_password,
                        )
                bridge_manager = BridgeManager(
                    bridge_router,
                    message_ledger,
                    delivery_store,
                    mirror_state,
                    matrix=matrix_client,
                    imessage=imessage_client,
                )
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                sqlite3.Error,
                DatabaseError,
            ) as exc:
                if mirror_state is not None:
                    mirror_state.close()
                mirror_state = None
                bridge_manager = None
                logger.error(f"Cross-platform bridge could not be configured: {exc}")

    usage_store: UsageStore | None = None
    if settings.quota_enabled:
        try:
            usage_store = UsageStore(
                store_source("usage.sqlite3"),
                daily_call_limit=settings.quota_daily_calls,
                daily_input_token_limit=settings.quota_daily_input_tokens,
                daily_output_token_limit=settings.quota_daily_output_tokens,
            )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Usage and quota store could not be opened: {exc}")

    semantic_recall: SemanticRecallService | None = None
    semantic_index_state: SemanticIndexState | None = None
    if settings.semantic_enabled:
        if not (
            settings.postgres_dsn
            and settings.embedding_api_key
            and settings.embedding_model
        ):
            logger.error(
                "Semantic recall is enabled but PostgreSQL or embedding settings "
                "are incomplete."
            )
        else:
            try:
                semantic_recall = SemanticRecallService(
                    EmbeddingClient(
                        base_url=settings.embedding_base_url,
                        api_key=settings.embedding_api_key,
                        model=settings.embedding_model,
                        dimensions=settings.embedding_dimensions,
                        timeout_seconds=settings.embedding_timeout_seconds,
                    ),
                    PgVectorBackend(
                        settings.postgres_dsn,
                        dimensions=settings.embedding_dimensions,
                        schema=settings.postgres_schema,
                    ),
                )
                semantic_index_state = SemanticIndexState(
                    store_source("semantic_index_state.sqlite3")
                )
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                sqlite3.Error,
                DatabaseError,
            ) as exc:
                semantic_recall = None
                semantic_index_state = None
                logger.error(f"Semantic recall could not be configured: {exc}")

    maintenance_state: MaintenanceState | None = None
    if settings.historian_enabled or settings.dream_enabled:
        try:
            maintenance_state = MaintenanceState(
                store_source("maintenance_state.sqlite3")
            )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Background maintenance state could not be opened: {exc}")

    historian_service: HistorianService | None = None
    if (
        settings.historian_enabled
        and message_ledger is not None
        and context_store is not None
    ):
        historian_service = HistorianService(
            message_ledger,
            context_store,
            long_term_memory,
            historian_generator,
            protected_provider=(
                lambda scope: (
                    pin_store.protected_message_ids(scope)
                    if pin_store is not None
                    else ()
                )
            ),
        )

    dream_service: DreamService | None = None
    if settings.dream_enabled:
        dream_service = DreamService(
            long_term_memory,
            dream_generator,
            evidence_provider=evidence_provider,
            min_entries=settings.dream_min_entries,
        )

    turn_journal: TurnJournal | None = None
    if settings.turn_journal_enabled and message_ledger is not None:
        try:
            turn_journal = TurnJournal(
                store_source("turn_journal.sqlite3"),
                archive_ttl_days=settings.turn_archive_ttl_days,
                archive_max_per_scope=settings.turn_archive_max_per_scope,
                archive_max_bytes=settings.turn_archive_max_bytes,
                event_max_chars=settings.turn_event_max_chars,
            )
            if turn_journal.recovered_unknown_effects:
                logger.warning(
                    "Marked "
                    f"{turn_journal.recovered_unknown_effects} interrupted tool "
                    "effect(s) as outcome-unknown."
                )
            if turn_journal.recovered_crashed_turns:
                logger.warning(
                    "Marked "
                    f"{turn_journal.recovered_crashed_turns} interrupted turn(s) "
                    "as crashed."
                )
        except (OSError, RuntimeError, sqlite3.Error, DatabaseError) as exc:
            logger.error(f"Turn journal could not be opened: {exc}")

    browser_manager: BrowserManager | None = None
    if settings.browser_enabled:
        browser_manager = BrowserManager(
            state_dir / "browser_profiles",
            timeout_seconds=settings.browser_timeout_seconds,
            max_sessions=settings.browser_max_sessions,
            idle_seconds=settings.browser_idle_seconds,
            executable_path=settings.browser_executable_path,
            allow_private_network=settings.browser_allow_private_network,
        )

    rich_renderer: RichMessageRenderer | None = None
    if settings.rich_render_enabled:
        rich_renderer = RichMessageRenderer(
            executable_path=settings.browser_executable_path,
            timeout_seconds=settings.browser_timeout_seconds,
        )

    return AppContext(
        settings=settings,
        state_dir=state_dir,
        project_root=project_root,
        started_at=int(time.time()) if started_at is None else started_at,
        logger=logger,
        background_tasks=BackgroundTaskSupervisor(logger),
        database=database,
        memory=memory,
        group_context=group_context,
        long_term_memory=long_term_memory,
        running_tasks=running_tasks,
        user_profiles=user_profiles,
        model_preferences=model_preferences,
        model_catalog=model_catalog,
        llm_gateway=llm_gateway,
        self_source=SelfSource(project_root),
        skill_registry=SkillRegistry(project_root / "skills"),
        recent_images=RecentImageStore(settings.ocr_recent_image_seconds),
        recent_voices=RecentVoiceStore(settings.voice_recent_seconds),
        sandbox_manager=DockerSandboxManager(
            max_per_owner=settings.sandbox_max_per_user,
            max_total=settings.sandbox_max_total,
            default_timeout_seconds=settings.sandbox_timeout_seconds,
            max_file_bytes=settings.sandbox_max_file_bytes,
        ),
        bridge_router=bridge_router,
        message_ledger=message_ledger,
        context_store=context_store,
        pin_store=pin_store,
        reminder_store=reminder_store,
        delivery_store=delivery_store,
        mirror_state=mirror_state,
        bridge_manager=bridge_manager,
        usage_store=usage_store,
        semantic_recall=semantic_recall,
        semantic_index_state=semantic_index_state,
        maintenance_state=maintenance_state,
        historian_service=historian_service,
        dream_service=dream_service,
        turn_journal=turn_journal,
        browser_manager=browser_manager,
        rich_renderer=rich_renderer,
    )
