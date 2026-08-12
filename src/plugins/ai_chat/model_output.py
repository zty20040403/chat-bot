from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Union


@dataclass(frozen=True)
class ModelText:
    text: str


@dataclass(frozen=True)
class ModelMention:
    principal_id: int
    display: str


@dataclass(frozen=True)
class ModelFace:
    native_id: int
    display: str


@dataclass(frozen=True)
class ModelMediaReference:
    media_kind: str
    canonical_message_id: int
    segment_index: int | None
    display: str


ModelOutputNode = Union[
    ModelText,
    ModelMention,
    ModelFace,
    ModelMediaReference,
]


_TOKEN = re.compile(
    r"(?:\[(?:"
    r"(?P<mention_kind>mention|@)#(?P<mention_id>[1-9][0-9]*)"
    r"(?::\s*(?P<mention_display>[^\]\r\n]*))?"
    r"|face#(?P<face_id>[0-9]+)"
    r"(?::\s*(?P<face_display>[^\]\r\n]*))?"
    r"|(?P<media_kind>image|sticker|表情包)#"
    r"(?P<media_message_id>[1-9][0-9]*)"
    r"(?:\.(?P<media_segment>[0-9]+))?"
    r"(?::\s*(?P<media_display>[^\]\r\n]*))?"
    r")\]"
    r"|(?<![A-Za-z0-9._%+-])@#(?P<bare_mention_id>[1-9][0-9]*))"
)
_PROTECTED = re.compile(r"```[\s\S]*?(?:```|\Z)|`[^`\r\n]*(?:`|\Z)")
_CANONICAL_MENTION = re.compile(
    r"(?:\[(?:mention|@)#[1-9][0-9]*|@#[1-9][0-9]*)"
)


def may_contain_model_mention(text: str) -> bool:
    source = str(text)
    return bool(_CANONICAL_MENTION.search(source) or "@" in source)


def parse_model_output(
    text: str,
    *,
    roster: Iterable[tuple[str, int]] = (),
    self_principal_id: int | None = None,
) -> tuple[ModelOutputNode, ...]:
    """Parse model-authored transport tokens outside code spans."""

    names = _usable_roster(roster)
    display_by_principal = {
        principal_id: display for display, principal_id in names
    }
    nodes: list[ModelOutputNode] = []
    source = str(text)
    position = 0
    for protected in _PROTECTED.finditer(source):
        _parse_plain_region(
            source[position : protected.start()],
            nodes,
            names,
            display_by_principal,
            self_principal_id,
        )
        _append_text(nodes, protected.group(0))
        position = protected.end()
    _parse_plain_region(
        source[position:],
        nodes,
        names,
        display_by_principal,
        self_principal_id,
    )
    return tuple(nodes)


def _parse_plain_region(
    text: str,
    nodes: list[ModelOutputNode],
    names: tuple[tuple[str, int], ...],
    display_by_principal: dict[int, str],
    self_principal_id: int | None,
) -> None:
    position = 0
    for matched in _TOKEN.finditer(text):
        _parse_name_mentions(
            text[position : matched.start()],
            nodes,
            names,
            self_principal_id,
        )
        if (
            matched.group("mention_id") is not None
            or matched.group("bare_mention_id") is not None
        ):
            principal_id = int(
                matched.group("mention_id")
                or matched.group("bare_mention_id")
            )
            if principal_id != self_principal_id:
                display = (
                    (matched.group("mention_display") or "").strip()
                    or display_by_principal.get(principal_id)
                    or str(principal_id)
                )
                nodes.append(ModelMention(principal_id, display))
        elif matched.group("face_id") is not None:
            native_id = int(matched.group("face_id"))
            display = (matched.group("face_display") or "").strip()
            nodes.append(ModelFace(native_id, display))
        else:
            raw_kind = str(matched.group("media_kind"))
            nodes.append(
                ModelMediaReference(
                    "sticker" if raw_kind in {"sticker", "表情包"} else "image",
                    int(matched.group("media_message_id")),
                    (
                        int(matched.group("media_segment"))
                        if matched.group("media_segment") is not None
                        else None
                    ),
                    (matched.group("media_display") or "").strip(),
                )
            )
        position = matched.end()
    _parse_name_mentions(text[position:], nodes, names, self_principal_id)


def _parse_name_mentions(
    text: str,
    nodes: list[ModelOutputNode],
    names: tuple[tuple[str, int], ...],
    self_principal_id: int | None,
) -> None:
    if not names or "@" not in text:
        _append_text(nodes, text)
        return

    position = 0
    while True:
        marker = text.find("@", position)
        if marker < 0:
            _append_text(nodes, text[position:])
            return
        _append_text(nodes, text[position:marker])
        previous = text[marker - 1] if marker else ""
        if previous and previous.isascii() and (
            previous.isalnum() or previous in "._%+-"
        ):
            _append_text(nodes, "@")
            position = marker + 1
            continue

        candidate = text[marker + 1 :]
        resolved = next(
            (
                (display, principal_id)
                for display, principal_id in names
                if candidate.startswith(display)
            ),
            None,
        )
        if resolved is None:
            _append_text(nodes, "@")
            position = marker + 1
            continue

        display, principal_id = resolved
        if principal_id != self_principal_id:
            nodes.append(ModelMention(principal_id, display))
        position = marker + 1 + len(display)


def _usable_roster(
    roster: Iterable[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    candidates: dict[str, set[int]] = {}
    for raw_display, raw_principal_id in roster:
        display = str(raw_display).strip()
        try:
            principal_id = int(raw_principal_id)
        except (TypeError, ValueError):
            continue
        if (
            principal_id <= 0
            or len(display) < 2
            or display[0].isdigit()
            or "@" in display
        ):
            continue
        candidates.setdefault(display, set()).add(principal_id)
    unique = [
        (display, next(iter(principal_ids)))
        for display, principal_ids in candidates.items()
        if len(principal_ids) == 1
    ]
    return tuple(sorted(unique, key=lambda item: len(item[0]), reverse=True))


def _append_text(nodes: list[ModelOutputNode], text: str) -> None:
    if not text:
        return
    if nodes and isinstance(nodes[-1], ModelText):
        previous = nodes[-1]
        nodes[-1] = ModelText(previous.text + text)
    else:
        nodes.append(ModelText(text))
