from __future__ import annotations

import asyncio
import os
import platform
import shutil
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .paths import CACHE_DIR

QQ_IMAGE_HOST_SUFFIXES = ("qq.com", "qq.com.cn", "qpic.cn")
MACOS_OCR_SCRIPT = Path(__file__).with_name("macos_ocr.swift")
MACOS_OCR_BINARY = CACHE_DIR / "macos_ocr"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_macos_compile_lock = asyncio.Lock()


class OCRError(RuntimeError):
    pass


class RecentImageStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._items: dict[str, tuple[float, list[str]]] = {}

    def record(
        self,
        key: str,
        sources: list[str],
        now: float | None = None,
    ) -> None:
        if not sources:
            return
        current_time = time.monotonic() if now is None else now
        self._items[key] = (current_time, list(sources))

    def get(self, key: str, now: float | None = None) -> list[str]:
        item = self._items.get(key)
        if item is None:
            return []
        current_time = time.monotonic() if now is None else now
        created_at, sources = item
        if current_time - created_at > self._ttl_seconds:
            self._items.pop(key, None)
            return []
        return list(sources)


def image_sources(message: Message, max_images: int = 2) -> list[str]:
    sources: list[str] = []
    for segment in message:
        if segment.type != "image":
            continue
        source = _preferred_image_source(segment.data)
        if source and source not in sources:
            sources.append(source)
        if len(sources) >= max_images:
            break
    return sources


def reply_message_id(message: Message) -> int | None:
    for segment in message:
        if segment.type != "reply":
            continue
        try:
            return int(str(segment.data.get("id", "")))
        except ValueError:
            return None
    return None


async def replied_image_sources(
    bot: Bot,
    message: Message,
    max_images: int = 2,
) -> list[str]:
    message_id = reply_message_id(message)
    if message_id is None:
        return []

    try:
        result = await bot.get_msg(message_id=message_id)
    except ActionFailed as exc:
        logger.warning(f"Could not read replied message for OCR: {exc}")
        return []
    if not isinstance(result, dict):
        return []

    raw_message = result.get("message")
    try:
        replied_message = _parse_api_message(raw_message)
    except (TypeError, ValueError):
        return []
    return image_sources(replied_message, max_images=max_images)


def _parse_api_message(raw_message: object) -> Message:
    if isinstance(raw_message, Message):
        return raw_message
    if isinstance(raw_message, str):
        return Message(raw_message)
    if not isinstance(raw_message, list):
        raise TypeError("Unsupported OneBot message payload.")

    segments: list[MessageSegment] = []
    for item in raw_message:
        if not isinstance(item, dict):
            continue
        segment_type = item.get("type")
        data = item.get("data")
        if isinstance(segment_type, str) and isinstance(data, dict):
            segments.append(MessageSegment(segment_type, data))
    return Message(segments)


async def recognize_images(
    bot: Bot,
    sources: list[str],
    timeout_seconds: int,
    max_chars: int,
) -> str:
    sections: list[str] = []
    for index, source in enumerate(sources, start=1):
        text = await recognize_image(bot, source, timeout_seconds)
        if text:
            sections.append(f"[图片 {index}]\n{text}")

    combined = "\n\n".join(sections).strip()
    if len(combined) > max_chars:
        return combined[:max_chars].rstrip() + "\n[OCR 内容过长，已截断]"
    return combined


async def recognize_image(bot: Bot, source: str, timeout_seconds: int) -> str:
    if platform.system() == "Darwin":
        return await _recognize_with_macos_vision(source, timeout_seconds)

    try:
        result = await bot.call_api("ocr_image", image=source)
    except ActionFailed as exc:
        raise OCRError(f"NapCat OCR failed: {exc}") from exc

    return _extract_ocr_text(result)


def _preferred_image_source(data: dict[str, object]) -> str:
    for key in ("url", "path", "file"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        source = value.strip()
        if key == "path" and Path(source).is_file():
            return source
        if source.startswith(("http://", "https://")):
            return source
        if key == "file":
            return source
    return ""


def _extract_ocr_text(result: object) -> str:
    lines: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
            for key in ("texts", "result", "data"):
                nested = value.get(key)
                if nested is not None:
                    collect(nested)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(result)
    return "\n".join(dict.fromkeys(lines))


async def _recognize_with_macos_vision(
    source: str, timeout_seconds: int
) -> str:
    executable = await _macos_ocr_executable()

    with TemporaryDirectory(prefix="qq-bot-ocr-") as directory:
        image_path = await _local_image_path(source, Path(directory), timeout_seconds)
        process = await asyncio.create_subprocess_exec(
            str(executable),
            str(image_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise OCRError("macOS Vision OCR timed out.") from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"macOS Vision OCR failed: {detail}")
            raise OCRError("macOS Vision OCR failed.")
        return stdout.decode("utf-8", errors="replace").strip()


async def _macos_ocr_executable() -> Path:
    if (
        MACOS_OCR_BINARY.is_file()
        and MACOS_OCR_BINARY.stat().st_mtime >= MACOS_OCR_SCRIPT.stat().st_mtime
    ):
        return MACOS_OCR_BINARY

    async with _macos_compile_lock:
        if (
            MACOS_OCR_BINARY.is_file()
            and MACOS_OCR_BINARY.stat().st_mtime >= MACOS_OCR_SCRIPT.stat().st_mtime
        ):
            return MACOS_OCR_BINARY

        swiftc = shutil.which("swiftc")
        if not swiftc or not MACOS_OCR_SCRIPT.exists():
            raise OCRError("The Swift compiler for macOS Vision OCR is unavailable.")

        MACOS_OCR_BINARY.parent.mkdir(parents=True, exist_ok=True)
        temporary_binary = MACOS_OCR_BINARY.with_suffix(".tmp")
        module_cache = MACOS_OCR_BINARY.parent / "swift-module-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["SWIFT_MODULECACHE_PATH"] = str(module_cache)
        environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache)

        process = await asyncio.create_subprocess_exec(
            swiftc,
            "-O",
            str(MACOS_OCR_SCRIPT),
            "-o",
            str(temporary_binary),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise OCRError("Compiling the macOS OCR helper timed out.") from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"Compiling macOS Vision OCR failed: {detail}")
            raise OCRError("Could not compile the macOS OCR helper.")

        temporary_binary.replace(MACOS_OCR_BINARY)
        MACOS_OCR_BINARY.chmod(0o700)
        return MACOS_OCR_BINARY


async def _local_image_path(
    source: str,
    directory: Path,
    timeout_seconds: int,
) -> Path:
    local_path = Path(source).expanduser()
    if local_path.is_file():
        return local_path

    parsed = urlsplit(source)
    if parsed.scheme not in {"http", "https"} or not _is_qq_image_host(
        parsed.hostname or ""
    ):
        raise OCRError("The image source is not a supported QQ image URL.")

    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(source)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OCRError("Could not download the QQ image.") from exc

    if len(response.content) > MAX_IMAGE_BYTES:
        raise OCRError("The image is too large for OCR.")

    image_path = directory / "image"
    image_path.write_bytes(response.content)
    return image_path


def _is_qq_image_host(hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    return any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in QQ_IMAGE_HOST_SUFFIXES
    )
