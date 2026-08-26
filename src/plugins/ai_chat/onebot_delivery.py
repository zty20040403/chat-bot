"""Onebot Delivery responsibilities extracted from the plugin entrypoint."""

from __future__ import annotations

import asyncio
import time
import httpx
from nonebot import (
    get_bots,
    logger,
)
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.exception import (
    ActionFailed,
)
from .bridges import (
    BridgeError,
    BridgeOutcomeUnknown,
    BridgePermanentError,
    BridgeRetryableError,
)
from .config import (
    settings,
)
from .conversation_scope import (
    ConversationScope,
)
from .delivery import (
    Delivery,
)
from .observability import (
    telemetry,
)
from .onebot_codec import (
    record_onebot_outgoing,
    render_onebot_body,
)
from .reminders import (
    Reminder,
)


async def _deliver_reminder(bot: Bot, reminder: Reminder) -> None:
    text = f"提醒：{reminder.message}"
    if reminder.conversation_kind == "group":
        group_id = int(reminder.native_conversation_id)
        if not _is_group_enabled(group_id):
            logger.info(
                f"Dropping {reminder.handle}: target QQ group is disabled."
            )
            return
        segments = Message()
        try:
            creator_id = int(reminder.creator_native_user_id)
        except (TypeError, ValueError):
            creator_id = 0
        if creator_id > 0:
            segments.append(MessageSegment.at(creator_id))
            segments.append(MessageSegment.text(" "))
        segments.append(MessageSegment.text(text))
        response = await bot.send_group_msg(
            group_id=group_id,
            message=segments,
        )
    else:
        segments = Message(text)
        response = await bot.send_private_msg(
            user_id=int(reminder.native_conversation_id),
            message=segments,
        )

    native_message_id = _sent_message_id(response)
    if native_message_id is not None and message_ledger is not None:
        scope = ConversationScope(
            reminder.platform,
            reminder.conversation_kind,  # type: ignore[arg-type]
            reminder.native_conversation_id,
            actor_native_user_id=reminder.creator_native_user_id,
            bot_native_user_id=str(bot.self_id),
        )
        record_onebot_outgoing(
            message_ledger,
            scope,
            native_message_id=native_message_id,
            message=segments,
            occurred_at=int(time.time()),
        )


async def _reminder_loop() -> None:
    while True:
        await asyncio.sleep(settings.reminder_check_seconds)
        if reminder_store is None:
            continue
        bots = [bot for bot in get_bots().values() if isinstance(bot, Bot)]
        if not bots:
            continue
        bot = bots[0]
        for reminder in reminder_store.claim_due(limit=10):
            try:
                await _deliver_reminder(bot, reminder)
            except ActionFailed as exc:
                if _is_napcat_send_timeout(exc):
                    logger.warning(
                        f"Reminder {reminder.handle} send outcome is unknown; "
                        "marking it delivered to avoid a duplicate."
                    )
                    reminder_store.mark_sent(reminder.reminder_id)
                else:
                    reminder_store.mark_failed(reminder.reminder_id, str(exc))
                    logger.warning(f"Reminder {reminder.handle} send failed: {exc}")
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                reminder_store.mark_failed(reminder.reminder_id, str(exc))
                logger.warning(f"Reminder {reminder.handle} delivery failed: {exc}")
            else:
                reminder_store.mark_sent(reminder.reminder_id)


