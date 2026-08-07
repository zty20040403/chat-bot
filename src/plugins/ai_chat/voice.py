from __future__ import annotations

import asyncio
import re
import shutil
import time
import wave
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import edge_tts
import miniaudio
import pysilk
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .paths import CACHE_DIR

VOICE_CACHE_DIR = CACHE_DIR / "voice"


class VoiceError(RuntimeError):
    pass


class RecentVoiceStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._items: dict[str, tuple[float, int]] = {}

    def record(
        self,
        key: str,
        message_id: int,
        now: float | None = None,
    ) -> None:
        current_time = time.monotonic() if now is None else now
        self._items[key] = (current_time, message_id)

    def get(self, key: str, now: float | None = None) -> int | None:
        item = self._items.get(key)
        if item is None:
            return None
        current_time = time.monotonic() if now is None else now
        created_at, message_id = item
        if current_time - created_at > self._ttl_seconds:
            self._items.pop(key, None)
            return None
        return message_id


def contains_voice(message: Message) -> bool:
    return any(segment.type == "record" for segment in message)


async def replied_voice_message_id(
    bot: Bot,
    message: Message,
) -> int | None:
    message_id = _reply_message_id(message)
    if message_id is None:
        return None

    try:
        result = await bot.get_msg(message_id=message_id)
    except ActionFailed as exc:
        logger.warning(f"Could not read replied message for voice: {exc}")
        return None
    if not isinstance(result, dict):
        return None

    try:
        replied_message = _parse_api_message(result.get("message"))
    except (TypeError, ValueError):
        return None
    return message_id if contains_voice(replied_message) else None


