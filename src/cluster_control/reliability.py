from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

from src.bot_storage import PostgresDatabase

from .execution_contracts import canonical_json, new_handle
from .guardian import (
    guardian_status_after_check,
    guardian_target_snapshot,
    resolve_guardian_target,
    validate_guardian_action,
)


def _decode(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _tokens(text: str) -> set[str]:
    normalized = text.casefold()
    words = set(re.findall(r"[a-z0-9_.:-]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    words.update(normalized[index:index + 2] for index in range(max(len(normalized) - 1, 0)))
    return {word for word in words if word.strip()}


class ReliabilityStore:
    """Durable incident memory and time-bounded guardian contracts."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    @staticmethod
    def _incident(item: Mapping[str, Any], cursor: Any) -> dict[str, Any]:
        result = dict(item)
        events = cursor.execute(
            """SELECT event_id, event_type, source_ref, confidence, payload_json,
                      occurred_at, created_at
               FROM fleet_incident_events WHERE incident_id = ?
               ORDER BY occurred_at, event_id""",
            (result["incident_id"],),
        ).fetchall()
        result["events"] = []
        for event in events:
            decoded = dict(event)
            decoded["payload"] = _decode(decoded.pop("payload_json", "{}"), {})
            result["events"].append(decoded)
        return result

    def observe_incident(
        self, *, incident_key: str, host_id: str, service_ref: str,
        severity: str, summary: str, event_type: str, source_ref: str,
        confidence: str, payload: Mapping[str, Any], occurred_at: int | None = None,
    ) -> dict[str, Any]:
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("invalid incident severity")
        if confidence not in {"confirmed", "supported", "unknown", "contradicted"}:
            raise ValueError("invalid incident confidence")
        now = int(time.time())
        observed_at = int(occurred_at or now)
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                """SELECT * FROM fleet_incidents WHERE incident_key = ?
                   AND status != 'resolved' ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (incident_key,),
            ).fetchone()
            if row is None:
                incident_id = new_handle("incident")
                cursor.execute(
                    """INSERT INTO fleet_incidents
                       (incident_id, incident_key, host_id, service_ref, severity,
                        status, summary, visibility_scope, first_seen_at, last_seen_at,
                        resource_version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, 'admin', ?, ?, 1, ?, ?)""",
                    (
                        incident_id, incident_key[:240], host_id[:64], service_ref[:160],
                        severity, summary[:2000], observed_at, observed_at, now, now,
                    ),
                )
            else:
                incident_id = str(row["incident_id"])
                rank = {"info": 0, "warning": 1, "critical": 2}
                next_severity = severity if rank[severity] > rank[str(row["severity"])] else row["severity"]
                cursor.execute(
                    """UPDATE fleet_incidents SET severity = ?, summary = ?,
                       last_seen_at = GREATEST(last_seen_at, ?),
                       resource_version = resource_version + 1, updated_at = ?
                       WHERE incident_id = ?""",
                    (next_severity, summary[:2000], observed_at, now, incident_id),
                )
            cursor.execute(
                """INSERT INTO fleet_incident_events
                   (incident_id, event_type, source_ref, confidence, payload_json,
                    occurred_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id, event_type[:80], source_ref[:240], confidence,
                    canonical_json(dict(payload)), observed_at, now,
                ),
            )
            connection.commit()
            return self.incident(incident_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def resolve_incident(
        self, incident_id: str, *, actor_id: str, summary: str,
        evidence: Mapping[str, Any], expected_version: int,
    ) -> dict[str, Any]:
        if not actor_id.startswith("admin:"):
            raise PermissionError("only an administrator can resolve incidents")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_incidents WHERE incident_id = ? FOR UPDATE",
                (incident_id,),
            ).fetchone()
            if row is None:
                raise LookupError("incident not found")
            if int(row["resource_version"]) != int(expected_version):
                raise ValueError("incident changed; refresh before resolving")
            cursor.execute(
                """UPDATE fleet_incidents SET status = 'resolved', summary = ?,
                   resolved_at = ?, last_seen_at = ?, resource_version = resource_version + 1,
                   updated_at = ? WHERE incident_id = ?""",
                (summary[:2000], now, now, now, incident_id),
            )
            cursor.execute(
                """INSERT INTO fleet_incident_events
                   (incident_id, event_type, source_ref, confidence, payload_json,
                    occurred_at, created_at)
                   VALUES (?, 'resolved', ?, 'confirmed', ?, ?, ?)""",
                (incident_id, actor_id, canonical_json(dict(evidence)), now, now),
            )
            connection.commit()
            return self.incident(incident_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def recover_incident(
        self, *, incident_key: str, source_ref: str, payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Close the current incident after a deterministic recovery probe."""
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                """SELECT * FROM fleet_incidents WHERE incident_key = ?
                   AND status != 'resolved' ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (incident_key,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            incident_id = str(row["incident_id"])
            cursor.execute(
                """UPDATE fleet_incidents SET status = 'resolved', resolved_at = ?,
                   last_seen_at = ?, resource_version = resource_version + 1,
                   updated_at = ? WHERE incident_id = ?""",
                (now, now, now, incident_id),
            )
            cursor.execute(
                """INSERT INTO fleet_incident_events
                   (incident_id, event_type, source_ref, confidence, payload_json,
                    occurred_at, created_at)
                   VALUES (?, 'recovered', ?, 'confirmed', ?, ?, ?)""",
                (incident_id, source_ref[:240], canonical_json(dict(payload)), now, now),
            )
            connection.commit()
            return self.incident(incident_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            return self._incident(row, cursor) if row else None
        finally:
            cursor.close()
            connection.close()

    def incidents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                """SELECT * FROM fleet_incidents ORDER BY last_seen_at DESC LIMIT ?""",
                (min(max(limit, 1), 500),),
            ).fetchall()
            return [self._incident(row, cursor) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def create_case(self, raw: Mapping[str, Any], *, actor_id: str) -> dict[str, Any]:
        if not actor_id.startswith("admin:"):
            raise PermissionError("only an administrator can publish runbook cases")
        status = str(raw.get("status") or "draft")
        confidence = str(raw.get("confidence") or "unknown")
        if status not in {"draft", "verified"} or confidence not in {
            "confirmed", "supported", "unknown",
        }:
            raise ValueError("invalid case status or confidence")
        resolution = raw.get("resolution")
        applicability = raw.get("applicability")
        evidence_refs = raw.get("evidence_refs")
        if not isinstance(resolution, list) or not resolution:
            raise ValueError("resolution must contain at least one step")
        if not isinstance(applicability, dict) or not isinstance(evidence_refs, list):
            raise ValueError("invalid applicability or evidence references")
        case_id = new_handle("case")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_runbook_cases
                   (case_id, title, host_id, service_ref, symptoms, confirmed_cause,
                    resolution_json, applicability_json, evidence_refs_json, status,
                    confidence, revision, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    case_id, str(raw.get("title") or "")[:240],
                    str(raw.get("host_id") or "")[:64],
                    str(raw.get("service_ref") or "")[:160],
                    str(raw.get("symptoms") or "")[:4000],
                    str(raw.get("confirmed_cause") or "")[:4000],
                    canonical_json(resolution), canonical_json(applicability),
                    canonical_json(evidence_refs), status, confidence, actor_id, now, now,
                ),
            )
            connection.commit()
            return self.case(case_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _case(item: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        for key, fallback in (
            ("resolution_json", []), ("applicability_json", {}),
            ("evidence_refs_json", []),
        ):
            result[key.removesuffix("_json")] = _decode(result.pop(key, None), fallback)
        return result

    def case(self, case_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_runbook_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            return self._case(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def cases(self, *, limit: int = 100) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT * FROM fleet_runbook_cases ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
            return [self._case(row) for row in rows]
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def validate_applicability(
        applicability: Mapping[str, Any], facts: Mapping[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        for key, expected in applicability.items():
            actual: Any = facts
            for part in str(key).split("."):
                actual = actual.get(part) if isinstance(actual, Mapping) else None
            if isinstance(expected, list):
                if actual not in expected:
                    reasons.append(f"{key} not in verified values")
            elif actual != expected:
                reasons.append(f"{key} changed")
        return not reasons, reasons

    def search_cases(
        self, query: str, *, host_id: str = "", service_ref: str = "",
        current_facts: Mapping[str, Any] | None = None,
        candidate_case_ids: list[str] | None = None, limit: int = 10,
    ) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                """SELECT * FROM fleet_runbook_cases WHERE status = 'verified'
                   AND (? = '' OR host_id = '' OR host_id = ?)
                   AND (? = '' OR service_ref = '' OR service_ref = ?)
                   ORDER BY updated_at DESC LIMIT 200""",
                (host_id, host_id, service_ref, service_ref),
            ).fetchall()
        finally:
            cursor.close()
            connection.close()
        query_tokens = _tokens(query)
        semantic_candidates = {
            str(case_id) for case_id in (candidate_case_ids or []) if str(case_id)
        }
        results: list[dict[str, Any]] = []
        for row in rows:
            item = self._case(row)
            text = " ".join(
                str(item.get(key) or "")
                for key in ("title", "symptoms", "confirmed_cause", "service_ref")
            )
            overlap = len(query_tokens & _tokens(text))
            applicable, reasons = self.validate_applicability(
                item["applicability"], current_facts or {}
            )
            item["retrieval_score"] = overlap
            item["semantic_candidate"] = item["case_id"] in semantic_candidates
            item["applicable"] = applicable
            item["applicability_reasons"] = reasons
            results.append(item)
        results.sort(
            key=lambda item: (
                bool(item["applicable"]), bool(item["semantic_candidate"]),
                int(item["retrieval_score"]),
                int(item["updated_at"]),
            ),
            reverse=True,
        )
        return results[: min(max(limit, 1), 50)]

    def create_guardian(
        self, raw: Mapping[str, Any], *, actor_id: str, origin_scope: str,
        known_targets: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not actor_id.startswith("admin:"):
            raise PermissionError("only an administrator can create guardians")
        target_id = str(raw.get("target_id") or "").strip()
        target_host_id, target_service_ref = resolve_guardian_target(
            raw, known_targets
        )
        target_snapshot = guardian_target_snapshot(known_targets[target_id])
        now = int(time.time())
        starts_at = int(raw.get("starts_at") or now)
        expires_at = int(raw.get("expires_at") or 0)
        interval = int(raw.get("interval_seconds") or 60)
        threshold = int(raw.get("failure_threshold") or 3)
        max_actions = int(raw.get("max_actions") or 0)
        mode = str(raw.get("mode") or "observe")
        if mode not in {"observe", "remediate"}:
            raise ValueError("invalid guardian mode")
        if not now - 60 <= starts_at < expires_at <= now + 31 * 86_400:
            raise ValueError("guardian time window must end within 31 days")
        if not 15 <= interval <= 86_400 or not 1 <= threshold <= 20:
            raise ValueError("invalid guardian interval or failure threshold")
        if not 0 <= max_actions <= 20:
            raise ValueError("invalid guardian action limit")
        action = validate_guardian_action(
            raw.get("authorized_action"),
            mode=mode,
            max_actions=max_actions,
            host_id=target_host_id,
            service_ref=target_service_ref,
        )
        probe_policy = dict(raw.get("probe_policy") or {})
        probe_policy["registered_target"] = target_snapshot
        guardian_id = new_handle("guardian")
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_guardians
                   (guardian_id, actor_id, origin_scope, target_id, host_id,
                    service_ref, mode, status, starts_at, expires_at,
                    interval_seconds, failure_threshold, max_actions,
                    probe_policy_json, authorized_action_json, next_check_at,
                    resource_version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    guardian_id, actor_id, origin_scope, target_id,
                    target_host_id[:64], target_service_ref[:160], mode,
                    starts_at, expires_at, interval, threshold, max_actions,
                    canonical_json(probe_policy),
                    canonical_json(dict(action)), starts_at, now, now,
                ),
            )
            connection.commit()
            return self.guardian(guardian_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _guardian(item: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        result["probe_policy"] = _decode(result.pop("probe_policy_json", "{}"), {})
        result["authorized_action"] = _decode(
            result.pop("authorized_action_json", "{}"), {}
        )
        return result

    def guardian(self, guardian_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_guardians WHERE guardian_id = ?", (guardian_id,)
            ).fetchone()
            if row is None:
                return None
            item = self._guardian(row)
            checks = cursor.execute(
                """SELECT * FROM fleet_guardian_checks WHERE guardian_id = ?
                   ORDER BY checked_at DESC, check_id DESC LIMIT 50""",
                (guardian_id,),
            ).fetchall()
            item["checks"] = []
            for check in checks:
                value = dict(check)
                value["facts"] = _decode(value.pop("facts_json", "{}"), {})
                item["checks"].append(value)
            return item
        finally:
            cursor.close()
            connection.close()

    def guardians(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE fleet_guardians SET status = 'completed', updated_at = ?,
                   resource_version = resource_version + 1
                   WHERE status IN ('scheduled','active','paused','needs_attention')
                     AND expires_at <= ?""",
                (now, now),
            )
            rows = cursor.execute(
                "SELECT * FROM fleet_guardians ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 200),),
            ).fetchall()
            connection.commit()
            return [self._guardian(row) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def set_guardian_status(
        self, guardian_id: str, *, actor_id: str, status: str,
        expected_version: int,
    ) -> dict[str, Any]:
        if status not in {"active", "paused", "cancelled"}:
            raise ValueError("invalid guardian status")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_guardians WHERE guardian_id = ? FOR UPDATE",
                (guardian_id,),
            ).fetchone()
            if row is None:
                raise LookupError("guardian not found")
            if row["actor_id"] != actor_id:
                raise PermissionError("guardian belongs to another administrator")
            if int(row["resource_version"]) != int(expected_version):
                raise ValueError("guardian changed; refresh before editing")
            if int(row["expires_at"]) <= now and status != "cancelled":
                raise ValueError("expired guardian cannot be resumed")
            cursor.execute(
                """UPDATE fleet_guardians SET status = ?, next_check_at = ?,
                   check_lease_owner = NULL, check_lease_until = NULL,
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE guardian_id = ?""",
                (status, now, now, guardian_id),
            )
            connection.commit()
            return self.guardian(guardian_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def claim_due_guardians(
        self, *, owner: str, limit: int = 10, lease_seconds: int = 45
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE fleet_guardians SET status = 'completed', updated_at = ?,
                   resource_version = resource_version + 1
                   WHERE status IN ('scheduled','active','paused','needs_attention')
                     AND expires_at <= ?""",
                (now, now),
            )
            rows = cursor.execute(
                """SELECT * FROM fleet_guardians
                   WHERE status IN ('scheduled','active','needs_attention') AND starts_at <= ?
                     AND expires_at > ? AND next_check_at <= ?
                     AND (check_lease_until IS NULL OR check_lease_until <= ?)
                   ORDER BY next_check_at, guardian_id
                   FOR UPDATE SKIP LOCKED LIMIT ?""",
                (now, now, now, now, min(max(limit, 1), 50)),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                cursor.execute(
                    """UPDATE fleet_guardians SET status = 'active',
                       check_lease_owner = ?, check_lease_until = ?, updated_at = ?
                       WHERE guardian_id = ?""",
                    (owner, now + min(max(lease_seconds, 15), 300), now, row["guardian_id"]),
                )
                item = dict(row)
                item["status"] = "active"
                item["check_lease_owner"] = owner
                claimed.append(self._guardian(item))
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finish_guardian_check(
        self, guardian_id: str, *, owner: str, outcome: str,
        facts: Mapping[str, Any], incident_id: str = "", operation_id: str = "",
        action_used: bool = False, requires_attention: bool = False,
    ) -> dict[str, Any]:
        if outcome not in {"passed", "failed", "unknown"}:
            raise ValueError("invalid guardian outcome")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_guardians WHERE guardian_id = ? FOR UPDATE",
                (guardian_id,),
            ).fetchone()
            if row is None:
                raise LookupError("guardian not found")
            if row["check_lease_owner"] != owner or int(row["check_lease_until"] or 0) < now:
                raise PermissionError("guardian check lease is stale")
            failures = 0 if outcome == "passed" else int(row["consecutive_failures"])
            if outcome == "failed":
                failures += 1
            actions = int(row["actions_used"]) + (1 if action_used else 0)
            status = guardian_status_after_check(
                outcome=outcome,
                failures=failures,
                failure_threshold=int(row["failure_threshold"]),
                mode=str(row["mode"]),
                actions_used=actions,
                max_actions=int(row["max_actions"]),
                expired=int(row["expires_at"]) <= now,
                requires_attention=requires_attention,
            )
            cursor.execute(
                """INSERT INTO fleet_guardian_checks
                   (guardian_id, status, facts_json, incident_id, operation_id,
                    model_requested, checked_at)
                   VALUES (?, ?, ?, ?, ?, FALSE, ?)""",
                (
                    guardian_id, outcome, canonical_json(dict(facts)),
                    incident_id or None, operation_id or None, now,
                ),
            )
            cursor.execute(
                """UPDATE fleet_guardians SET status = ?, actions_used = ?,
                   consecutive_failures = ?, last_checked_at = ?, next_check_at = ?,
                   check_lease_owner = NULL, check_lease_until = NULL,
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE guardian_id = ?""",
                (
                    status, actions, failures, now,
                    min(now + int(row["interval_seconds"]), int(row["expires_at"])),
                    now, guardian_id,
                ),
            )
            connection.commit()
            return self.guardian(guardian_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
