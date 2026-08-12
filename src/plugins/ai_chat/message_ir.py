from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal, Union


MediaKind = Literal["image", "sticker", "video", "audio", "file"]


@dataclass(frozen=True)
class TextNode:
    segment_index: int
    text: str


@dataclass(frozen=True)
class MentionNode:
    segment_index: int
    native_user_id: str
    display: str
    principal_id: int | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmoteNode:
    segment_index: int
    native_id: str
    name: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaNode:
    segment_index: int
    media_kind: MediaKind
    source: str = ""
    name: str = ""
    description: str = ""
    mime: str = ""
    source_type: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CardNode:
    segment_index: int
    title: str = ""
    url: str = ""
    source_type: str = "json"
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardNode:
    segment_index: int
    native_id: str
    count: int | None = None
    description: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UnsupportedNode:
    segment_index: int
    source_type: str
    description: str
    raw_data: dict[str, Any] = field(default_factory=dict)


MessageNode = Union[
    TextNode,
    MentionNode,
    EmoteNode,
    MediaNode,
    CardNode,
    ForwardNode,
    UnsupportedNode,
]


@dataclass(frozen=True)
class MessageBody:
    nodes: tuple[MessageNode, ...] = ()

    def has_media(self, kind: MediaKind | None = None) -> bool:
        return any(
            isinstance(node, MediaNode)
            and (kind is None or node.media_kind == kind)
            for node in self.nodes
        )


def resolve_mentions(
    body: MessageBody,
    resolver: Callable[[str, str], int | None],
) -> MessageBody:
    nodes: list[MessageNode] = []
    for node in body.nodes:
        if isinstance(node, MentionNode) and node.native_user_id != "all":
            principal_id = resolver(node.native_user_id, node.display)
            nodes.append(replace(node, principal_id=principal_id))
        else:
            nodes.append(node)
    return MessageBody(tuple(nodes))


def canonicalize_for_storage(body: MessageBody) -> MessageBody:
    """Remove transport-only inline bytes before canonical persistence."""
    nodes: list[MessageNode] = []
    for node in body.nodes:
        if not hasattr(node, "raw_data"):
            nodes.append(node)
            continue
        raw_data, omitted = _sanitize_stored_value(node.raw_data)
        if not isinstance(raw_data, dict):
            raw_data = {}
        if isinstance(node, MediaNode):
            raw_file = node.raw_data.get("file")
            source_is_inline = _is_inline_media_value(
                node.source
            ) or _is_inline_media_value(raw_file)
            source = "" if source_is_inline else node.source
            name = (
                ""
                if _is_inline_media_value(node.name) or source_is_inline
                else node.name
            )
            description = (
                "" if _is_inline_media_value(node.description) else node.description
            )
            if omitted and not description:
                description = f"内联媒体已从账本省略（sha256:{omitted[:12]}）"
            nodes.append(
                replace(
                    node,
                    source=source,
                    name=name,
                    description=description,
                    raw_data=raw_data,
                )
            )
            continue
        nodes.append(replace(node, raw_data=raw_data))
    return MessageBody(tuple(nodes))


def fallback_text(node: MessageNode) -> str:
    if isinstance(node, TextNode):
        return node.text
    if isinstance(node, MentionNode):
        if node.native_user_id == "all":
            return "@全体成员"
        return f"@{node.display or '群成员'}"
    if isinstance(node, EmoteNode):
        label = node.name or node.native_id
        return f"[QQ表情:{label}]"
    if isinstance(node, MediaNode):
        labels = {
            "image": "图片",
            "sticker": "表情包",
            "video": "视频",
            "audio": "语音",
            "file": "文件",
        }
        detail = node.description or node.name
        return f"[{labels[node.media_kind]}{':' + detail if detail else ''}]"
    if isinstance(node, CardNode):
        content = " - ".join(part for part in (node.title, node.url) if part)
        return f"[卡片{':' + content if content else ''}]"
    if isinstance(node, ForwardNode):
        detail = node.description or (
            f"{node.count} 条" if node.count is not None else ""
        )
        return f"[合并转发{':' + detail if detail else ''}]"
    return node.description or f"[{node.source_type}]"


def render_fallback_text(body: MessageBody) -> str:
    return "".join(fallback_text(node) for node in body.nodes).strip()


def render_prompt_text(
    body: MessageBody,
    canonical_message_id: int | None = None,
) -> str:
    parts: list[str] = []
    for node in body.nodes:
        if isinstance(node, MentionNode) and node.principal_id is not None:
            label = node.display or f"用户{node.principal_id}"
            parts.append(f"[mention#{node.principal_id}: {label}]")
            continue
        if isinstance(node, MediaNode) and canonical_message_id is not None:
            handle_kind = {
                "image": "image",
                "sticker": "sticker",
                "video": "video",
                "audio": "voice",
                "file": "file",
            }[node.media_kind]
            detail = node.description or node.name
            suffix = f": {detail}" if detail else ""
            parts.append(
                f"[{handle_kind}#{canonical_message_id}.{node.segment_index}{suffix}]"
            )
            continue
        if isinstance(node, EmoteNode):
            detail = f": {node.name}" if node.name else ""
            parts.append(f"[face#{node.native_id}{detail}]")
            continue
        parts.append(fallback_text(node))
    return "".join(parts).strip()


