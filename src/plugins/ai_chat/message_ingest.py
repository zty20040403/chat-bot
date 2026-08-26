"""Message Ingest responsibilities extracted from the plugin entrypoint."""

from __future__ import annotations

import asyncio
from src.bot_storage import (
    DatabaseError,
)
from nonebot import (
    logger,
)
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
)
from .config import (
    settings,
)
from .onebot_codec import (
    scope_from_event,
)
from .stickers import (
    learn_stickers_from_message,
)
async def handle_canonical_ingest(event: MessageEvent) -> None:
    await onebot_ingest_adapter.ingest(event)


async def handle_group_activity(event: MessageEvent) -> None:
    await onebot_ingest_adapter.observe_group_activity(event)


async def handle_image_auto_description(event: MessageEvent) -> None:
    if vision_worker is None:
        return
    if event.user_id == event.self_id:
        return
    if isinstance(event, GroupMessageEvent) and not _is_group_enabled(event.group_id):
        return
    if isinstance(event, GroupMessageEvent) and not _is_group_vision_auto_describe_enabled(
        event.group_id
    ):
        return
    if not isinstance(event, GroupMessageEvent) and not settings.vision_auto_describe:
        return
    if event.original_message.extract_plain_text().strip():
        return
    source_items = _indexed_image_sources(
        event.original_message,
        ordinary_only=True,
    )
    if not source_items:
        return
    segment_index, source_url = source_items[0]
    try:
        await asyncio.to_thread(
            vision_worker.submit,
            scope_key=scope_from_event(event).key,
            native_message_id=event.message_id,
            segment_index=segment_index,
            requester_native_user_id=event.user_id,
            source_url=source_url,
            mode="summary",
            delivery_target=scope_from_event(event),
            reply_to_native_message_id=event.message_id,
        )
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        logger.warning(f"Automatic image introduction failed: {exc}")
        return


async def handle_group_context_recorder(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.user_id == event.self_id:
        return
    if not _is_group_enabled(event.group_id):
        return

    learn_stickers_from_message(event.original_message)

    if message_ledger is not None:
        return

    text = _render_message_text(event.original_message)
    if not text or text.lstrip().startswith("/"):
        return

    group_context.append(
        event.group_id,
        _sender_label(event),
        text,
        timestamp=event.time,
        message_id=event.message_id,
    )
