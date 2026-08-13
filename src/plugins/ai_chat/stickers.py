from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from random import choice, sample
from urllib.parse import urlsplit, urlunsplit

from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment

from src.bot_storage import StateSource, open_json_state

from .paths import STATE_DIR

STICKER_DIR = Path(__file__).parent / "assets" / "stickers"
LEARNED_STICKERS_PATH = STATE_DIR / "learned_stickers.json"
LEARNED_STICKERS_NAMESPACE = "learned_stickers"
_learned_stickers_lock = threading.RLock()
_learned_stickers_state = open_json_state(
    LEARNED_STICKERS_PATH,
    LEARNED_STICKERS_NAMESPACE,
)
STICKER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
LEARNABLE_SEGMENT_TYPES = {"face", "image"}
MAX_STABLE_FACE_ID = 348
EXTENDED_STABLE_FACE_IDS = frozenset({350, 353, 355, 356, 357, 428})
QQ_BUILTIN_FACE_IDS = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    51,
    52,
    53,
    62,
    63,
    64,
    65,
    66,
    67,
    68,
    69,
    70,
    71,
)
QQ_FACE_ALIASES = {
    "微笑": 14,
    "笑": 14,
    "偷笑": 19,
    "可爱": 20,
    "白眼": 21,
    "困": 24,
    "憨笑": 27,
    "疑问": 31,
    "嘘": 32,
    "晕": 33,
    "再见": 38,
    "鼓掌": 41,
    "坏笑": 43,
    "亲亲": 51,
    "可怜": 53,
    "爱心": 65,
    "心碎": 66,
}
AI_REPLY_KAOMOJI_RULES = (
    (("危险", "违法", "风险", "小心", "注意", "隐私", "安全", "不要这样"), ("(｀・ω・´)", "(;｀・ω・´)")),
    (("不确定", "不知道", "不清楚", "可能", "也许", "大概", "需要查证", "联网"), ("(・・?)", "(｡•́︿•̀｡)")),
    (("抱歉", "失败", "错误", "出错", "不能", "不行", "难过", "心碎"), ("(；へ：)", "(っ- ‸ - ς)")),
    (("哈哈", "欸嘿", "可爱", "开心", "喜欢", "好耶", "捏", "嘛"), ("(*´▽`*)", "(≧▽≦)", "(｡･ω･｡)")),
    (("可以", "当然", "没问题", "好了", "完成", "不错", "恭喜"), ("(・∀・)", "٩(ˊᗜˋ*)و", "(*'▽'*)")),
    (("代码", "函数", "算法", "递归", "linux", "arch", "bug", "报错", "程序"), ("(｀・ω・´)", "(ง •̀_•́)ง")),
    (("为什么", "怎么", "什么", "如何", "吗", "？", "?"), ("(・・?)", "(｡･ω･｡)?")),
)
AI_REPLY_DEFAULT_KAOMOJIS = ("(*´▽`*)", "(・ω・)", "(｡･ω･｡)", "(*'▽'*)")
QQ_FACE_KEYWORDS = (
    "qq表情",
    "qq自带表情",
    "自带表情",
    "小黄脸",
    "face",
)


def configure_learned_sticker_state(source: StateSource) -> None:
    global _learned_stickers_state
    with _learned_stickers_lock:
        _learned_stickers_state = open_json_state(
            source,
            LEARNED_STICKERS_NAMESPACE,
        )


def _load_learned_stickers() -> list[dict[str, object]]:
    with _learned_stickers_lock:
        data = _learned_stickers_state.load()

    return data if isinstance(data, list) else []


def _save_learned_stickers(stickers: list[dict[str, object]]) -> None:
    with _learned_stickers_lock:
        _learned_stickers_state.save(stickers)


def _sticker_key(sticker: dict[str, object]) -> str:
    segment_type = str(sticker.get("type", ""))
    data = sticker.get("data")
    if not isinstance(data, dict):
        return segment_type

    if segment_type == "face":
        return f"face:{data.get('id')}"
    if segment_type == "image":
        return f"image:{data.get('file') or data.get('url')}"
    return json.dumps(sticker, sort_keys=True, ensure_ascii=False)


def _clean_segment_data(data: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, value in data.items():
        if key == "raw":
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key] = value
    return cleaned


def learn_stickers_from_message(message: Message) -> int:
    with _learned_stickers_lock:
        learned = _load_learned_stickers()
        known_keys = {_sticker_key(sticker) for sticker in learned}
        added = 0

        for segment in message:
            if segment.type not in LEARNABLE_SEGMENT_TYPES:
                continue

            data = _clean_segment_data(segment.data)
            if segment.type == "image":
                image_url = data.get("url")
                if not (
                    isinstance(image_url, str)
                    and image_url.startswith(("http://", "https://"))
                ):
                    continue
                data = {"url": image_url}

            if segment.type == "face":
                face_id = _stable_face_id(data.get("id"))
                if face_id is None:
                    continue
                data = {"id": str(face_id)}

            sticker = {"type": segment.type, "data": data}
            key = _sticker_key(sticker)
            if key in known_keys:
                continue

            learned.append(sticker)
            known_keys.add(key)
            added += 1

        if added:
            _save_learned_stickers(learned)

        return added


def learned_sticker_count() -> int:
    return len(_load_learned_stickers())