async def _deliver_onebot_outbox(bot: Bot, delivery: Delivery) -> None:
    if delivery.target_kind == "group" and not _is_group_enabled(
        int(delivery.target_native_conversation_id)
    ):
        raise BridgePermanentError("target QQ group is disabled")
    message = render_onebot_body(delivery.body)
    if delivery.reply_to_native_message_id:
        try:
            reply_id = int(delivery.reply_to_native_message_id)
        except (TypeError, ValueError):
            reply_id = 0
        if reply_id > 0:
            message = Message([MessageSegment.reply(reply_id), *message])
    if delivery.target_kind == "group":
        with telemetry.delivery(delivery.target_platform):
            response = await bot.send_group_msg(
                group_id=int(delivery.target_native_conversation_id),
                message=message,
            )
    else:
        with telemetry.delivery(delivery.target_platform):
            response = await bot.send_private_msg(
                user_id=int(delivery.target_native_conversation_id),
                message=message,
            )
    native_message_id = _sent_message_id(response)
    if delivery_store is not None:
        delivery_store.mark_committed(
            delivery.delivery_id,
            native_message_id=native_message_id or "",
        )
    if native_message_id is not None and mirror_state is not None:
        mirror_state.confirm_delivery(
            delivery.delivery_id,
            str(native_message_id),
        )
    is_mirror_delivery = bool(
        mirror_state is not None
        and mirror_state.is_mirror_delivery(delivery.delivery_id)
    )
    if is_mirror_delivery:
        return
    if native_message_id is not None and message_ledger is not None:
        scope = ConversationScope(
            delivery.target_platform,
            delivery.target_kind,  # type: ignore[arg-type]
            delivery.target_native_conversation_id,
            bot_native_user_id=str(bot.self_id),
        )
        stored = record_onebot_outgoing(
            message_ledger,
            scope,
            native_message_id=native_message_id,
            message=message,
            occurred_at=int(time.time()),
        )
        if (
            delivery.turn_id is not None
            and turn_journal is not None
        ):
            turn_journal.link_send(
                delivery.turn_id,
                stored.canonical_message_id,
                node_id=f"outbox:{delivery.delivery_id}",
            )
        if bridge_manager is not None:
            bridge_manager.mirror_local_outgoing(
                source_scope=scope,
                source_native_event_id=str(native_message_id),
                canonical_message_id=stored.canonical_message_id,
                body=delivery.body,
                occurred_at=int(time.time()),
                reply_to_native_message_id=delivery.reply_to_native_message_id,
            )


async def _delivery_loop() -> None:
    while True:
        await asyncio.sleep(settings.outbox_check_seconds)
        if delivery_store is None:
            continue
        expired = delivery_store.park_expired_attempts()
        if expired:
            logger.warning(
                f"Parked {expired} expired delivery lease(s) as ambiguous."
            )
        bots = [bot for bot in get_bots().values() if isinstance(bot, Bot)]
        for delivery in delivery_store.claim_due(limit=20):
            try:
                if delivery.target_platform == "onebot-v11":
                    if not bots:
                        raise BridgeRetryableError("OneBot 尚未连接")
                    await _deliver_onebot_outbox(bots[0], delivery)
                elif bridge_manager is not None:
                    native_id = await bridge_manager.deliver(delivery)
                    delivery_store.mark_committed(
                        delivery.delivery_id,
                        native_message_id=native_id,
                    )
                else:
                    raise BridgeRetryableError(
                        f"没有注册 {delivery.target_platform} 投递器"
                    )
            except ActionFailed as exc:
                if _is_napcat_send_timeout(exc):
                    delivery_store.mark_ambiguous(
                        delivery.delivery_id,
                        str(exc),
                    )
                    logger.warning(
                        f"{delivery.handle} timed out; waiting for echo "
                        "instead of retrying blindly."
                    )
                else:
                    delivery_store.mark_failed(
                        delivery.delivery_id,
                        str(exc),
                        retryable=False,
                    )
                    logger.warning(f"{delivery.handle} was rejected: {exc}")
            except BridgeOutcomeUnknown as exc:
                delivery_store.mark_ambiguous(delivery.delivery_id, str(exc))
                logger.warning(
                    f"{delivery.handle} outcome is unknown; waiting for echo."
                )
            except BridgePermanentError as exc:
                delivery_store.mark_failed(
                    delivery.delivery_id,
                    str(exc),
                    retryable=False,
                )
                logger.warning(f"{delivery.handle} was rejected: {exc}")
            except BridgeRetryableError as exc:
                delivery_store.mark_failed(
                    delivery.delivery_id,
                    str(exc),
                    retryable=True,
                    retry_seconds=min(30 * max(delivery.attempts, 1), 300),
                )
                logger.warning(f"{delivery.handle} will retry: {exc}")
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                delivery_store.mark_failed(
                    delivery.delivery_id,
                    str(exc),
                    retryable=True,
                    retry_seconds=min(30 * max(delivery.attempts, 1), 300),
                )
                logger.warning(f"{delivery.handle} delivery failed softly: {exc}")


async def _matrix_sync_loop() -> None:
    while True:
        if bridge_manager is None or bridge_manager.matrix is None:
            return
        try:
            processed = await bridge_manager.sync_matrix_once()
            if processed:
                logger.info(f"Matrix bridge ingested {processed} new event(s).")
        except asyncio.CancelledError:
            raise
        except (BridgeError, httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"Matrix sync failed without advancing its cursor: {exc}")
            await asyncio.sleep(settings.matrix_sync_retry_seconds)
