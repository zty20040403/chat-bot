"""Presentation constants shared by the OneBot services."""

import re
from zoneinfo import ZoneInfo

SEND_RETRY_DELAY_SECONDS = 2.0
SEND_RETRY_MAX_CHARS = 800
TURN_PROMPT_VERSION = "qqbot-turn-v13"
BOT_VERSION = "0.11.1"
EMPTY_MENTION_FOLLOW_UP = "你觉得呢"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
GROUP_CONVERSATION_ID_PATTERN = re.compile(r"^group:(\d+):user:(\d+)$")
