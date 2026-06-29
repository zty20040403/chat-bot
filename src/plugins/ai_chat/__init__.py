from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from nonebot import get_bots, get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.rule import Rule, to_me

from .ai_tools import (
    READ_IMAGE_TEXT_TOOL_NAME,
    REPLY_WITH_VOICE_TOOL_NAME,
    TRANSCRIBE_VOICE_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    available_tools,
    force_tool,
)
from .config import settings
from .deepseek import DeepSeekConfigError, ask_deepseek, ask_deepseek_with_tools
from .identity import GroupUserProfileStore
from .memory import ConversationMemory, GroupContextMemory
from .ocr import (
    OCRError,
    RecentImageStore,
    image_sources,
    recognize_images,
    replied_image_sources,
    reply_message_id,
    wants_image_ocr,
)
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
    SearchResult,
    render_search_sources,
    search_freshness,
    search_web,
)
from .voice import (
    RecentVoiceStore,
    VoiceError,
    contains_voice,
    replied_voice_message_id,
    synthesize_silk_voice,
    transcribe_voice,
    wants_voice_transcription,
)

memory = ConversationMemory(settings.max_context_turns)
group_context = GroupContextMemory(
    settings.group_context_messages, settings.group_context_chars
)
user_profiles = GroupUserProfileStore(
    Path(__file__).parent / "assets" / "user_profiles.json"
)
recent_images = RecentImageStore(settings.ocr_recent_image_seconds)
recent_voices = RecentVoiceStore(settings.voice_recent_seconds)
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


def _image_cache_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}:user:{event.user_id}"
    return f"private:{event.user_id}"


def _voice_cache_key(event: MessageEvent) -> str:
    return _image_cache_key(event)


def _has_available_ocr_image(event: MessageEvent) -> bool:
    return bool(
        image_sources(event.original_message, max_images=1)
        or reply_message_id(event.original_message)
        or recent_images.get(_image_cache_key(event))
    )


def _has_available_voice(event: MessageEvent) -> bool:
    return bool(
        contains_voice(event.original_message)
        or reply_message_id(event.original_message)
        or recent_voices.get(_voice_cache_key(event))
    )


def _has_image_ocr_intent(event: MessageEvent) -> bool:
    return (
        settings.ocr_enabled
        and isinstance(event, GroupMessageEvent)
        and settings.is_group_enabled(event.group_id)
        and wants_image_ocr(event.message.extract_plain_text())
        and _has_available_ocr_image(event)
    )


def _has_voice_transcription_intent(event: MessageEvent) -> bool:
    return (
        settings.voice_enabled
        and isinstance(event, GroupMessageEvent)
        and settings.is_group_enabled(event.group_id)
        and wants_voice_transcription(event.message.extract_plain_text())
        and _has_available_voice(event)
    )


