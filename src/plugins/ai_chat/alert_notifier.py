"""Deliver new Alertmanager alerts to one QQ group without LLM involvement."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed


class AlertLogger(Protocol):
    def info(self, message: object, *args: object, **kwargs: object) -> object: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> object: ...


class GroupMessageBot(Protocol):
    async def send_group_msg(self, **data: Any) -> Any: ...


@dataclass(frozen=True)
class ActivityAlert:
    name: str
    severity: str
    instance: str
    peer: str
    summary: str
    description: str
    starts_at: str
    fingerprint: str

    @property
    def identity(self) -> str:
        raw = self.fingerprint or "|".join(
            (self.name, self.instance, self.peer, self.summary)
        )
        return f"{raw}|{self.starts_at}"

    @property
    def is_server_down(self) -> bool:
        normalized = self.name.casefold().replace("_", "").replace("-", "")
        return normalized in {
            "hostdown",
            "hostunreachable",
            "instancedown",
            "nodedown",
            "serverdown",
            "targetdown",
        }


AlertFetcher = Callable[[], Awaitable[list[ActivityAlert]]]
BotProvider = Callable[[], Sequence[GroupMessageBot]]


class AlertNotificationService:
    def __init__(
        self,
        *,
        alertmanager_url: str,
        group_id: int,
        check_seconds: int,
        state_path: Path,
        logger: AlertLogger,
        fetcher: AlertFetcher | None = None,
        bot_provider: BotProvider | None = None,
    ) -> None:
        self._alertmanager_url = alertmanager_url.rstrip("/")
        self._group_id = group_id
        self._check_seconds = max(check_seconds, 10)
        self._state_path = state_path
        self._logger = logger
        self._fetcher = fetcher or self._fetch_active_alerts
        self._bot_provider = bot_provider or _onebot_bots
        self._loaded = False
        self._seen_ids: set[str] = set()

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self._check_seconds)

    async def run_once(self) -> int:
        try:
            alerts = await self._fetcher()
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
            self._logger.warning(f"Alertmanager notification poll failed: {exc}")
            return 0

        active_ids = {alert.identity for alert in alerts}
        if not self._loaded:
            stored = await asyncio.to_thread(self._read_state)
            self._loaded = True
            if stored is None:
                self._seen_ids = active_ids
                await asyncio.to_thread(self._write_state, active_ids)
                self._logger.info(
                    "Alert notifier established its initial active-alert baseline "
                    f"with {len(active_ids)} alert(s)."
                )
                return 0
            self._seen_ids = stored

        new_alerts = [
            alert for alert in alerts if alert.identity not in self._seen_ids
        ]
        if not new_alerts:
            if active_ids != self._seen_ids:
                self._seen_ids = active_ids
                await asyncio.to_thread(self._write_state, active_ids)
            return 0

        bots = list(self._bot_provider())
        if not bots:
            self._logger.warning(
                "New alerts are waiting because no OneBot connection is available."
            )
            return 0

        message = Message(
            [MessageSegment.text(format_alert_notification(new_alerts))]
        )
        try:
            await bots[0].send_group_msg(
                group_id=self._group_id,
                message=message,
            )
        except ActionFailed as exc:
            if "timeout" not in str(exc).casefold():
                self._logger.warning(f"Activity alert notification failed: {exc}")
                return 0
            self._logger.warning(
                "Activity alert notification receipt timed out; treating the "
                "outcome as delivered to avoid duplicate alert messages."
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._logger.warning(f"Activity alert notification failed: {exc}")
            return 0

        self._seen_ids = active_ids
        await asyncio.to_thread(self._write_state, active_ids)
        self._logger.info(
            f"Delivered {len(new_alerts)} new activity alert(s) to QQ group "
            f"{self._group_id}."
        )
        return len(new_alerts)

    async def _fetch_active_alerts(self) -> list[ActivityAlert]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self._alertmanager_url}/api/v2/alerts",
                params={
                    "active": "true",
                    "silenced": "false",
                    "inhibited": "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Alertmanager returned a non-list response")
        return [
            _parse_alert(item)
            for item in payload
            if isinstance(item, dict)
        ]

    def _read_state(self) -> set[str] | None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._logger.warning(f"Alert notifier state could not be read: {exc}")
            return None
        values = payload.get("active_alert_ids", [])
        if not isinstance(values, list):
            return None
        return {str(value) for value in values if str(value)}

    def _write_state(self, active_ids: set[str]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active_alert_ids": sorted(active_ids),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._state_path)


def format_alert_notification(alerts: Sequence[ActivityAlert]) -> str:
    ordered = sorted(
        alerts,
        key=lambda alert: (_severity_rank(alert.severity), alert.starts_at),
        reverse=True,
    )
    highest = max((_severity_rank(alert.severity) for alert in ordered), default=0)
    if highest >= 3:
        heading = "【紧急告警】"
    elif highest == 2:
        heading = "【活动告警】"
    else:
        heading = "【监控提示】"

    lines = [heading]
    for index, alert in enumerate(ordered, start=1):
        level = _severity_label(alert.severity)
        if alert.is_server_down and alert.instance:
            target = alert.instance
            problem = "服务器寄了"
        else:
            target = " → ".join(
                value for value in (alert.instance, alert.peer) if value
            ) or alert.name
            problem = (_compact(alert.summary) or alert.name)[:120]
        lines.append(
            f"{index}. {level}｜{target}｜{problem}｜{_format_time(alert.starts_at)}"
        )
    return "\n".join(lines)


def _parse_alert(raw: dict[str, Any]) -> ActivityAlert:
    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    annotations = (
        raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
    )
    return ActivityAlert(
        name=str(labels.get("alertname") or "Alert")[:160],
        severity=str(labels.get("severity") or "warning")[:32],
        instance=str(labels.get("instance") or labels.get("host") or "")[:160],
        peer=str(labels.get("peer") or "")[:160],
        summary=str(annotations.get("summary") or "")[:500],
        description=str(annotations.get("description") or "")[:1000],
        starts_at=str(raw.get("startsAt") or "")[:64],
        fingerprint=str(raw.get("fingerprint") or _fallback_fingerprint(raw))[:128],
    )


def _severity_rank(severity: str) -> int:
    normalized = severity.casefold()
    if normalized in {"critical", "emergency", "fatal", "page"}:
        return 3
    if normalized in {"warning", "warn"}:
        return 2
    return 1


def _severity_label(severity: str) -> str:
    rank = _severity_rank(severity)
    if rank == 3:
        return "严重"
    if rank == 2:
        return "警告"
    return "提示"


def _format_time(value: str) -> str:
    if not value:
        return "时间未知"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return value


def _compact(value: str) -> str:
    return " ".join(value.replace("`", "").split())


def _fallback_fingerprint(raw: dict[str, Any]) -> str:
    payload = json.dumps(raw, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _onebot_bots() -> list[Bot]:
    return [bot for bot in get_bots().values() if isinstance(bot, Bot)]
