from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from nonebot import get_bots, get_driver, logger, on_command, on_message
from nonebot.adapters import Message, MessageSegment
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.rule import to_me

from .config import settings
from .deepseek import DeepSeekConfigError, ask_deepseek
from .memory import ConversationMemory, GroupContextMemory
from .proactive import IdleWarmupScheduler, ProactiveChatScheduler
from .rate_limit import RateLimiter
from .stickers import (
    ai_reply_message,
    clear_learned_stickers,
    learn_stickers_from_message,
    learned_sticker_count,
    list_stickers,
    qq_face_message,
    random_local_sticker_message,
    random_sticker_message,
    wants_qq_face,
    wants_sticker,
)
from .web_search import (
    SearchError,
    render_search_context,
    render_search_sources,
    search_freshness,
    search_web,
    should_search,
)

memory = ConversationMemory(settings.max_context_turns)
group_context = GroupContextMemory(
    settings.group_context_messages, settings.group_context_chars
)
rate_limiter = RateLimiter(settings.rate_limit_seconds)
proactive_scheduler = ProactiveChatScheduler(
    min_messages=settings.proactive_min_messages,
    cooldown_seconds=settings.proactive_cooldown_seconds,
    chance_percent=settings.proactive_chance_percent,
)
idle_warmup_scheduler = IdleWarmupScheduler(
    idle_seconds=settings.warmup_idle_seconds,
    cooldown_seconds=settings.warmup_cooldown_seconds,
    daily_limit=settings.warmup_daily_limit,
    state_path=Path(__file__).parent / "assets" / "warmup_state.json",
)
SEND_RETRY_DELAY_SECONDS = 2.0
SEND_RETRY_MAX_CHARS = 800
_warmup_task: asyncio.Task[None] | None = None
driver = get_driver()

ai = on_command("ai", aliases={"ds", "deepseek", "问"}, priority=10, block=True)
web_search = on_command(
    "搜", aliases={"搜索", "联网搜索", "查一下", "search"}, priority=10, block=True
)
ai_reset = on_command("ai_reset", aliases={"清空记忆"}, priority=10, block=True)
clear_data = on_command(
    "clear",
    aliases={"清空上下文", "清空数据", "清空存储", "重置数据"},
    priority=10,
    block=True,
)
sticker = on_command(
    "表情", aliases={"表情包", "贴纸", "meme", "sticker"}, priority=10, block=True
)
qq_face = on_command(
    "qq表情",
    aliases={"QQ表情", "自带表情", "小黄脸", "face"},
    priority=10,
    block=True,
)
sticker_status = on_command(
    "表情状态", aliases={"表情库", "表情数量"}, priority=10, block=True
)
mention_ai = on_message(rule=to_me(), priority=20, block=True)
group_activity_tracker = on_message(priority=1, block=False)
proactive_chat = on_message(priority=80, block=False)
group_context_recorder = on_message(priority=90, block=False)


def _sender_name(event: GroupMessageEvent) -> str:
    return (
        event.sender.card
        or event.sender.nickname
        or f"QQ{event.user_id}"
    )


def _render_message_text(message: Message) -> str:
    parts: list[str] = []

    for segment in message:
        if segment.type == "text":
            parts.append(segment.data.get("text", ""))
        elif segment.type == "at":
            parts.append(f"@{segment.data.get('qq', '')}")
        elif segment.type == "face":
            raw = segment.data.get("raw")
            face_text = raw.get("faceText") if isinstance(raw, dict) else None
            parts.append(face_text or f"[QQ表情:{segment.data.get('id', '')}]")
        elif segment.type == "image":
            parts.append("[图片]")
        elif segment.type == "record":
            parts.append("[语音]")
        elif segment.type == "video":
            parts.append("[视频]")
        elif segment.type == "reply":
            parts.append("[回复]")
        else:
            parts.append(f"[{segment.type}]")

    return "".join(parts).strip()


def _current_group_context(event: MessageEvent) -> str:
    if not isinstance(event, GroupMessageEvent):
        return ""
    return group_context.render(event.group_id)


def _conversation_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    if isinstance(event, PrivateMessageEvent):
        return f"private:{event.user_id}"
    return f"unknown:{event.get_session_id()}"


def _rate_limit_key(event: MessageEvent) -> str:
    return f"{_conversation_id(event)}:user:{event.user_id}"


def _trim_reply(text: str) -> str:
    if len(text) <= settings.max_reply_chars:
        return text
    return text[: settings.max_reply_chars].rstrip() + "\n\n[回复太长，已截断]"