def sticker_inventory() -> dict[str, object]:
    items: list[dict[str, object]] = []
    learned_faces = 0
    learned_images = 0

    for index, sticker in enumerate(_load_learned_stickers(), start=1):
        segment_type = str(sticker.get("type", ""))
        data = sticker.get("data")
        if not isinstance(data, dict):
            continue

        if segment_type == "face":
            face_id = _stable_face_id(data.get("id"))
            if face_id is None:
                continue
            learned_faces += 1
            items.append(
                {
                    "inventory_id": f"learned-{index}",
                    "source": "learned",
                    "kind": "qq-face",
                    "name": f"QQ 表情 #{face_id}",
                    "reference": str(face_id),
                    "size_bytes": None,
                }
            )
            continue

        if segment_type == "image":
            image_url = data.get("url")
            if not isinstance(image_url, str) or not image_url.startswith(
                ("http://", "https://")
            ):
                continue
            learned_images += 1
            items.append(
                {
                    "inventory_id": f"learned-{index}",
                    "source": "learned",
                    "kind": "image",
                    "name": _image_reference_name(image_url),
                    "reference": _safe_image_reference(image_url),
                    "size_bytes": None,
                }
            )

    local_stickers = list_stickers()
    for index, sticker_path in enumerate(local_stickers, start=1):
        try:
            size_bytes: int | None = sticker_path.stat().st_size
        except OSError:
            size_bytes = None
        items.append(
            {
                "inventory_id": f"local-{index}",
                "source": "local",
                "kind": "image",
                "name": sticker_path.name,
                "reference": sticker_path.name,
                "size_bytes": size_bytes,
            }
        )

    return {
        "counts": {
            "total": len(items),
            "learned_faces": learned_faces,
            "learned_images": learned_images,
            "local_images": len(local_stickers),
        },
        "items": items,
    }


def clear_learned_stickers() -> int:
    count = learned_sticker_count()
    _save_learned_stickers([])
    return count


def _safe_image_reference(image_url: str) -> str:
    parsed = urlsplit(image_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:500]


def _image_reference_name(image_url: str) -> str:
    parsed = urlsplit(image_url)
    filename = Path(parsed.path).name
    if filename:
        return filename[:160]
    return (parsed.netloc or "QQ 图片表情")[:160]


def _message_from_learned_sticker(sticker: dict[str, object]) -> MessageSegment | None:
    segment_type = str(sticker.get("type", ""))
    data = sticker.get("data")
    if not isinstance(data, dict):
        return None

    if segment_type == "face":
        face_id = _stable_face_id(data.get("id"))
        if face_id is None:
            return None
        return MessageSegment.face(face_id)

    if segment_type == "image":
        image_url = data.get("url")
        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
            return MessageSegment.image(image_url)
        return None

    return None


def _stable_face_id(value: object) -> int | None:
    try:
        face_id = int(str(value))
    except (TypeError, ValueError):
        return None

    if (
        0 <= face_id <= MAX_STABLE_FACE_ID
        or face_id in EXTENDED_STABLE_FACE_IDS
    ):
        return face_id
    return None


def random_learned_sticker_message() -> MessageSegment | None:
    learned = _load_learned_stickers()
    if not learned:
        return None

    for sticker in sample(learned, k=len(learned)):
        message = _message_from_learned_sticker(sticker)
        if message is not None:
            return message

    return None


def list_stickers() -> list[Path]:
    if not STICKER_DIR.exists():
        return []

    return sorted(
        path
        for path in STICKER_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in STICKER_EXTENSIONS
    )


def random_sticker() -> Path | None:
    stickers = list_stickers()
    if not stickers:
        return None
    return choice(stickers)


def random_local_sticker_message() -> MessageSegment | str:
    sticker = random_sticker()
    if sticker is None:
        return f"还没有表情包图片，先往 {STICKER_DIR} 放 png/jpg/gif/webp。"
    return MessageSegment.image(sticker.read_bytes())


def random_sticker_message() -> MessageSegment | str:
    learned_sticker = random_learned_sticker_message()
    if learned_sticker is not None:
        return learned_sticker

    return random_local_sticker_message()


def qq_face_message(text: str = "") -> MessageSegment | str:
    face_id = _requested_face_id(text)
    if face_id is None:
        face_id = choice(QQ_BUILTIN_FACE_IDS)

    stable_face_id = _stable_face_id(face_id)
    if stable_face_id is None:
        extended = "、".join(str(item) for item in sorted(EXTENDED_STABLE_FACE_IDS))
        return (
            f"QQ 自带表情 ID 只支持 0 到 {MAX_STABLE_FACE_ID}，"
            f"以及扩展 ID {extended}。"
        )

    return MessageSegment.face(stable_face_id)


def ai_reply_message(answer: str, user_text: str = "") -> str:
    text = answer.rstrip()
    if not text:
        return answer

    # Keep the closing fence on its own line so the host can recognize and
    # render the block. Normal conversational replies still receive kaomoji.
    if text.splitlines()[-1].strip() == "```":
        return text

    return f"{text} {choose_ai_reply_kaomoji(answer, user_text)}"


def choose_ai_reply_kaomoji(answer: str, user_text: str = "") -> str:
    combined_text = f"{user_text}\n{answer}".lower()

    for keywords, kaomojis in AI_REPLY_KAOMOJI_RULES:
        if any(keyword.lower() in combined_text for keyword in keywords):
            return choice(kaomojis)

    return choice(AI_REPLY_DEFAULT_KAOMOJIS)


def _requested_face_id(text: str) -> int | None:
    normalized = text.strip().lower()
    if not normalized or normalized in {"随机", "random", "r"}:
        return None

    for keyword in QQ_FACE_KEYWORDS:
        normalized = normalized.replace(keyword, " ")

    normalized = normalized.strip()
    if not normalized or normalized in {"随机", "random", "r", "发", "来个", "发个"}:
        return None

    if normalized in QQ_FACE_ALIASES:
        return QQ_FACE_ALIASES[normalized]

    matched_number = re.search(r"\d+", normalized)
    if matched_number:
        return int(matched_number.group(0))

    return None
