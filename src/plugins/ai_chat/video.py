from __future__ import annotations

import time
from dataclasses import dataclass

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .ocr import reply_message_id


@dataclass(frozen=True)
class VideoReference:
    message_id: int
    segment_index: int
    source_url: str


class RecentVideoStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._items: dict[str, tuple[float, int]] = {}

    def record(
        self,
        key: str,
        message_id: int,
        now: float | None = None,
    ) -> None:
        current_time = time.monotonic() if now is None else now
        self._items[key] = (current_time, int(message_id))

    def get(self, key: str, now: float | None = None) -> int | None:
        item = self._items.get(key)
        if item is None:
            return None
        current_time = time.monotonic() if now is None else now
        created_at, message_id = item
        if current_time - created_at > self._ttl_seconds:
            self._items.pop(key, None)
            return None
        return message_id


def contains_video(message: Message) -> bool:
    return any(segment.type == "video" for segment in message)


def indexed_video_sources(raw_message: object) -> list[tuple[int, str]]:
    if isinstance(raw_message, Message):
        items: list[object] = list(raw_message)
    elif isinstance(raw_message, list):
        items = raw_message
    else:
        return []
    sources: list[tuple[int, str]] = []
    for index, item in enumerate(items):
        if isinstance(item, MessageSegment):
            segment_type = item.type
            data = item.data
        elif isinstance(item, dict):
            segment_type = str(item.get("type") or "")
            raw_data = item.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
        else:
            continue
        if segment_type != "video":
            continue
        source = ""
        for key in ("url", "file"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                source = value.strip()
                break
        if source:
            sources.append((index, source))
    return sources


async def message_video_sources(
    bot: Bot,
    message_id: int,
) -> list[tuple[int, str]]:
    try:
        result = await bot.get_msg(message_id=int(message_id))
    except ActionFailed as exc:
        logger.warning(f"Could not read video message {message_id}: {exc}")
        return []
    if not isinstance(result, dict):
        return []
    return indexed_video_sources(result.get("message"))


def replied_video_message_id(message: Message) -> int | None:
    return reply_message_id(message)