def _is_napcat_send_timeout(exc: ActionFailed) -> bool:
    info = getattr(exc, "info", {})
    if not isinstance(info, dict) or info.get("retcode") != 1200:
        return False

    detail = "\n".join(
        str(info.get(key, "")) for key in ("message", "wording")
    ).lower()
    return "timeout:" in detail and "sendmsg" in detail


def _make_retry_text(message: str) -> str:
    text = re.sub(r"https?://\S+", "[链接已省略]", message)
    if len(text) <= SEND_RETRY_MAX_CHARS:
        return text
    return text[:SEND_RETRY_MAX_CHARS].rstrip() + "\n\n[回复较长，已缩短]"


async def _finish_safely(
    matcher,
    message: Message | MessageSegment | str,
    label: str = "message",
    retry_on_timeout: bool = False,
) -> None:
    try:
        await matcher.finish(message)
    except ActionFailed as exc:
        if not _is_napcat_send_timeout(exc):
            raise

        if retry_on_timeout and isinstance(message, str):
            logger.warning(
                f"NapCat timed out waiting for the {label} receipt; "
                "retrying once with a shorter QQ-safe reply."
            )
            await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
            try:
                await matcher.finish(_make_retry_text(message))
            except ActionFailed as retry_exc:
                if not _is_napcat_send_timeout(retry_exc):
                    raise
                logger.error(f"NapCat timed out again while sending the {label}.")
                raise FinishedException
            raise FinishedException

        logger.warning(
            f"NapCat timed out waiting for the {label} receipt; "
            "the message may already have been sent, so it will not be retried."
        )
        raise FinishedException


async def _finish_sticker(matcher) -> None:
    try:
        await _finish_safely(matcher, random_sticker_message(), "sticker")
    except ActionFailed as exc:
        logger.warning(f"Learned sticker send failed, fallback to local sticker: {exc}")
        await _finish_safely(matcher, random_local_sticker_message(), "local sticker")


async def _finish_qq_face(matcher, text: str = "") -> None:
    try:
        await _finish_safely(matcher, qq_face_message(text), "QQ face")
    except ActionFailed as exc:
        logger.warning(f"QQ builtin face send failed: {exc}")
        await _finish_safely(
            matcher, "这个 QQ 自带表情发不出去，换个 ID 试试。", "QQ face fallback"
        )


async def _ask_ai(
    event: MessageEvent, user_text: str, force_search: bool = False
) -> Message | str:
    if isinstance(event, GroupMessageEvent) and not settings.is_group_enabled(event.group_id):
        return "这个群暂时没有开启 AI。"

    if len(user_text) > settings.max_input_chars:
        return f"问题太长了，先压到 {settings.max_input_chars} 个字符以内。"

    allowed, wait_seconds = rate_limiter.check(_rate_limit_key(event))
    if not allowed:
        return f"慢一点，{wait_seconds} 秒后再问我。"

    conversation_id = _conversation_id(event)
    search_results = []
    should_use_search = (
        settings.search_enabled
        and (force_search or (settings.search_auto_enabled and should_search(user_text)))
    )

    if should_use_search:
        try:
            search_results = await search_web(
                user_text,
                max_results=settings.search_max_results,
                timeout_seconds=settings.search_timeout_seconds,
                freshness=search_freshness(user_text),
            )
        except SearchError as exc:
            logger.warning(f"Web search failed: {exc}")
            if force_search:
                return "联网搜索失败了，可能是网络或搜索页面暂时不可用。"

        if force_search and not search_results:
            return "没搜到可用结果，换个关键词试试。"

    try:
        answer = await ask_deepseek(
            user_text,
            memory.get(conversation_id),
            _current_group_context(event),
            render_search_context(search_results),
        )
    except DeepSeekConfigError:
        return "还没有配置 DEEPSEEK_API_KEY。"
    except RuntimeError as exc:
        logger.warning(f"DeepSeek request failed: {exc}")
        return "DeepSeek 暂时没回上来，等会儿再试。"
    except Exception as exc:
        logger.exception(f"Unexpected AI chat error: {exc}")
        return "我这边处理消息时出错了。"

    if not answer:
        return "DeepSeek 没有返回内容。"

    answer = _trim_reply(answer)
    memory.append_turn(conversation_id, user_text, answer)
    if isinstance(event, GroupMessageEvent):
        group_context.append(event.group_id, "机器人", answer)

    reply = ai_reply_message(answer, user_text)
    sources = render_search_sources(search_results)
    if sources:
        return f"{reply}\n\n{sources}"
    return reply


