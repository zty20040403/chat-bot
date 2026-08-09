from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


_BVID = re.compile(r"BV[0-9A-Za-z]{10}")
_AVID = re.compile(r"(?:bilibili\.com/video/)?av([0-9]+)", re.IGNORECASE)
_B23 = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.IGNORECASE)


class BilibiliError(RuntimeError):
    pass


@dataclass(frozen=True)
class BilibiliRef:
    bvid: str = ""
    aid: int = 0
    short_url: str = ""


class BilibiliClient:
    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(int(timeout_seconds), 5)),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
                "Referer": "https://www.bilibili.com/",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def inspect(self, value: str, *, comment_count: int = 10) -> dict[str, object]:
        ref = find_bilibili_ref(value)
        if ref is None:
            raise BilibiliError("没有找到 BV 号、av 号或 b23.tv 视频短链")
        if ref.short_url:
            ref = await self._resolve_short(ref.short_url)
        params = {"bvid": ref.bvid} if ref.bvid else {"aid": ref.aid}
        payload = await self._get_json(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
        )
        data = _api_data(payload)
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        aid = _safe_int(data.get("aid"))
        comments: object
        try:
            comment_payload = await self._get_json(
                "https://api.bilibili.com/x/v2/reply",
                params={
                    "type": 1,
                    "sort": 2,
                    "ps": min(max(int(comment_count), 0), 20),
                    "oid": aid,
                },
            )
            comment_data = _api_data(comment_payload)
            raw_replies = comment_data.get("replies")
            replies = raw_replies if isinstance(raw_replies, list) else []
            comments = [
                {
                    "user": str(
                        (item.get("member") or {}).get("uname")
                        if isinstance(item.get("member"), dict)
                        else ""
                    ),
                    "likes": _safe_int(item.get("like")),
                    "text": str(
                        (item.get("content") or {}).get("message")
                        if isinstance(item.get("content"), dict)
                        else ""
                    )[:300],
                }
                for item in replies[: min(max(int(comment_count), 0), 20)]
                if isinstance(item, dict)
            ]
        except (BilibiliError, httpx.HTTPError):
            comments = []
        return {
            "bvid": str(data.get("bvid") or ""),
            "aid": aid,
            "cid": _safe_int(data.get("cid")),
            "title": str(data.get("title") or ""),
            "description": str(data.get("desc") or "")[:1200],
            "uploader": str(owner.get("name") or ""),
            "duration_seconds": _safe_int(data.get("duration")),
            "published_at": _safe_int(data.get("pubdate")),
            "parts": _safe_int(data.get("videos"), 1),
            "stats": {
                key: _safe_int(stat.get(key))
                for key in (
                    "view",
                    "danmaku",
                    "reply",
                    "favorite",
                    "coin",
                    "share",
                    "like",
                )
            },
            "top_comments": comments,
            "url": f"https://www.bilibili.com/video/{data.get('bvid') or ''}",
        }

    async def _resolve_short(self, url: str) -> BilibiliRef:
        response = await self.client.get(url)
        if response.status_code >= 400:
            raise BilibiliError(f"b23.tv 短链解析失败：HTTP {response.status_code}")
        final = str(response.url)
        host = (urlparse(final).hostname or "").casefold()
        if host != "bilibili.com" and not host.endswith(".bilibili.com"):
            raise BilibiliError("b23.tv 短链没有指向 bilibili.com")
        resolved = find_bilibili_ref(final)
        if resolved is None or resolved.short_url:
            raise BilibiliError("b23.tv 短链没有指向可识别的视频")
        return resolved

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, object],
    ) -> dict[str, Any]:
        response = await self.client.get(url, params=params)
        if response.status_code >= 400:
            raise BilibiliError(f"B站接口 HTTP {response.status_code}")
        if len(response.content) > 4 * 1024 * 1024:
            raise BilibiliError("B站接口响应超过大小上限")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BilibiliError("B站接口没有返回 JSON") from exc
        if not isinstance(payload, dict):
            raise BilibiliError("B站接口响应结构异常")
        return payload


def find_bilibili_ref(value: str) -> BilibiliRef | None:
    text = str(value)
    bvid = _BVID.search(text)
    if bvid is not None:
        return BilibiliRef(bvid=bvid.group(0))
    avid = _AVID.search(text)
    if avid is not None:
        return BilibiliRef(aid=int(avid.group(1)))
    short = _B23.search(text)
    if short is not None:
        return BilibiliRef(short_url=short.group(0))
    return None


def _api_data(payload: dict[str, Any]) -> dict[str, Any]:
    code = _safe_int(payload.get("code"), -1)
    data = payload.get("data")
    if code != 0 or not isinstance(data, dict):
        raise BilibiliError(
            f"B站接口拒绝（code {code}）：{str(payload.get('message') or '')[:200]}"
        )
    return data


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
