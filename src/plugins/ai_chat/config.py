from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


def _get_group_ids(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()

    group_ids: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            group_ids.add(int(item))
        except ValueError:
            continue
    return group_ids


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_thinking: str
    system_prompt: str
    max_context_turns: int
    group_context_messages: int
    group_context_chars: int
    ledger_enabled: bool
    context_lifecycle_enabled: bool
    context_input_budget_tokens: int
    context_high_watermark_tokens: int
    context_low_watermark_tokens: int
    context_compartment_target_tokens: int
    context_raw_tail_min_messages: int
    context_max_compartments: int
    turn_journal_enabled: bool
    turn_recent_hours: int
    turn_recent_limit: int
    turn_archive_ttl_days: int
    turn_archive_max_per_scope: int
    turn_archive_max_bytes: int
    turn_event_max_chars: int
    turn_expand_max_chars: int
    turn_replay_enabled: bool
    turn_replay_max_chars: int
    turn_replay_max_segments: int
    memory_max_entries: int
    memory_max_chars: int
    max_input_chars: int
    max_reply_chars: int
    tool_max_rounds: int
    tool_simple_max_rounds: int
    tool_max_calls_per_round: int
    tool_max_total_calls: int
    tool_max_result_chars: int
    tool_max_context_chars: int
    search_enabled: bool
    search_auto_enabled: bool
    search_max_results: int
    search_timeout_seconds: int
    ocr_enabled: bool
    ocr_max_images: int
    ocr_max_chars: int
    ocr_timeout_seconds: int
    ocr_recent_image_seconds: int
    voice_enabled: bool
    voice_provider: str
    voice_name: str
    voice_rate: str
    voice_pitch: str
    voice_local_name: str
    voice_local_rate: int
    voice_max_chars: int
    voice_timeout_seconds: int
    voice_recent_seconds: int
    proactive_enabled: bool
    proactive_chance_percent: int
    proactive_cooldown_seconds: int
    proactive_min_messages: int
    proactive_max_reply_chars: int
    warmup_enabled: bool
    warmup_idle_seconds: int
    warmup_cooldown_seconds: int
    warmup_daily_limit: int
    warmup_check_seconds: int
    warmup_max_reply_chars: int
    warmup_quiet_start_hour: int
    warmup_quiet_end_hour: int
    sandbox_enabled: bool
    sandbox_allowed_users: set[int]
    sandbox_max_per_user: int
    sandbox_max_total: int
    sandbox_timeout_seconds: int
    sandbox_max_file_bytes: int
    enabled_groups: set[int]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
            deepseek_thinking=os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower(),
            system_prompt=os.getenv(
                "AI_SYSTEM_PROMPT",
                "你是QQ群里的友好助手。回答要简洁、准确、有帮助；不知道就说不知道。",
            ).strip(),
            max_context_turns=_get_int("AI_MAX_CONTEXT_TURNS", 6),
            group_context_messages=_get_int("AI_GROUP_CONTEXT_MESSAGES", 40),
            group_context_chars=_get_int("AI_GROUP_CONTEXT_CHARS", 4000),
            ledger_enabled=_get_bool("AI_LEDGER_ENABLED", True),
            context_lifecycle_enabled=_get_bool(
                "AI_CONTEXT_LIFECYCLE_ENABLED",
                True,
            ),
            context_input_budget_tokens=min(
                max(_get_int("AI_CONTEXT_INPUT_BUDGET_TOKENS", 6000), 1000),
                64000,
            ),
            context_high_watermark_tokens=min(
                max(_get_int("AI_CONTEXT_HIGH_WATERMARK_TOKENS", 4500), 500),
                64000,
            ),
            context_low_watermark_tokens=min(
                max(_get_int("AI_CONTEXT_LOW_WATERMARK_TOKENS", 2200), 250),
                32000,
            ),
            context_compartment_target_tokens=min(
                max(
                    _get_int("AI_CONTEXT_COMPARTMENT_TARGET_TOKENS", 1200),
                    250,
                ),
                8000,
            ),
            context_raw_tail_min_messages=min(
                max(_get_int("AI_CONTEXT_RAW_TAIL_MIN_MESSAGES", 8), 1),
                100,
            ),
            context_max_compartments=min(
                max(_get_int("AI_CONTEXT_MAX_COMPARTMENTS", 12), 1),
                50,
            ),
            turn_journal_enabled=_get_bool("AI_TURN_JOURNAL_ENABLED", True),
            turn_recent_hours=min(
                max(_get_int("AI_TURN_RECENT_HOURS", 24), 1), 168
            ),
            turn_recent_limit=min(
                max(_get_int("AI_TURN_RECENT_LIMIT", 5), 1), 20
            ),
            turn_archive_ttl_days=min(
                max(_get_int("AI_TURN_ARCHIVE_TTL_DAYS", 14), 0), 90
            ),
            turn_archive_max_per_scope=min(
                max(_get_int("AI_TURN_ARCHIVE_MAX_PER_SCOPE", 50), 0),
                500,
            ),
            turn_archive_max_bytes=min(
                max(_get_int("AI_TURN_ARCHIVE_MAX_KB", 512), 64),
                4096,
            )
            * 1024,
            turn_event_max_chars=min(
                max(_get_int("AI_TURN_EVENT_MAX_CHARS", 12000), 1000),
                100000,
            ),
            turn_expand_max_chars=min(
                max(_get_int("AI_TURN_EXPAND_MAX_CHARS", 10000), 1000),
                50000,
            ),
            turn_replay_enabled=_get_bool("AI_TURN_REPLAY_ENABLED", True),
            turn_replay_max_chars=min(
                max(_get_int("AI_TURN_REPLAY_MAX_CHARS", 40000), 4000),
                200000,
            ),
            turn_replay_max_segments=min(
                max(_get_int("AI_TURN_REPLAY_MAX_SEGMENTS", 3), 1),
                10,
            ),
            memory_max_entries=min(
                max(_get_int("AI_MEMORY_MAX_ENTRIES", 30), 1), 100
            ),
            memory_max_chars=min(
                max(_get_int("AI_MEMORY_MAX_CHARS", 300), 50), 1000
            ),
            max_input_chars=_get_int("AI_MAX_INPUT_CHARS", 1500),
            max_reply_chars=_get_int("AI_MAX_REPLY_CHARS", 3000),
            tool_max_rounds=min(
                max(_get_int("AI_TOOL_MAX_ROUNDS", 30), 1), 100
            ),
            tool_simple_max_rounds=min(
                max(_get_int("AI_TOOL_SIMPLE_MAX_ROUNDS", 3), 1), 20
            ),
            tool_max_calls_per_round=min(
                max(_get_int("AI_TOOL_MAX_CALLS_PER_ROUND", 4), 1), 20
            ),
            tool_max_total_calls=min(
                max(_get_int("AI_TOOL_MAX_TOTAL_CALLS", 60), 1),
                200,
            ),
            tool_max_result_chars=min(
                max(_get_int("AI_TOOL_MAX_RESULT_CHARS", 12000), 1000),
                100000,
            ),
            tool_max_context_chars=min(
                max(_get_int("AI_TOOL_MAX_CONTEXT_CHARS", 60000), 5000),
                500000,
            ),
            search_enabled=_get_bool("AI_SEARCH_ENABLED", True),
            search_auto_enabled=_get_bool("AI_SEARCH_AUTO_ENABLED", True),
            search_max_results=_get_int("AI_SEARCH_MAX_RESULTS", 5),
            search_timeout_seconds=_get_int("AI_SEARCH_TIMEOUT_SECONDS", 10),
            ocr_enabled=_get_bool("AI_OCR_ENABLED", True),
            ocr_max_images=max(_get_int("AI_OCR_MAX_IMAGES", 2), 1),
            ocr_max_chars=max(_get_int("AI_OCR_MAX_CHARS", 4000), 200),
            ocr_timeout_seconds=max(
                _get_int("AI_OCR_TIMEOUT_SECONDS", 30), 5
            ),
            ocr_recent_image_seconds=max(
                _get_int("AI_OCR_RECENT_IMAGE_SECONDS", 300), 30
            ),
            voice_enabled=_get_bool("AI_VOICE_ENABLED", True),
            voice_provider=(
                os.getenv("AI_VOICE_PROVIDER", "edge").strip().lower()
                or "edge"
            ),
            voice_name=(
                os.getenv(
                    "AI_VOICE_NAME",
                    "zh-CN-YunxiaNeural",
                ).strip()
                or "zh-CN-YunxiaNeural"
            ),
            voice_rate=os.getenv("AI_VOICE_RATE", "+0%").strip() or "+0%",
            voice_pitch=os.getenv("AI_VOICE_PITCH", "+0Hz").strip() or "+0Hz",
            voice_local_name=(
                os.getenv("AI_VOICE_LOCAL_NAME", "Tingting").strip()
                or "Tingting"
            ),
            voice_local_rate=min(
                max(_get_int("AI_VOICE_LOCAL_RATE", 210), 80), 400
            ),
            voice_max_chars=min(
                max(_get_int("AI_VOICE_MAX_CHARS", 350), 50), 1000
            ),
            voice_timeout_seconds=max(
                _get_int("AI_VOICE_TIMEOUT_SECONDS", 45), 5
            ),
            voice_recent_seconds=max(
                _get_int("AI_VOICE_RECENT_SECONDS", 300), 30
            ),
            proactive_enabled=_get_bool("AI_PROACTIVE_ENABLED", False),
            proactive_chance_percent=min(
                max(_get_int("AI_PROACTIVE_CHANCE_PERCENT", 15), 0), 100
            ),
            proactive_cooldown_seconds=max(
                _get_int("AI_PROACTIVE_COOLDOWN_SECONDS", 120), 0
            ),
            proactive_min_messages=max(
                _get_int("AI_PROACTIVE_MIN_MESSAGES", 4), 1
            ),
            proactive_max_reply_chars=max(
                _get_int("AI_PROACTIVE_MAX_REPLY_CHARS", 180), 20
            ),
            warmup_enabled=_get_bool("AI_WARMUP_ENABLED", False),
            warmup_idle_seconds=max(
                _get_int("AI_WARMUP_IDLE_SECONDS", 1800), 60
            ),
            warmup_cooldown_seconds=max(
                _get_int("AI_WARMUP_COOLDOWN_SECONDS", 1800), 60
            ),
            warmup_daily_limit=max(
                _get_int("AI_WARMUP_DAILY_LIMIT", 2), 0
            ),
            warmup_check_seconds=max(
                _get_int("AI_WARMUP_CHECK_SECONDS", 60), 10
            ),
            warmup_max_reply_chars=max(
                _get_int("AI_WARMUP_MAX_REPLY_CHARS", 80), 20
            ),
            warmup_quiet_start_hour=min(
                max(_get_int("AI_WARMUP_QUIET_START_HOUR", 1), 0), 23
            ),
            warmup_quiet_end_hour=min(
                max(_get_int("AI_WARMUP_QUIET_END_HOUR", 8), 0), 23
            ),
            sandbox_enabled=_get_bool("AI_SANDBOX_ENABLED", False),
            sandbox_allowed_users=_get_group_ids(
                "AI_SANDBOX_ALLOWED_USERS"
            ),
            sandbox_max_per_user=min(
                max(_get_int("AI_SANDBOX_MAX_PER_USER", 2), 1), 5
            ),
            sandbox_max_total=min(
                max(_get_int("AI_SANDBOX_MAX_TOTAL", 8), 1), 20
            ),
            sandbox_timeout_seconds=min(
                max(_get_int("AI_SANDBOX_TIMEOUT_SECONDS", 120), 5), 300
            ),
            sandbox_max_file_bytes=min(
                max(_get_int("AI_SANDBOX_MAX_FILE_MB", 20), 1), 100
            )
            * 1024
            * 1024,
            enabled_groups=_get_group_ids("AI_ENABLED_GROUPS"),
        )

    def is_group_enabled(self, group_id: int) -> bool:
        return not self.enabled_groups or group_id in self.enabled_groups

    def is_sandbox_user_allowed(self, user_id: int) -> bool:
        return self.sandbox_enabled and (
            not self.sandbox_allowed_users
            or user_id in self.sandbox_allowed_users
        )


settings = Settings.from_env()
