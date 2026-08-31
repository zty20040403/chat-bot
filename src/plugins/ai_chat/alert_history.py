from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

from src.bot_storage import PostgresDatabase


class AlertRecord(Protocol):
    identity: str
    fingerprint: str
    name: str
    severity: str
    instance: str
    peer: str
    summary: str
    description: str
    starts_at: str
    labels: dict[str, str]


class AlertEventStore:
    """Durable lifecycle history for Alertmanager observations and QQ notices."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def record_snapshot(
        self,
        alerts: Sequence[AlertRecord],
        *,
        incident_keys: dict[str, str],
        suppressed_ids: set[str],
        observed_at: int | None = None,
    ) -> None:
        now = int(observed_at or time.time())
        active_ids = [alert.identity for alert in alerts]
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            for alert in alerts:
                started_at = min(now, _epoch(alert.starts_at, fallback=now))
                cursor.execute(
                    """
                    INSERT INTO alert_events (
                        alert_key, fingerprint, incident_key, name, severity,
                        instance, peer, summary, description, labels_json,
                        status, suppressed, starts_at, first_seen_at,
                        last_seen_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'firing', ?, ?, ?, ?, NULL)
                    ON CONFLICT(alert_key) DO UPDATE SET
                        fingerprint = EXCLUDED.fingerprint,
                        incident_key = EXCLUDED.incident_key,
                        name = EXCLUDED.name,
                        severity = EXCLUDED.severity,
                        instance = EXCLUDED.instance,
                        peer = EXCLUDED.peer,
                        summary = EXCLUDED.summary,
                        description = EXCLUDED.description,
                        labels_json = EXCLUDED.labels_json,
                        status = 'firing',
                        suppressed = EXCLUDED.suppressed,
                        last_seen_at = EXCLUDED.last_seen_at,
                        resolved_at = NULL
                    """,
                    (
                        alert.identity,
                        alert.fingerprint,
                        incident_keys[alert.identity],
                        alert.name,
                        alert.severity,
                        alert.instance,
                        alert.peer,
                        alert.summary,
                        alert.description,
                        json.dumps(alert.labels, ensure_ascii=True, sort_keys=True),
                        alert.identity in suppressed_ids,
                        started_at,
                        started_at,
                        now,
                    ),
                )

            if active_ids:
                placeholders = ", ".join("?" for _ in active_ids)
                cursor.execute(
                    f"""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = ?, last_seen_at = ?
                    WHERE status = 'firing'
                      AND alert_key NOT IN ({placeholders})
                    """,
                    (now, now, *active_ids),
                )
            else:
                cursor.execute(
                    """
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = ?, last_seen_at = ?
                    WHERE status = 'firing'
                    """,
                    (now, now),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_notifications(
        self,
        incidents: Sequence[Any],
        *,
        kind: str,
        message: str,
        created_at: int | None = None,
    ) -> None:
        if not incidents:
            return
        now = int(created_at or time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            for incident in incidents:
                cursor.execute(
                    """
                    INSERT INTO alert_notifications (
                        incident_key, kind, severity, alert_count, message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(incident.key),
                        kind,
                        str(incident.representative.severity),
                        len(incident.alerts),
                        message[:2000],
                        now,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot(self, *, days: int = 1, limit: int = 200) -> dict[str, object]:
        bounded_days = min(max(int(days), 1), 365)
        bounded_limit = min(max(int(limit), 1), 500)
        now = int(time.time())
        start = _range_start(bounded_days, now=now)

        connection = self.database.store_connection()
        try:
            summary_row = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'firing') AS current_active,
                    COUNT(DISTINCT incident_key) FILTER (
                        WHERE status = 'firing'
                    ) AS current_incidents,
                    COUNT(*) FILTER (
                        WHERE first_seen_at >= ?
                    ) AS triggered,
                    COUNT(*) FILTER (
                        WHERE resolved_at >= ?
                    ) AS resolved,
                    COUNT(DISTINCT incident_key) FILTER (
                        WHERE first_seen_at >= ?
                    ) AS incidents
                FROM alert_events
                """,
                (start, start, start),
            ).fetchone()
            notification_row = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE created_at >= ? AND kind IN ('firing', 'escalation')
                    ) AS firing_notifications,
                    COUNT(*) FILTER (
                        WHERE created_at >= ? AND kind = 'recovery'
                    ) AS recovery_notifications
                FROM alert_notifications
                """,
                (start, start),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT event_id, alert_key, fingerprint, incident_key, name,
                       severity, instance, peer, summary, description,
                       labels_json, status, suppressed, starts_at,
                       first_seen_at, last_seen_at, resolved_at
                FROM alert_events
                WHERE first_seen_at >= ? OR status = 'firing'
                ORDER BY first_seen_at DESC, event_id DESC
                LIMIT ?
                """,
                (start, bounded_limit),
            ).fetchall()
            notification_rows = connection.execute(
                """
                SELECT notification_id, incident_key, kind, severity,
                       alert_count, message, created_at
                FROM alert_notifications
                WHERE created_at >= ?
                ORDER BY created_at DESC, notification_id DESC
                LIMIT ?
                """,
                (start, bounded_limit),
            ).fetchall()
        finally:
            connection.close()

        events = [_event_payload(row) for row in rows]
        return {
            "configured": True,
            "available": True,
            "timezone": "Asia/Shanghai",
            "range_start": start,
            "generated_at": now,
            "summary": {
                "current_active": _integer(summary_row, "current_active"),
                "current_incidents": _integer(summary_row, "current_incidents"),
                "triggered": _integer(summary_row, "triggered"),
                "resolved": _integer(summary_row, "resolved"),
                "incidents": _integer(summary_row, "incidents"),
                "firing_notifications": _integer(
                    notification_row, "firing_notifications"
                ),
                "recovery_notifications": _integer(
                    notification_row, "recovery_notifications"
                ),
            },
            "events": events,
            "incidents": _incident_payloads(events),
            "notifications": [
                {
                    "notification_id": int(row["notification_id"]),
                    "incident_key": str(row["incident_key"]),
                    "kind": str(row["kind"]),
                    "severity": str(row["severity"]),
                    "alert_count": int(row["alert_count"]),
                    "message": str(row["message"]),
                    "created_at": int(row["created_at"]),
                }
                for row in notification_rows
            ],
        }

    def rank_incidents(
        self,
        *,
        days: int = 7,
        limit: int = 10,
    ) -> dict[str, object]:
        """Rank incidents over the full period without snapshot row truncation."""

        bounded_days = min(max(int(days), 1), 365)
        bounded_limit = min(max(int(limit), 1), 20)
        now = int(time.time())
        start = _range_start(bounded_days, now=now)
        connection = self.database.store_connection()
        try:
            rows = connection.execute(
                """
                SELECT
                    incident_key,
                    COUNT(*) AS event_count,
                    SUM(CASE WHEN status = 'firing' THEN 1 ELSE 0 END)
                        AS active_event_count,
                    MIN(first_seen_at) AS first_seen_at,
                    MAX(last_seen_at) AS last_seen_at
                FROM alert_events
                WHERE first_seen_at >= ? OR status = 'firing'
                GROUP BY incident_key
                ORDER BY event_count DESC,
                         active_event_count DESC,
                         last_seen_at DESC
                LIMIT ?
                """,
                (start, bounded_limit),
            ).fetchall()
        finally:
            connection.close()

        return {
            "configured": True,
            "available": True,
            "timezone": "Asia/Shanghai",
            "days": bounded_days,
            "range_start": start,
            "generated_at": now,
            "items": [
                {
                    "incident_key": str(row["incident_key"]),
                    "event_count": int(row["event_count"] or 0),
                    "active_event_count": int(row["active_event_count"] or 0),
                    "first_seen_at": int(row["first_seen_at"] or 0),
                    "last_seen_at": int(row["last_seen_at"] or 0),
                }
                for row in rows
            ],
        }


def _event_payload(row: Any) -> dict[str, object]:
    return {
        "event_id": int(row["event_id"]),
        "alert_key": str(row["alert_key"]),
        "fingerprint": str(row["fingerprint"]),
        "incident_key": str(row["incident_key"]),
        "name": str(row["name"]),
        "severity": str(row["severity"]),
        "instance": str(row["instance"]),
        "peer": str(row["peer"]),
        "summary": str(row["summary"]),
        "description": str(row["description"]),
        "labels": _json_object(row["labels_json"]),
        "status": str(row["status"]),
        "suppressed": bool(row["suppressed"]),
        "starts_at": int(row["starts_at"]),
        "first_seen_at": int(row["first_seen_at"]),
        "last_seen_at": int(row["last_seen_at"]),
        "resolved_at": (
            int(row["resolved_at"]) if row["resolved_at"] is not None else None
        ),
    }


def _range_start(days: int, *, now: int) -> int:
    timezone = ZoneInfo("Asia/Shanghai")
    local_now = datetime.fromtimestamp(now, tz=timezone)
    return int(
        (
            local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=days - 1)
        ).timestamp()
    )


def _incident_payloads(events: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for event in events:
        groups[str(event["incident_key"])].append(event)
    payloads: list[dict[str, object]] = []
    for key, members in groups.items():
        representative = max(
            members,
            key=lambda item: (
                _severity_rank(str(item["severity"])),
                int(item["first_seen_at"]),
            ),
        )
        active = [item for item in members if item["status"] == "firing"]
        payloads.append(
            {
                "incident_key": key,
                "name": representative["name"],
                "severity": max(
                    (str(item["severity"]) for item in members),
                    key=_severity_rank,
                ),
                "instance": representative["instance"],
                "peer": representative["peer"],
                "summary": representative["summary"],
                "status": "firing" if active else "resolved",
                "event_count": len(members),
                "active_event_count": len(active),
                "first_seen_at": min(int(item["first_seen_at"]) for item in members),
                "last_seen_at": max(int(item["last_seen_at"]) for item in members),
                "resolved_at": (
                    None
                    if active
                    else max(int(item["resolved_at"] or 0) for item in members)
                ),
            }
        )
    return sorted(
        payloads,
        key=lambda item: (item["status"] == "firing", int(item["last_seen_at"])),
        reverse=True,
    )


def _integer(row: Any, key: str) -> int:
    if row is None or row[key] is None:
        return 0
    return int(row[key])


def _json_object(value: object) -> dict[str, object]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _epoch(value: str, *, fallback: int) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return fallback


def _severity_rank(value: str) -> int:
    normalized = value.casefold()
    if normalized in {"critical", "emergency", "fatal", "page"}:
        return 3
    if normalized in {"warning", "warn"}:
        return 2
    return 1