async def transcribe_voice(
    bot: Bot,
    message_id: int,
    timeout_seconds: int,
) -> str:
    try:
        result = await asyncio.wait_for(
            bot.call_api("fetch_ptt_text", message_id=str(message_id)),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise VoiceError("NapCat voice transcription timed out.") from exc
    except ActionFailed as exc:
        raise VoiceError(f"NapCat voice transcription failed: {exc}") from exc

    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


async def synthesize_silk_voice(
    text: str,
    *,
    provider: str,
    voice_name: str,
    rate: str,
    pitch: str,
    local_voice_name: str,
    local_rate: int,
    max_chars: int,
    timeout_seconds: int,
) -> tuple[bytes, str]:
    speech_text = _prepare_speech_text(text, max_chars)
    if not speech_text:
        raise VoiceError("There is no text to synthesize.")

    if provider == "edge":
        try:
            silk = await _synthesize_edge_silk_voice(
                speech_text,
                voice_name=voice_name,
                rate=rate,
                pitch=pitch,
                timeout_seconds=timeout_seconds,
            )
            return silk, speech_text
        except VoiceError as exc:
            logger.warning(
                f"Online neural TTS failed, falling back to macOS voice: {exc}"
            )

    return await _synthesize_local_silk_voice(
        speech_text,
        voice_name=local_voice_name,
        rate=local_rate,
        max_chars=max_chars,
        timeout_seconds=timeout_seconds,
    )


async def _synthesize_edge_silk_voice(
    speech_text: str,
    *,
    voice_name: str,
    rate: str,
    pitch: str,
    timeout_seconds: int,
) -> bytes:
    communicate = edge_tts.Communicate(
        speech_text,
        voice_name,
        rate=rate,
        pitch=pitch,
    )

    async def collect_audio() -> bytes:
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data")
                if isinstance(data, bytes):
                    chunks.append(data)
        return b"".join(chunks)

    try:
        mp3 = await asyncio.wait_for(
            collect_audio(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise VoiceError("Online neural TTS timed out.") from exc
    except Exception as exc:
        raise VoiceError("Online neural TTS request failed.") from exc
    if not mp3:
        raise VoiceError("Online neural TTS returned empty audio.")

    try:
        decoded = await asyncio.to_thread(
            miniaudio.decode,
            mp3,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=24000,
        )
        pcm = bytes(decoded.samples)
    except Exception as exc:
        raise VoiceError("Could not decode neural TTS audio.") from exc
    if not pcm:
        raise VoiceError("Decoded neural TTS audio is empty.")

    silk = await asyncio.to_thread(_encode_tencent_silk, pcm, 24000)
    if not silk.startswith(b"\x02#!SILK_V3"):
        raise VoiceError("The local SILK encoder returned invalid audio.")
    return silk


async def _synthesize_local_silk_voice(
    text: str,
    *,
    voice_name: str,
    rate: int,
    max_chars: int,
    timeout_seconds: int,
) -> tuple[bytes, str]:
    speech_text = _prepare_speech_text(text, max_chars)
    if not speech_text:
        raise VoiceError("There is no text to synthesize.")

    say_path = shutil.which("say")
    afconvert_path = shutil.which("afconvert")
    if not say_path or not afconvert_path:
        raise VoiceError("macOS voice tools are unavailable.")

    VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="tts-", dir=VOICE_CACHE_DIR) as directory:
        aiff_path = Path(directory) / "reply.aiff"
        wav_path = Path(directory) / "reply.wav"
        say_process = await asyncio.create_subprocess_exec(
            say_path,
            "-v",
            voice_name,
            "-r",
            str(rate),
            "-o",
            str(aiff_path),
            speech_text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, say_stderr = await asyncio.wait_for(
                say_process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            say_process.kill()
            await say_process.communicate()
            raise VoiceError("macOS voice synthesis timed out.") from exc
        if say_process.returncode != 0:
            detail = say_stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"macOS voice synthesis failed: {detail}")
            raise VoiceError("macOS voice synthesis failed.")
        if not aiff_path.is_file() or aiff_path.stat().st_size <= 4096:
            raise VoiceError("macOS voice synthesis produced empty audio.")

        convert_process = await asyncio.create_subprocess_exec(
            afconvert_path,
            "-f",
            "WAVE",
            "-d",
            "LEI16@24000",
            "-c",
            "1",
            str(aiff_path),
            str(wav_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, convert_stderr = await asyncio.wait_for(
                convert_process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            convert_process.kill()
            await convert_process.communicate()
            raise VoiceError("Converting synthesized voice to PCM timed out.") from exc
        if convert_process.returncode != 0:
            detail = convert_stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"Converting synthesized voice to PCM failed: {detail}")
            raise VoiceError("Could not convert synthesized voice to PCM.")

        pcm, sample_rate = _read_pcm(wav_path)
        silk = await asyncio.to_thread(_encode_tencent_silk, pcm, sample_rate)
        if not silk.startswith(b"\x02#!SILK_V3"):
            raise VoiceError("The local SILK encoder returned invalid audio.")
        return silk, speech_text


def _read_pcm(wav_path: Path) -> tuple[bytes, int]:
    try:
        with wave.open(str(wav_path), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getcomptype() != "NONE"
            ):
                raise VoiceError("Synthesized WAV is not mono 16-bit PCM.")
            sample_rate = wav_file.getframerate()
            pcm = wav_file.readframes(wav_file.getnframes())
    except (OSError, wave.Error) as exc:
        raise VoiceError("Could not read synthesized PCM audio.") from exc
    if not pcm:
        raise VoiceError("Synthesized PCM audio is empty.")
    return pcm, sample_rate


def _encode_tencent_silk(pcm: bytes, sample_rate: int) -> bytes:
    source = BytesIO(pcm)
    output = BytesIO()
    try:
        pysilk.encode(
            source,
            output,
            sample_rate=sample_rate,
            bit_rate=24000,
            max_internal_sample_rate=24000,
            tencent=True,
        )
    except Exception as exc:
        raise VoiceError("Local Tencent SILK encoding failed.") from exc
    return output.getvalue()


def _prepare_speech_text(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"```.*?```", "代码内容略", text, flags=re.DOTALL)
    cleaned = re.sub(r"https?://\S+", "这里有一个链接", cleaned)
    cleaned = re.sub(r"[#*_`~>|]", "", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "，后面的内容有点长，先说到这里。"
    return cleaned


def _reply_message_id(message: Message) -> int | None:
    for segment in message:
        if segment.type != "reply":
            continue
        try:
            return int(str(segment.data.get("id", "")))
        except ValueError:
            return None
    return None


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
