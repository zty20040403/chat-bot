from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .message_ir import (
    CardNode,
    EmoteNode,
    ForwardNode,
    MediaNode,
    MentionNode,
    MessageBody,
    MessageNode,
    TextNode,
    UnsupportedNode,
    fallback_text,
)


Tier = Literal["native", "text", "drop"]


@dataclass(frozen=True)
class OutboundCapabilities:
    text: bool = True
    mention: Tier = "text"
    emote: Tier = "text"
    image: Tier = "text"
    sticker: Tier = "text"
    video: Tier = "text"
    audio: Tier = "text"
    file: Tier = "text"
    card: Tier = "text"
    max_text_bytes: int | None = None
    max_native_media: int = 0


@dataclass(frozen=True)
class LowerNote:
    segment_index: int
    node_kind: str
    action: str
    reason: str


@dataclass(frozen=True)
class LoweredMessage:
    chunks: tuple[MessageBody, ...]
    notes: tuple[LowerNote, ...]


ONEBOT_V11_CAPABILITIES = OutboundCapabilities(
    text=True,
    mention="native",
    emote="native",
    image="native",
    sticker="native",
    video="native",
    audio="native",
    file="native",
    card="native",
    max_text_bytes=None,
    max_native_media=20,
)


def lower_message(
    body: MessageBody,
    capabilities: OutboundCapabilities,
    *,
    destination_platform: str,
    origin_platform: str = "onebot-v11",
    mention_native: Callable[[MentionNode], str | None] | None = None,
) -> LoweredMessage:
    lowered: list[MessageNode] = []
    notes: list[LowerNote] = []
    native_media = 0
    for node in body.nodes:
        if isinstance(node, TextNode):
            if capabilities.text:
                lowered.append(node)
            else:
                notes.append(_note(node, "drop", "text capability disabled"))
            continue

        if isinstance(node, MentionNode):
            native_id = (
                mention_native(node)
                if mention_native is not None
                else node.native_user_id
                if destination_platform == origin_platform
                else None
            )
            if capabilities.mention == "native" and native_id:
                lowered.append(
                    MentionNode(
                        node.segment_index,
                        native_id,
                        node.display,
                        node.principal_id,
                        node.raw_data,
                    )
                )
            else:
                _degrade(node, capabilities.mention, lowered, notes, "mention")
            continue

        if isinstance(node, EmoteNode):
            same_platform = destination_platform == origin_platform
            if capabilities.emote == "native" and same_platform and node.native_id:
                lowered.append(node)
            else:
                _degrade(node, capabilities.emote, lowered, notes, "emote")
            continue

        if isinstance(node, MediaNode):
            tier = getattr(capabilities, node.media_kind)
            sendable = _media_sendable(node)
            within_budget = native_media < capabilities.max_native_media
            if tier == "native" and sendable and within_budget:
                lowered.append(node)
                native_media += 1
            else:
                reason = (
                    "media source unavailable"
                    if not sendable
                    else "native media budget exceeded"
                    if not within_budget
                    else "media capability is not native"
                )
                _degrade(
                    node,
                    "text" if tier == "native" else tier,
                    lowered,
                    notes,
                    reason,
                )
            continue

        if isinstance(node, CardNode):
            if capabilities.card == "native" and node.raw_data:
                lowered.append(node)
            else:
                _degrade(node, capabilities.card, lowered, notes, "card")
            continue

        if isinstance(node, (ForwardNode, UnsupportedNode)):
            _degrade(node, "text", lowered, notes, "non-wire canonical node")
            continue

    coalesced = _coalesce_text(lowered)
    chunks = _chunk_nodes(coalesced, capabilities.max_text_bytes)
    return LoweredMessage(tuple(MessageBody(chunk) for chunk in chunks), tuple(notes))


def _degrade(
    node: MessageNode,
    tier: Tier,
    lowered: list[MessageNode],
    notes: list[LowerNote],
    reason: str,
) -> None:
    if tier == "drop":
        notes.append(_note(node, "drop", reason))
        return
    lowered.append(TextNode(node.segment_index, fallback_text(node)))
    notes.append(_note(node, "text", reason))


def _note(node: MessageNode, action: str, reason: str) -> LowerNote:
    return LowerNote(
        segment_index=node.segment_index,
        node_kind=type(node).__name__,
        action=action,
        reason=reason,
    )


def _coalesce_text(nodes: list[MessageNode]) -> tuple[MessageNode, ...]:
    result: list[MessageNode] = []
    for node in nodes:
        if isinstance(node, TextNode) and result and isinstance(result[-1], TextNode):
            previous = result[-1]
            result[-1] = TextNode(previous.segment_index, previous.text + node.text)
        else:
            result.append(node)
    return tuple(result)


def _chunk_nodes(
    nodes: tuple[MessageNode, ...],
    max_text_bytes: int | None,
) -> tuple[tuple[MessageNode, ...], ...]:
    if not nodes:
        return ((),)
    if max_text_bytes is None or max_text_bytes <= 0:
        return (nodes,)
    chunks: list[list[MessageNode]] = [[]]
    used = 0
    for node in nodes:
        if not isinstance(node, TextNode):
            chunks[-1].append(node)
            continue
        remaining = node.text
        while remaining:
            room = max_text_bytes - used
            if room <= 0:
                chunks.append([])
                used = 0
                room = max_text_bytes
            part, remaining = _take_utf8_prefix(remaining, room)
            if not part:
                part, remaining = remaining[0], remaining[1:]
                chunks[-1].append(TextNode(node.segment_index, part))
                used = len(part.encode("utf-8"))
                if remaining:
                    chunks.append([])
                    used = 0
                continue
            chunks[-1].append(TextNode(node.segment_index, part))
            used += len(part.encode("utf-8"))
            if remaining:
                chunks.append([])
                used = 0
    return tuple(tuple(chunk) for chunk in chunks if chunk)


def _take_utf8_prefix(text: str, max_bytes: int) -> tuple[str, str]:
    if len(text.encode("utf-8")) <= max_bytes:
        return text, ""
    used = 0
    index = 0
    for index, character in enumerate(text):
        size = len(character.encode("utf-8"))
        if used + size > max_bytes:
            return text[:index], text[index:]
        used += size
    return text, ""


def _media_sendable(node: MediaNode) -> bool:
    if node.source:
        return True
    for key in ("file", "url", "file_id"):
        value = node.raw_data.get(key)
        if isinstance(value, bytes) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False
