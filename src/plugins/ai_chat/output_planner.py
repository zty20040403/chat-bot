from __future__ import annotations

import re
from dataclasses import dataclass


MAX_REPLY_CHUNKS = 10
DEFAULT_SILENCE_FACE_ID = 7
PROCESSING_FACE_ID = 212
FAILURE_FACE_ID = 357
ACK_FACE_ID = 124

# NapCat QSid values. The names are deliberately curated so the model can
# select a reaction by meaning without ever choosing an arbitrary API value.
CURATED_FACE_IDS = {
    "微笑": 14,
    "呲牙": 13,
    "憨笑": 28,
    "偷笑": 20,
    "坏笑": 101,
    "笑哭": 182,
    "doge": 179,
    "得意": 4,
    "调皮": 12,
    "酷": 16,
    "可爱": 21,
    "卖萌": 175,
    "耶": 355,
    "赞": 76,
    "点赞": 201,
    "鼓掌": 99,
    "牛啊": 299,
    "666": 356,
    "崇拜": 318,
    "比心": 319,
    "庆祝": 320,
    "惊讶": 0,
    "疑问": 32,
    "惊恐": 26,
    "晕": 34,
    "吃瓜": 271,
    "暗中观察": 269,
    "摸鱼": 285,
    "嘘": 33,
    "流泪": 5,
    "大哭": 9,
    "委屈": 106,
    "可怜": 111,
    "难过": 15,
    "尴尬": 10,
    "擦汗": 97,
    "流汗": 27,
    "无奈": 174,
    "白眼": 22,
    "鄙视": 105,
    "嫌弃": 323,
    "面无表情": 284,
    "撇嘴": 1,
    "发怒": 11,
    "生气": 326,
    "裂开": 357,
    "捂脸": 264,
    "脑阔疼": 262,
    "困": 25,
    "哈欠": 104,
    "拥抱": 49,
    "贴贴": 350,
    "爱心": 66,
    "握手": 78,
    "抱拳": 118,
    "敬礼": 282,
    "拜托": 353,
    "OK": 124,
    "NO": 123,
    "收到": 428,
    "再见": 39,
    "托腮": 212,
    "闭嘴": DEFAULT_SILENCE_FACE_ID,
}

_QUOTE_HANDLE = re.compile(r"^\s*\[(?:reply|↩)#(?:msg)?(-?[0-9]+)\]\s*")
_REPLY_TOKEN = re.compile(
    r"\[(?:reply|↩)#(?:msg)?(?P<message_id>-?[0-9]+)"
    r"(?::[^\]\r\n]*)?\](?:\([^\)\r\n]*\))?[ \t]?"
)
_PROTECTED = re.compile(r"```[\s\S]*?(?:```|\Z)|`[^`\r\n]*(?:`|\Z)")
_STRONG_EMPHASIS = re.compile(
    r"(?P<mark>\*\*|__)(?P<body>[^\r\n]+?)(?P=mark)"
)
_SILENCE = re.compile(r"^\[(?:silence|沉默)(?:[:：]([^\]]+))?\]$")


@dataclass(frozen=True)
class PlannedChunk:
    text: str
    reply_message_id: int | None = None


@dataclass(frozen=True)
class ReplyPlan:
    chunks: tuple[PlannedChunk, ...]
    silence: bool = False
    silence_face_id: int | None = None
    silence_reply_message_id: int | None = None


def plan_reply(text: str) -> ReplyPlan:
    source = str(text).strip()
    quote_ids, silence_body = _extract_reply_handles(source)
    silence_match = _SILENCE.fullmatch(silence_body)
    if not source or silence_match is not None:
        reason = (
            silence_match.group(1).strip()
            if silence_match is not None and silence_match.group(1)
            else ""
        )
        return ReplyPlan(
            chunks=(),
            silence=True,
            silence_face_id=CURATED_FACE_IDS.get(
                reason,
                DEFAULT_SILENCE_FACE_ID,
            ),
            silence_reply_message_id=_first_positive(quote_ids),
        )

    chunks = [_planned_chunk(part) for part in _split_chunks(source)]
    chunks = [chunk for chunk in chunks if chunk.text]
    if len(chunks) > MAX_REPLY_CHUNKS:
        kept = chunks[: MAX_REPLY_CHUNKS - 1]
        overflow = chunks[MAX_REPLY_CHUNKS - 1 :]
        kept.append(
            PlannedChunk(
                text="\n\n".join(chunk.text for chunk in overflow),
                reply_message_id=overflow[0].reply_message_id,
            )
        )
        chunks = kept
    return ReplyPlan(chunks=tuple(chunks))


def split_leading_quote_handles(text: str) -> tuple[tuple[int, ...], str]:
    ids: list[int] = []
    remainder = text
    while True:
        matched = _QUOTE_HANDLE.match(remainder)
        if matched is None:
            break
        ids.append(int(matched.group(1)))
        remainder = remainder[matched.end() :]
    return tuple(ids), remainder.strip()


def face_prompt_table() -> str:
    return "、".join(f"{name}#{face_id}" for name, face_id in CURATED_FACE_IDS.items())


def _planned_chunk(text: str) -> PlannedChunk:
    ids, body = _extract_reply_handles(text.strip())
    return PlannedChunk(
        text=_strip_markdown_emphasis(body),
        reply_message_id=_first_positive(ids),
    )


def _extract_reply_handles(text: str) -> tuple[tuple[int, ...], str]:
    ids: list[int] = []
    parts: list[str] = []
    position = 0

    def strip_tokens(region: str) -> str:
        def replace(matched: re.Match[str]) -> str:
            ids.append(int(matched.group("message_id")))
            return ""

        return _REPLY_TOKEN.sub(replace, region)

    for protected in _PROTECTED.finditer(text):
        parts.append(strip_tokens(text[position : protected.start()]))
        parts.append(protected.group(0))
        position = protected.end()
    parts.append(strip_tokens(text[position:]))
    return tuple(ids), "".join(parts).strip()


def _first_positive(values: tuple[int, ...]) -> int | None:
    return next((value for value in values if value > 0), None)


def _strip_markdown_emphasis(text: str) -> str:
    parts: list[str] = []
    position = 0
    for protected in _PROTECTED.finditer(text):
        parts.append(
            _STRONG_EMPHASIS.sub(
                lambda match: match.group("body"),
                text[position : protected.start()],
            )
        )
        parts.append(protected.group(0))
        position = protected.end()
    parts.append(
        _STRONG_EMPHASIS.sub(
            lambda match: match.group("body"),
            text[position:],
        )
    )
    return "".join(parts)


def _split_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    code: list[str] = []
    in_fence = False

    def flush_current() -> None:
        value = "\n".join(current).strip()
        current.clear()
        if value:
            chunks.append(value)

    def flush_code() -> None:
        value = "\n".join(code).strip()
        code.clear()
        if value:
            chunks.append(value)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.lstrip().startswith("```"):
            if not in_fence:
                flush_current()
                in_fence = True
                code.append(line)
            else:
                code.append(line)
                in_fence = False
                flush_code()
            continue

        if in_fence:
            code.append(line)
            continue

        if not line.strip():
            flush_current()
            continue

        pieces = line.split("[split]")
        for index, piece in enumerate(pieces):
            if piece.strip():
                current.append(piece.strip() if len(pieces) > 1 else piece)
            if index < len(pieces) - 1:
                flush_current()

    if in_fence:
        flush_code()
    else:
        flush_current()
    return chunks