def body_to_json(body: MessageBody) -> str:
    return json.dumps(
        {"v": 1, "nodes": [_node_to_dict(node) for node in body.nodes]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def body_from_json(raw: str) -> MessageBody:
    try:
        payload = json.loads(raw, object_hook=_json_object_hook)
    except (TypeError, json.JSONDecodeError):
        return MessageBody()
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return MessageBody()
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        return MessageBody()
    nodes = [node for item in raw_nodes if (node := _node_from_dict(item))]
    return MessageBody(tuple(nodes))


def _node_to_dict(node: MessageNode) -> dict[str, Any]:
    if isinstance(node, TextNode):
        return {"type": "text", "index": node.segment_index, "text": node.text}
    if isinstance(node, MentionNode):
        return {
            "type": "mention",
            "index": node.segment_index,
            "native_user_id": node.native_user_id,
            "display": node.display,
            "principal_id": node.principal_id,
            "raw": _json_safe(node.raw_data),
        }
    if isinstance(node, EmoteNode):
        return {
            "type": "emote",
            "index": node.segment_index,
            "native_id": node.native_id,
            "name": node.name,
            "raw": _json_safe(node.raw_data),
        }
    if isinstance(node, MediaNode):
        return {
            "type": "media",
            "index": node.segment_index,
            "kind": node.media_kind,
            "source": node.source,
            "name": node.name,
            "description": node.description,
            "mime": node.mime,
            "source_type": node.source_type,
            "raw": _json_safe(node.raw_data),
        }
    if isinstance(node, CardNode):
        return {
            "type": "card",
            "index": node.segment_index,
            "title": node.title,
            "url": node.url,
            "source_type": node.source_type,
            "raw": _json_safe(node.raw_data),
        }
    if isinstance(node, ForwardNode):
        return {
            "type": "forward",
            "index": node.segment_index,
            "native_id": node.native_id,
            "count": node.count,
            "description": node.description,
            "raw": _json_safe(node.raw_data),
        }
    return {
        "type": "unsupported",
        "index": node.segment_index,
        "source_type": node.source_type,
        "description": node.description,
        "raw": _json_safe(node.raw_data),
    }


def _node_from_dict(raw: Any) -> MessageNode | None:
    if not isinstance(raw, dict):
        return None
    try:
        segment_index = max(int(raw.get("index") or 0), 0)
    except (TypeError, ValueError):
        return None
    node_type = raw.get("type")
    raw_data = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    if node_type == "text":
        return TextNode(segment_index, str(raw.get("text") or ""))
    if node_type == "mention":
        principal_id = raw.get("principal_id")
        try:
            parsed_principal = int(principal_id) if principal_id is not None else None
        except (TypeError, ValueError):
            parsed_principal = None
        return MentionNode(
            segment_index,
            str(raw.get("native_user_id") or ""),
            str(raw.get("display") or ""),
            parsed_principal,
            raw_data,
        )
    if node_type == "emote":
        return EmoteNode(
            segment_index,
            str(raw.get("native_id") or ""),
            str(raw.get("name") or ""),
            raw_data,
        )
    if node_type == "media" and raw.get("kind") in {
        "image",
        "sticker",
        "video",
        "audio",
        "file",
    }:
        return MediaNode(
            segment_index,
            raw["kind"],
            str(raw.get("source") or ""),
            str(raw.get("name") or ""),
            str(raw.get("description") or ""),
            str(raw.get("mime") or ""),
            str(raw.get("source_type") or ""),
            raw_data,
        )
    if node_type == "card":
        return CardNode(
            segment_index,
            str(raw.get("title") or ""),
            str(raw.get("url") or ""),
            str(raw.get("source_type") or "json"),
            raw_data,
        )
    if node_type == "forward":
        raw_count = raw.get("count")
        try:
            count = int(raw_count) if raw_count is not None else None
        except (TypeError, ValueError):
            count = None
        return ForwardNode(
            segment_index,
            str(raw.get("native_id") or ""),
            max(count, 0) if count is not None else None,
            str(raw.get("description") or ""),
            raw_data,
        )
    if node_type == "unsupported":
        return UnsupportedNode(
            segment_index,
            str(raw.get("source_type") or "unknown"),
            str(raw.get("description") or "[不支持的消息]"),
            raw_data,
        )
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _sanitize_stored_value(value: Any) -> tuple[Any, str]:
    if isinstance(value, bytes):
        digest = hashlib.sha256(value).hexdigest()
        return {"omitted_binary_bytes": len(value), "sha256": digest}, digest
    if isinstance(value, str) and _is_inline_media_value(value):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {"omitted_inline_text_bytes": len(value.encode("utf-8")), "sha256": digest}, digest
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        first_digest = ""
        for key, item in value.items():
            safe_item, digest = _sanitize_stored_value(item)
            sanitized[str(key)] = safe_item
            first_digest = first_digest or digest
        return sanitized, first_digest
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        first_digest = ""
        for item in value:
            safe_item, digest = _sanitize_stored_value(item)
            sanitized_items.append(safe_item)
            first_digest = first_digest or digest
        return sanitized_items, first_digest
    return value, ""


def _is_inline_media_value(value: Any) -> bool:
    if isinstance(value, bytes):
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    return normalized.startswith(("base64://", "data:"))


def _json_object_hook(value: dict[str, Any]) -> Any:
    encoded = value.get("__bytes__")
    if isinstance(encoded, str) and len(value) == 1:
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError:
            return b""
    return value