async def _generate_proactive_reply(
    event: GroupMessageEvent, latest_text: str
) -> str:
    prompt = (
        "这是一次QQ群主动接话判断。你没有被点名，请结合最近群聊和最后一条消息，"
        "判断现在插一句是否自然、有趣或有帮助。适合时只输出一条简短自然的群聊发言；"
        "不要复述规则，不要说“根据上下文”，不要@任何人，不要输出链接。"
        "如果话题与你无关、正在进行私人对话、仅凭现有信息接话会尴尬，"
        "就严格只输出 NO_REPLY。\n\n"
        f"最后一条消息：{_sender_name(event)}: {latest_text}"
    )

    try:
        answer = await ask_deepseek(
            prompt,
            [],
            _current_group_context(event),
        )
    except DeepSeekConfigError:
        logger.warning("Proactive chat skipped: DEEPSEEK_API_KEY is not configured.")
        return ""
    except RuntimeError as exc:
        logger.warning(f"Proactive DeepSeek request failed: {exc}")
        return ""
    except Exception as exc:
        logger.exception(f"Unexpected proactive chat error: {exc}")
        return ""

    answer = answer.strip()
    if not answer or answer.upper().startswith("NO_REPLY"):
        return ""

    if len(answer) > settings.proactive_max_reply_chars:
        answer = answer[: settings.proactive_max_reply_chars].rstrip()

    group_context.append(event.group_id, "机器人", answer)
    return ai_reply_message(answer, latest_text)


async def _generate_warmup_reply(group_id: int) -> str:
    prompt = (
        "QQ群已经安静了一会儿。请以普通群友的口吻主动暖场，只输出一条自然、轻松、"
        "容易让人接话的中文消息，不超过80字。可以延续最近的轻松话题，也可以抛出一个"
        "简单有趣的问题；不要提到暖场、冷场、机器人、规则或沉默时长，不要@任何人，"
        "不要输出链接。如果最近话题敏感或私人，就换一个无害的新话题。"
    )
    try:
        answer = await ask_deepseek(prompt, [], group_context.render(group_id))
    except DeepSeekConfigError:
        logger.warning("Group warmup skipped: DEEPSEEK_API_KEY is not configured.")
        return ""
    except RuntimeError as exc:
        logger.warning(f"Warmup DeepSeek request failed: {exc}")
        return ""
    except Exception as exc:
        logger.exception(f"Unexpected group warmup error: {exc}")
        return ""

    answer = answer.strip()
    if not answer:
        return ""
    if len(answer) > settings.warmup_max_reply_chars:
        answer = answer[: settings.warmup_max_reply_chars].rstrip()
    return ai_reply_message(answer)


async def _send_group_message_safely(
    bot: Bot, group_id: int, message: str
) -> bool:
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
        return True
    except ActionFailed as exc:
        if not _is_napcat_send_timeout(exc):
            logger.warning(f"Group warmup send failed: {exc}")
            return False

    logger.warning(
        "NapCat timed out waiting for the warmup receipt; retrying once."
    )
    await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
    try:
        await bot.send_group_msg(
            group_id=group_id,
            message=_make_retry_text(message),
        )
        return True
    except ActionFailed as exc:
        if _is_napcat_send_timeout(exc):
            logger.error("NapCat timed out again while sending the group warmup.")
        else:
            logger.warning(f"Group warmup retry failed: {exc}")
        return False


def _is_warmup_quiet_hour(hour: int) -> bool:
    start = settings.warmup_quiet_start_hour
    end = settings.warmup_quiet_end_hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _warmup_loop() -> None:
    while True:
        await asyncio.sleep(settings.warmup_check_seconds)
        now = datetime.now()
        if _is_warmup_quiet_hour(now.hour):
            continue

        bots = [bot for bot in get_bots().values() if isinstance(bot, Bot)]
        if not bots:
            continue
        bot = bots[0]

        day = now.date().isoformat()
        for group_id in idle_warmup_scheduler.due_groups(day):
            reply = await _generate_warmup_reply(group_id)
            if not reply or not idle_warmup_scheduler.is_still_idle(group_id):
                continue

            idle_warmup_scheduler.mark_warmup(group_id, day)
            if await _send_group_message_safely(bot, group_id, reply):
                group_context.append(group_id, "机器人", reply)


@driver.on_startup
async def start_warmup_task() -> None:
    global _warmup_task
    if settings.warmup_enabled and _warmup_task is None:
        _warmup_task = asyncio.create_task(_warmup_loop())
        logger.info(
            "Idle group warmup enabled: "
            f"{settings.warmup_idle_seconds}s idle, "
            f"{settings.warmup_daily_limit} times per group per day."
        )