ai = on_command("ai", aliases={"ds", "deepseek", "问"}, priority=10, block=True)
web_search = on_command(
    "搜", aliases={"搜索", "联网搜索", "查一下", "search"}, priority=10, block=True
)
image_ocr = on_command(
    "ocr",
    aliases={"OCR", "识图", "图片文字", "看图"},
    priority=10,
    block=True,
)
image_ocr_request = on_message(
    rule=Rule(_has_image_ocr_intent),
    priority=15,
    block=True,
)
voice_answer = on_command(
    "语音",
    aliases={"语音回答", "voice"},
    priority=10,
    block=True,
)
voice_transcription = on_command(
    "听",
    aliases={"听语音", "语音识别", "语音转文字"},
    priority=10,
    block=True,
)
voice_transcription_request = on_message(
    rule=Rule(_has_voice_transcription_intent),
    priority=15,
    block=True,
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
    return event.sender.card or event.sender.nickname or f"QQ{event.user_id}"


def _sender_label(event: GroupMessageEvent) -> str:
    return f"{_sender_name(event)}（QQ {event.user_id}）"


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

    sections: list[str] = []
    profiles = user_profiles.render_group(event.group_id)
    if profiles:
        sections.append(f"[群成员身份记录]\n{profiles}")
    recent_messages = group_context.render(event.group_id)
    if recent_messages:
        sections.append(f"[最近群聊]\n{recent_messages}")
    return "\n\n".join(sections)


def _current_user_identity(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return user_profiles.describe_user(event.group_id, event.user_id)
    return f"QQ {event.user_id}"


def _conversation_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}:user:{event.user_id}"
    if isinstance(event, PrivateMessageEvent):
        return f"private:{event.user_id}"
    return f"unknown:{event.get_session_id()}"


def _rate_limit_key(event: MessageEvent) -> str:
    return _conversation_id(event)


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


def _reply_message(
    event: MessageEvent,
    content: Message | MessageSegment | str,
) -> Message:
    if isinstance(content, Message) and any(
        segment.type == "record" for segment in content
    ):
        return Message(content)
    if isinstance(content, MessageSegment) and content.type == "record":
        return Message([content])

    message = Message([MessageSegment.reply(event.message_id)])
    if isinstance(content, Message):
        message.extend(content)
    elif isinstance(content, MessageSegment):
        message.append(content)
    else:
        message.append(MessageSegment.text(content))
    return message


def _make_retry_message(message: Message | str) -> Message | str:
    if isinstance(message, str):
        return _make_retry_text(message)

    reply_segments = [
        segment for segment in message if segment.type == "reply"
    ]
    text = _make_retry_text(message.extract_plain_text())
    return Message([*reply_segments, MessageSegment.text(text)])


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

        if retry_on_timeout and isinstance(message, (Message, str)):
            logger.warning(
                f"NapCat timed out waiting for the {label} receipt; "
                "retrying once with a shorter QQ-safe reply."
            )
            await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
            try:
                await matcher.finish(_make_retry_message(message))
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


async def _finish_sticker(matcher, event: MessageEvent) -> None:
    try:
        await _finish_safely(
            matcher,
            _reply_message(event, random_sticker_message()),
            "sticker",
        )
    except ActionFailed as exc:
        logger.warning(f"Learned sticker send failed, fallback to local sticker: {exc}")
        await _finish_safely(
            matcher,
            _reply_message(event, random_local_sticker_message()),
            "local sticker",
        )


async def _finish_qq_face(
    matcher, event: MessageEvent, text: str = ""
) -> None:
    try:
        await _finish_safely(
            matcher,
            _reply_message(event, qq_face_message(text)),
            "QQ face",
        )
    except ActionFailed as exc:
        logger.warning(f"QQ builtin face send failed: {exc}")
        await _finish_safely(
            matcher,
            _reply_message(
                event,
                "这个 QQ 自带表情发不出去，换个 ID 试试。",
            ),
            "QQ face fallback",
        )


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
) -> Message | str:
    if isinstance(event, GroupMessageEvent) and not settings.is_group_enabled(event.group_id):
        return "这个群暂时没有开启 AI。"

    if len(user_text) > settings.max_input_chars:
        return f"问题太长了，先压到 {settings.max_input_chars} 个字符以内。"

    allowed, wait_seconds = rate_limiter.check(_rate_limit_key(event))
    if not allowed:
        return f"慢一点，{wait_seconds} 秒后再问我。"

    if force_search and not settings.search_enabled:
        return "联网搜索暂时没有开启。"
    if force_ocr and not settings.ocr_enabled:
        return "图片文字识别暂时没有开启。"
    if (force_voice_reply or force_voice_transcription) and not settings.voice_enabled:
        return "语音功能暂时没有开启。"

    conversation_id = _conversation_id(event)
    search_results: list[SearchResult] = []
    used_ocr_texts: list[str] = []
    used_voice_texts: list[str] = []
    voice_reply_segment: MessageSegment | None = None
    voice_reply_text = ""

    if available_image_sources is None and settings.ocr_enabled:
        available_image_sources = await _resolve_ocr_sources(bot, event)
    available_image_sources = available_image_sources or []

    should_resolve_voice = (
        force_voice_transcription
        or wants_voice_transcription(user_text)
        or contains_voice(event.original_message)
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
        include_image_ocr=(
            settings.ocr_enabled and bool(available_image_sources)
        ),
        include_voice_transcription=(
            settings.voice_enabled and available_voice_message_id is not None
        ),
        include_voice_reply=settings.voice_enabled,
    )

    async def execute_tool(name: str, arguments: dict[str, object]) -> str:
        nonlocal voice_reply_segment, voice_reply_text

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

        return json.dumps(
            {"ok": False, "error": f"不支持的工具：{name}"},
            ensure_ascii=False,
        )

    tool_choice = "auto"
    if force_search:
        tool_choice = force_tool(WEB_SEARCH_TOOL_NAME)
    elif force_ocr:
        tool_choice = force_tool(READ_IMAGE_TEXT_TOOL_NAME)
    elif force_voice_transcription:
        tool_choice = force_tool(TRANSCRIBE_VOICE_TOOL_NAME)
    elif force_voice_reply:
        tool_choice = force_tool(REPLY_WITH_VOICE_TOOL_NAME)

    try:
        answer = await ask_deepseek_with_tools(
            user_text,
            memory.get(conversation_id),
            tools,
            execute_tool,
            group_context=_current_group_context(event),
            current_user=_current_user_identity(event),
            tool_choice=tool_choice,
        )
    except DeepSeekConfigError:
        return "还没有配置 DEEPSEEK_API_KEY。"
    except RuntimeError as exc:
        logger.warning(f"DeepSeek request failed: {exc}")
        return "DeepSeek 暂时没回上来，等会儿再试。"
    except Exception as exc:
        logger.exception(f"Unexpected AI chat error: {exc}")
        return "我这边处理消息时出错了。"

    if not answer and not voice_reply_text:
        return "DeepSeek 没有返回内容。"

    answer = _trim_reply(voice_reply_text or answer)
    memory_user_text = user_text
    if used_ocr_texts:
        memory_user_text += "\n\n[图片 OCR]\n" + "\n\n".join(used_ocr_texts)
    if used_voice_texts:
        memory_user_text += "\n\n[语音转文字]\n" + "\n\n".join(used_voice_texts)
    memory.append_turn(conversation_id, memory_user_text, answer)
    if isinstance(event, GroupMessageEvent):
        group_context.append(event.group_id, "机器人", answer)

    if voice_reply_segment is not None:
        return Message([voice_reply_segment])

    reply = ai_reply_message(answer, user_text)
    sources = render_search_sources(search_results)
    if sources:
        return f"{reply}\n\n{sources}"
    return reply


