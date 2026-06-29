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
    rate_limit_seconds: int
    max_input_chars: int
    max_reply_chars: int
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
            rate_limit_seconds=_get_int("AI_RATE_LIMIT_SECONDS", 8),
            max_input_chars=_get_int("AI_MAX_INPUT_CHARS", 1500),
            max_reply_chars=_get_int("AI_MAX_REPLY_CHARS", 3000),
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
            enabled_groups=_get_group_ids("AI_ENABLED_GROUPS"),
        )

    def is_group_enabled(self, group_id: int) -> bool:
        return not self.enabled_groups or group_id in self.enabled_groups


settings = Settings.from_env()