@driver.on_shutdown
async def stop_warmup_task() -> None:
    global _warmup_task
    if _warmup_task is None:
        return
    _warmup_task.cancel()
    try:
        await _warmup_task
    except asyncio.CancelledError:
        pass
    _warmup_task = None


@ai.handle()
async def handle_ai(event: MessageEvent, args: Message = CommandArg()) -> None:
    user_text = args.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(ai, "用法：/ai 你的问题")

    await _finish_safely(
        ai, await _ask_ai(event, user_text), "AI reply", retry_on_timeout=True
    )


@web_search.handle()
async def handle_web_search(event: MessageEvent, args: Message = CommandArg()) -> None:
    user_text = args.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(web_search, "用法：/搜 关键词")

    await _finish_safely(
        web_search,
        await _ask_ai(event, user_text, force_search=True),
        "search reply",
        retry_on_timeout=True,
    )


@mention_ai.handle()
async def handle_mention_ai(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return

    user_text = event.message.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(mention_ai, "你想问什么？可以 @我 后面加问题。")

    if wants_qq_face(user_text):
        await _finish_qq_face(mention_ai, user_text)

    if wants_sticker(user_text):
        await _finish_sticker(mention_ai)

    await _finish_safely(
        mention_ai,
        await _ask_ai(event, user_text),
        "AI reply",
        retry_on_timeout=True,
    )


@group_activity_tracker.handle()
async def handle_group_activity(event: MessageEvent) -> None:
    if not settings.warmup_enabled:
        return
    if not isinstance(event, GroupMessageEvent):
        return
    if event.user_id == event.self_id:
        return
    if not settings.is_group_enabled(event.group_id):
        return
    idle_warmup_scheduler.record_human_activity(event.group_id)


@proactive_chat.handle()
async def handle_proactive_chat(event: MessageEvent) -> None:
    if not settings.proactive_enabled:
        return
    if not isinstance(event, GroupMessageEvent):
        return
    if event.user_id == event.self_id:
        return
    if not settings.is_group_enabled(event.group_id):
        return

    latest_text = _render_message_text(event.original_message)
    if not proactive_scheduler.should_trigger(event.group_id, latest_text):
        return

    reply = await _generate_proactive_reply(event, latest_text)
    if not reply:
        return

    await _finish_safely(
        proactive_chat,
        reply,
        "proactive reply",
        retry_on_timeout=True,
    )


@sticker.handle()
async def handle_sticker() -> None:
    await _finish_sticker(sticker)


@qq_face.handle()
async def handle_qq_face(args: Message = CommandArg()) -> None:
    await _finish_qq_face(qq_face, args.extract_plain_text().strip())


@sticker_status.handle()
async def handle_sticker_status() -> None:
    await _finish_safely(
        sticker_status,
        f"已学习 {learned_sticker_count()} 个 QQ 表情；"
        f"本地内置 {len(list_stickers())} 张图片表情。",
    )


@group_context_recorder.handle()
async def handle_group_context_recorder(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.user_id == event.self_id:
        return
    if not settings.is_group_enabled(event.group_id):
        return

    learn_stickers_from_message(event.original_message)

    text = _render_message_text(event.original_message)
    if not text or text.lstrip().startswith("/"):
        return

    group_context.append(event.group_id, _sender_name(event), text)


@ai_reset.handle()
async def handle_ai_reset(event: MessageEvent) -> None:
    conversation_id = _conversation_id(event)
    memory.clear(conversation_id)
    if isinstance(event, GroupMessageEvent):
        group_context.clear(event.group_id)
        proactive_scheduler.reset(event.group_id)
        await _finish_safely(ai_reset, "已清空当前会话记忆和群聊上下文。")
    await _finish_safely(ai_reset, "已清空当前会话记忆。")


@clear_data.handle()
async def handle_clear_data(event: MessageEvent) -> None:
    conversation_id = _conversation_id(event)
    memory.clear(conversation_id)

    cleared_items = ["当前会话记忆"]
    if isinstance(event, GroupMessageEvent):
        group_context.clear(event.group_id)
        proactive_scheduler.reset(event.group_id)
        cleared_items.append("当前群聊上下文")

    sticker_count = clear_learned_stickers()
    cleared_items.append(f"自动学习表情 {sticker_count} 个")

    await _finish_safely(clear_data, "已清空：" + "、".join(cleared_items) + "。")