async def _resolve_ocr_sources(
    bot: Bot,
    event: MessageEvent,
) -> list[str]:
    sources = image_sources(
        event.original_message,
        max_images=settings.ocr_max_images,
    )
    if not sources:
        sources = await replied_image_sources(
            bot,
            event.original_message,
            max_images=settings.ocr_max_images,
        )
    if not sources:
        sources = recent_images.get(_image_cache_key(event))
    if sources:
        recent_images.record(_image_cache_key(event), sources)
    return sources


async def _resolve_voice_message_id(
    bot: Bot,
    event: MessageEvent,
) -> int | None:
    if contains_voice(event.original_message):
        message_id = event.message_id
    else:
        message_id = await replied_voice_message_id(
            bot,
            event.original_message,
        )
    if message_id is None:
        message_id = recent_voices.get(_voice_cache_key(event))
    if message_id is not None:
        recent_voices.record(_voice_cache_key(event), message_id)
    return message_id


async def _finish_image_ocr(
    matcher,
    bot: Bot,
    event: MessageEvent,
    user_text: str,
) -> None:
    if not settings.ocr_enabled:
        await _finish_safely(
            matcher,
            _reply_message(event, "图片文字识别暂时没有开启。"),
        )
        return

    sources = await _resolve_ocr_sources(bot, event)
    if not sources:
        await _finish_safely(
            matcher,
            _reply_message(
                event,
                "请先发一张图片，5 分钟内再让我识图；也可以回复那张图片。",
            ),
        )
        return

    question = user_text.strip() or "请概括并解释图片中的文字。"
    await _finish_safely(
        matcher,
        _reply_message(
            event,
            await _ask_ai(
                bot,
                event,
                question,
                force_ocr=True,
                available_image_sources=sources,
            ),
        ),
        "OCR reply",
        retry_on_timeout=True,
    )


async def _finish_voice_transcription(
    matcher,
    bot: Bot,
    event: MessageEvent,
    user_text: str,
) -> None:
    if not settings.voice_enabled:
        await _finish_safely(
            matcher,
            _reply_message(event, "语音功能暂时没有开启。"),
        )
        return

    message_id = await _resolve_voice_message_id(bot, event)
    if message_id is None:
        await _finish_safely(
            matcher,
            _reply_message(
                event,
                "请先发一条语音，5 分钟内再让我听；也可以回复那条语音。",
            ),
        )
        return

    question = user_text.strip() or "请根据这条语音的内容自然回答。"
    await _finish_safely(
        matcher,
        _reply_message(
            event,
            await _ask_ai(
                bot,
                event,
                question,
                force_voice_transcription=True,
                available_voice_message_id=message_id,
            ),
        ),
        "voice transcription reply",
        retry_on_timeout=True,
    )


async def _generate_proactive_reply(
    event: GroupMessageEvent, latest_text: str
) -> str:
    prompt = (
        "这是一次QQ群主动接话判断。你没有被点名，请结合最近群聊和最后一条消息，"
        "判断现在插一句是否自然、有趣或有帮助。适合时只输出一条简短自然的群聊发言；"
        "不要复述规则，不要说“根据上下文”，不要@任何人，不要输出链接。"
        "如果话题与你无关、正在进行私人对话、仅凭现有信息接话会尴尬，"
        "就严格只输出 NO_REPLY。\n\n"
        f"最后一条消息：{_sender_label(event)}: {latest_text}"
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
async def handle_ai(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    user_text = args.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(
            ai,
            _reply_message(event, "用法：/ai 你的问题"),
        )

    await _finish_safely(
        ai,
        _reply_message(event, await _ask_ai(bot, event, user_text)),
        "AI reply",
        retry_on_timeout=True,
    )


@web_search.handle()
async def handle_web_search(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    user_text = args.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(
            web_search,
            _reply_message(event, "用法：/搜 关键词"),
        )

    await _finish_safely(
        web_search,
        _reply_message(
            event,
            await _ask_ai(bot, event, user_text, force_search=True),
        ),
        "search reply",
        retry_on_timeout=True,
    )


@image_ocr.handle()
async def handle_image_ocr(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    await _finish_image_ocr(
        image_ocr,
        bot,
        event,
        args.extract_plain_text().strip(),
    )


@image_ocr_request.handle()
async def handle_image_ocr_request(bot: Bot, event: MessageEvent) -> None:
    await _finish_image_ocr(
        image_ocr_request,
        bot,
        event,
        event.message.extract_plain_text().strip(),
    )


@voice_answer.handle()
async def handle_voice_answer(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    user_text = args.extract_plain_text().strip()
    if not user_text:
        await _finish_safely(
            voice_answer,
            _reply_message(event, "用法：/语音 你的问题"),
        )

    await _finish_safely(
        voice_answer,
        _reply_message(
            event,
            await _ask_ai(
                bot,
                event,
                user_text,
                force_voice_reply=True,
            ),
        ),
        "voice reply",
    )


@voice_transcription.handle()
async def handle_voice_transcription(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    await _finish_voice_transcription(
        voice_transcription,
        bot,
        event,
        args.extract_plain_text().strip(),
    )


@voice_transcription_request.handle()
async def handle_voice_transcription_request(
    bot: Bot,
    event: MessageEvent,
) -> None:
    await _finish_voice_transcription(
        voice_transcription_request,
        bot,
        event,
        event.message.extract_plain_text().strip(),
    )


@mention_ai.handle()
async def handle_mention_ai(bot: Bot, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return

    user_text = event.message.extract_plain_text().strip()
    if (
        settings.ocr_enabled
        and _has_available_ocr_image(event)
        and (not user_text or wants_image_ocr(user_text))
    ):
        await _finish_image_ocr(mention_ai, bot, event, user_text)

    if (
        settings.voice_enabled
        and _has_available_voice(event)
        and (not user_text or wants_voice_transcription(user_text))
    ):
        await _finish_voice_transcription(mention_ai, bot, event, user_text)

    if not user_text:
        await _finish_safely(
            mention_ai,
            _reply_message(event, "你想问什么？可以 @我 后面加问题。"),
        )

    if wants_qq_face(user_text):
        await _finish_qq_face(mention_ai, event, user_text)

    if wants_sticker(user_text):
        await _finish_sticker(mention_ai, event)

    await _finish_safely(
        mention_ai,
        _reply_message(event, await _ask_ai(bot, event, user_text)),
        "AI reply",
        retry_on_timeout=True,
    )


@group_activity_tracker.handle()
async def handle_group_activity(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.user_id == event.self_id:
        return
    if not settings.is_group_enabled(event.group_id):
        return

    user_profiles.observe(
        event.group_id,
        event.user_id,
        nickname=event.sender.nickname or "",
        card=event.sender.card or "",
    )
    sources = image_sources(
        event.original_message,
        max_images=settings.ocr_max_images,
    )
    if sources:
        recent_images.record(_image_cache_key(event), sources)
    if contains_voice(event.original_message):
        recent_voices.record(_voice_cache_key(event), event.message_id)
    if settings.warmup_enabled:
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
async def handle_sticker(event: MessageEvent) -> None:
    await _finish_sticker(sticker, event)


@qq_face.handle()
async def handle_qq_face(
    event: MessageEvent, args: Message = CommandArg()
) -> None:
    await _finish_qq_face(
        qq_face,
        event,
        args.extract_plain_text().strip(),
    )


@sticker_status.handle()
async def handle_sticker_status(event: MessageEvent) -> None:
    await _finish_safely(
        sticker_status,
        _reply_message(
            event,
            f"已学习 {learned_sticker_count()} 个 QQ 表情；"
            f"本地内置 {len(list_stickers())} 张图片表情。",
        ),
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

    group_context.append(event.group_id, _sender_label(event), text)


@ai_reset.handle()
async def handle_ai_reset(event: MessageEvent) -> None:
    conversation_id = _conversation_id(event)
    memory.clear(conversation_id)
    if isinstance(event, GroupMessageEvent):
        group_context.clear(event.group_id)
        proactive_scheduler.reset(event.group_id)
        await _finish_safely(
            ai_reset,
            _reply_message(event, "已清空当前会话记忆和群聊上下文。"),
        )
    await _finish_safely(
        ai_reset,
        _reply_message(event, "已清空当前会话记忆。"),
    )


@clear_data.handle()
async def handle_clear_data(event: MessageEvent) -> None:
    conversation_id = _conversation_id(event)
    memory.clear(conversation_id)

    cleared_items = ["当前会话记忆"]
    if isinstance(event, GroupMessageEvent):
        group_context.clear(event.group_id)
        proactive_scheduler.reset(event.group_id)
        cleared_items.append("当前群聊上下文")
        profile_count = user_profiles.clear_group(event.group_id)
        cleared_items.append(f"当前群成员身份 {profile_count} 个")

    sticker_count = clear_learned_stickers()
    cleared_items.append(f"自动学习表情 {sticker_count} 个")

    await _finish_safely(
        clear_data,
        _reply_message(
            event,
            "已清空：" + "、".join(cleared_items) + "。",
        ),
    )
