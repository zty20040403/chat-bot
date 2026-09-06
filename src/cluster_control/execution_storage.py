from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from src.bot_storage import PostgresDatabase

from .execution_contracts import canonical_json, new_handle, safe_artifact_name


def _decode(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


class ClusterExecutionStore:
    """Authoritative P3/P4 state. Remote effects happen only after commit."""

    def __init__(self, database: PostgresDatabase, artifact_root: Path) -> None:
        self.database = database
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _event(cursor: Any, table: str, key_name: str, key: str, **values: Any) -> None:
        row = cursor.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 FROM {table} WHERE {key_name} = ?",
            (key,),
        ).fetchone()
        sequence = int(row[0]) if row else 1
        if table == "fleet_operation_events":
            cursor.execute(
                """INSERT INTO fleet_operation_events
                   (operation_id, sequence, event_type, status, actor_id, fence,
                    payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, sequence, values["event_type"], values["status"],
                    values.get("actor_id", "system"), values.get("fence", 0),
                    canonical_json(values.get("payload", {})), values["created_at"],
                ),
            )
        else:
            cursor.execute(
                """INSERT INTO fleet_job_events
                   (job_id, sequence, event_type, status, worker_id, fence,
                    payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, sequence, values["event_type"], values["status"],
                    values.get("worker_id", ""), values.get("fence", 0),
                    canonical_json(values.get("payload", {})), values["created_at"],
                ),
            )

    def prepare_operation(self, record: dict[str, Any]) -> dict[str, Any]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            existing = cursor.execute(
                """SELECT * FROM fleet_operations
                   WHERE actor_id = ? AND origin_scope = ? AND idempotency_key = ?""",
                (record["actor_id"], record["origin_scope"], record["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                item = self._operation(dict(existing), cursor)
                if item["contract_hash"] != record["contract_hash"]:
                    raise ValueError("idempotency key already belongs to a different operation")
                return item
            cursor.execute(
                """INSERT INTO fleet_operations (
                   operation_id, task_ref, step_ref, actor_id, origin_scope, host_id,
                   resource_ref, operation, operation_version, arguments_json,
                   backend_ref, backend_binding_version, expected_state_json,
                   resource_version, policy_version, deadline_at, resource_budget_json,
                   idempotency_key, verification_json, compensation_json, contract_hash,
                   status, capability_status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?)""",
                (
                    record["operation_id"], record["task_ref"], record["step_ref"],
                    record["actor_id"], record["origin_scope"], record["host_id"],
                    record["resource_ref"], record["operation"], record["operation_version"],
                    canonical_json(record["arguments"]), record["backend_ref"],
                    record["backend_binding_version"], canonical_json(record["expected_state"]),
                    record["resource_version"], record["policy_version"], record["deadline_at"],
                    canonical_json(record["resource_budget"]), record["idempotency_key"],
                    canonical_json(record["verification"]), canonical_json(record["compensation"]),
                    record["contract_hash"], record["status"], record["capability_status"],
                    record["created_at"], record["created_at"],
                ),
            )
            self._event(
                cursor, "fleet_operation_events", "operation_id", record["operation_id"],
                event_type="prepared", status=record["status"], actor_id=record["actor_id"],
                payload={"capability_status": record["capability_status"]},
                created_at=record["created_at"],
            )
            connection.commit()
            return self.get_operation(record["operation_id"]) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def approve_operation(
        self, operation_id: str, *, actor_id: str, expected_hash: str,
        expected_version: int, expires_at: int,
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_operations WHERE operation_id = ? FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise LookupError("operation not found")
            item = dict(row)
            if item["capability_status"] != "available":
                raise PermissionError("operation backend is not available")
            if item["status"] != "awaiting_approval":
                raise ValueError("operation is not awaiting approval")
            if item["contract_hash"] != expected_hash or int(item["resource_version"]) != expected_version:
                raise ValueError("operation changed; prepare and review it again")
            if int(item["deadline_at"]) <= now:
                raise ValueError("operation deadline expired")
            approval_id = new_handle("approval")
            cursor.execute(
                """INSERT INTO fleet_approvals
                   (approval_id, operation_id, actor_id, contract_hash, resource_version,
                    approved_at, expires_at, consumed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (approval_id, operation_id, actor_id, expected_hash, expected_version,
                 now, min(expires_at, int(item["deadline_at"])), None),
            )
            cursor.execute(
                """UPDATE fleet_operations SET approval_ref = ?, status = 'queued',
                   updated_at = ? WHERE operation_id = ?""",
                (approval_id, now, operation_id),
            )
            self._event(
                cursor, "fleet_operation_events", "operation_id", operation_id,
                event_type="approved", status="queued", actor_id=actor_id,
                payload={"approval_id": approval_id}, created_at=now,
            )
            connection.commit()
            return self.get_operation(operation_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def cancel_operation(
        self, operation_id: str, *, actor_id: str, origin_scope: str = ""
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT status, fence FROM fleet_operations WHERE operation_id = ? FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise LookupError("operation not found")
            owner = cursor.execute(
                "SELECT actor_id, origin_scope FROM fleet_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if owner is None or (
                not actor_id.startswith("admin:")
                and (owner["actor_id"] != actor_id or owner["origin_scope"] != origin_scope)
            ):
                raise PermissionError("operation belongs to another scope")
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return self._operation_by_id(cursor, operation_id)
            status = "cancelled" if row["status"] in {"planned", "awaiting_approval", "queued"} else "cancelling"
            cursor.execute(
                "UPDATE fleet_operations SET status = ?, updated_at = ? WHERE operation_id = ?",
                (status, now, operation_id),
            )
            self._event(
                cursor, "fleet_operation_events", "operation_id", operation_id,
                event_type="cancel_requested", status=status, actor_id=actor_id,
                fence=int(row["fence"]), created_at=now,
            )
            connection.commit()
            return self.get_operation(operation_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _operation_by_id(self, cursor: Any, operation_id: str) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT * FROM fleet_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise LookupError("operation not found")
        return self._operation(dict(row), cursor)

    def _operation(self, item: dict[str, Any], cursor: Any) -> dict[str, Any]:
        for key in ("arguments_json", "expected_state_json", "resource_budget_json", "verification_json", "compensation_json", "result_json"):
            item[key.removesuffix("_json")] = _decode(item.pop(key, "{}"), {})
        events = cursor.execute(
            """SELECT sequence, event_type, status, actor_id, fence, payload_json, created_at
               FROM fleet_operation_events WHERE operation_id = ? ORDER BY sequence""",
            (item["operation_id"],),
        ).fetchall()
        item["events"] = [
            {**dict(event), "payload": _decode(dict(event).get("payload_json"), {})}
            for event in events
        ]
        for event in item["events"]:
            event.pop("payload_json", None)
        return item

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return self._operation(dict(row), cursor) if row else None
        finally:
            cursor.close()
            connection.close()

    def recent_operations(self, limit: int = 50) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT * FROM fleet_operations ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 200),),
            ).fetchall()
            return [self._operation(dict(row), cursor) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def upsert_worker(self, worker_id: str, payload: dict[str, Any], *, host_id: str) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_workers
                   (worker_id, host_id, boot_id, protocol_version, availability,
                    capabilities_json, runtime_json, capacity_json, public_base_url,
                    last_seen_at, resource_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(worker_id) DO UPDATE SET
                    host_id = EXCLUDED.host_id, boot_id = EXCLUDED.boot_id,
                    protocol_version = EXCLUDED.protocol_version,
                    availability = EXCLUDED.availability,
                    capabilities_json = EXCLUDED.capabilities_json,
                    runtime_json = EXCLUDED.runtime_json,
                    capacity_json = EXCLUDED.capacity_json,
                    public_base_url = EXCLUDED.public_base_url,
                    last_seen_at = EXCLUDED.last_seen_at,
                    resource_version = fleet_workers.resource_version + 1""",
                (
                    worker_id, host_id, payload["boot_id"], payload["protocol_version"],
                    payload["availability"], canonical_json(payload["capabilities"]),
                    canonical_json(payload["runtime"]), canonical_json(payload["capacity"]),
                    payload.get("public_base_url", ""), now,
                ),
            )
            connection.commit()
            return self.worker(worker_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _worker(item: dict[str, Any]) -> dict[str, Any]:
        for key, fallback in (("capabilities_json", []), ("runtime_json", {}), ("capacity_json", {})):
            item[key.removesuffix("_json")] = _decode(item.pop(key, None), fallback)
        item["fresh"] = int(time.time()) - int(item["last_seen_at"]) <= 45
        return item

    def worker(self, worker_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute("SELECT * FROM fleet_workers WHERE worker_id = ?", (worker_id,)).fetchone()
            return self._worker(dict(row)) if row else None
        finally:
            cursor.close()
            connection.close()

    def workers(self) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute("SELECT * FROM fleet_workers ORDER BY worker_id").fetchall()
            return [self._worker(dict(row)) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def submit_job(self, record: dict[str, Any]) -> dict[str, Any]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            existing = cursor.execute(
                """SELECT * FROM fleet_worker_jobs
                   WHERE actor_id = ? AND origin_scope = ? AND idempotency_key = ?""",
                (record["actor_id"], record["origin_scope"], record["idempotency_key"]),
            ).fetchone()
            if existing:
                item = self._job(dict(existing), cursor)
                if (
                    item["kind"] != record["kind"]
                    or item["payload"] != record["payload"]
                    or item["constraints"] != record["constraints"]
                    or int(item["deadline_at"]) != int(record["deadline_at"])
                ):
                    raise ValueError("idempotency key already belongs to a different job")
                return item
            cursor.execute(
                """INSERT INTO fleet_worker_jobs
                   (job_id, actor_id, origin_scope, kind, payload_json, constraints_json,
                    idempotency_key, status, deadline_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (
                    record["job_id"], record["actor_id"], record["origin_scope"],
                    record["kind"], canonical_json(record["payload"]),
                    canonical_json(record["constraints"]), record["idempotency_key"],
                    record["deadline_at"], record["created_at"], record["created_at"],
                ),
            )
            self._event(
                cursor, "fleet_job_events", "job_id", record["job_id"],
                event_type="submitted", status="queued", created_at=record["created_at"],
            )
            connection.commit()
            return self.get_job(record["job_id"]) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def claim_job(self, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any] | None:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            overdue = cursor.execute(
                """SELECT job_id, kind FROM fleet_worker_jobs
                   WHERE status = 'queued' AND deadline_at <= ? FOR UPDATE""",
                (now,),
            ).fetchall()
            for stale in overdue:
                cursor.execute(
                    """UPDATE fleet_worker_jobs SET status = 'failed',
                       error_code = 'deadline_expired', updated_at = ? WHERE job_id = ?""",
                    (now, stale["job_id"]),
                )
                if stale["kind"] == "preview.static":
                    cursor.execute(
                        """UPDATE fleet_previews SET state = 'failed',
                           health_status = 'deadline_expired', updated_at = ?
                           WHERE job_id = ?""",
                        (now, stale["job_id"]),
                    )
                self._event(
                    cursor, "fleet_job_events", "job_id", stale["job_id"],
                    event_type="deadline_expired", status="failed",
                    payload={"retryable": False}, created_at=now,
                )
            expired = cursor.execute(
                """SELECT job_id, kind, worker_id, fence FROM fleet_worker_jobs
                   WHERE status IN ('running','verifying') AND lease_expires_at <= ?
                   FOR UPDATE""",
                (now,),
            ).fetchall()
            for stale in expired:
                if stale["kind"] == "preview.static":
                    cursor.execute(
                        """UPDATE fleet_worker_jobs SET status = 'failed',
                           error_code = 'outcome_unknown', lease_expires_at = NULL,
                           updated_at = ? WHERE job_id = ?""",
                        (now, stale["job_id"]),
                    )
                    cursor.execute(
                        """UPDATE fleet_previews SET state = 'failed',
                           health_status = 'unknown', updated_at = ? WHERE job_id = ?""",
                        (now, stale["job_id"]),
                    )
                    expired_status = "failed"
                else:
                    cursor.execute(
                        """UPDATE fleet_worker_jobs SET status = 'queued', worker_id = NULL,
                           lease_expires_at = NULL, updated_at = ? WHERE job_id = ?""",
                        (now, stale["job_id"]),
                    )
                    expired_status = "queued"
                cursor.execute(
                    """UPDATE fleet_reservations SET status = 'expired', released_at = ?
                       WHERE job_id = ? AND status = 'active'""",
                    (now, stale["job_id"]),
                )
                self._event(
                    cursor, "fleet_job_events", "job_id", stale["job_id"],
                    event_type="lease_expired", status=expired_status,
                    worker_id=str(stale["worker_id"] or ""), fence=int(stale["fence"]),
                    payload={"retryable": stale["kind"] != "preview.static"}, created_at=now,
                )
            worker = cursor.execute(
                "SELECT * FROM fleet_workers WHERE worker_id = ? FOR UPDATE", (worker_id,)
            ).fetchone()
            if worker is None or worker["availability"] != "available" or now - int(worker["last_seen_at"]) > 45:
                connection.commit()
                return None
            capabilities = set(_decode(worker["capabilities_json"], []))
            rows = cursor.execute(
                """SELECT * FROM fleet_worker_jobs
                   WHERE status = 'queued' AND deadline_at > ?
                   ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 20""",
                (now,),
            ).fetchall()
            chosen = next((row for row in rows if row["kind"] in capabilities), None)
            if chosen is None:
                connection.commit()
                return None
            constraints = _decode(chosen["constraints_json"], {})
            cpu = min(max(int(constraints.get("cpu_millis", 500)), 50), 8000)
            memory = min(max(int(constraints.get("memory_bytes", 268_435_456)), 16_777_216), 8 * 1024**3)
            gpu = min(max(int(constraints.get("gpu_slots", 0)), 0), 8)
            capacity = _decode(worker["capacity_json"], {})
            active = cursor.execute(
                """SELECT COALESCE(SUM(cpu_millis),0), COALESCE(SUM(memory_bytes),0),
                          COALESCE(SUM(gpu_slots),0) FROM fleet_reservations
                   WHERE worker_id = ? AND status = 'active' AND lease_expires_at > ?""",
                (worker_id, now),
            ).fetchone()
            if (
                int(active[0]) + cpu > int(capacity.get("cpu_millis", 0))
                or int(active[1]) + memory > int(capacity.get("memory_bytes", 0))
                or int(active[2]) + gpu > int(capacity.get("gpu_slots", 0))
            ):
                connection.commit()
                return None
            fence = int(chosen["fence"]) + 1
            lease_at = min(now + min(max(lease_seconds, 15), 300), int(chosen["deadline_at"]))
            cursor.execute(
                """UPDATE fleet_worker_jobs SET status = 'running', worker_id = ?,
                   attempt = attempt + 1, fence = ?, lease_expires_at = ?, updated_at = ?
                   WHERE job_id = ?""",
                (worker_id, fence, lease_at, now, chosen["job_id"]),
            )
            cursor.execute(
                """INSERT INTO fleet_reservations
                   (reservation_id, job_id, worker_id, cpu_millis, memory_bytes,
                    gpu_slots, status, fence, lease_expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                    reservation_id = EXCLUDED.reservation_id,
                    worker_id = EXCLUDED.worker_id,
                    cpu_millis = EXCLUDED.cpu_millis,
                    memory_bytes = EXCLUDED.memory_bytes,
                    gpu_slots = EXCLUDED.gpu_slots,
                    status = 'active', fence = EXCLUDED.fence,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    created_at = EXCLUDED.created_at, released_at = NULL""",
                (new_handle("reservation"), chosen["job_id"], worker_id, cpu, memory,
                 gpu, fence, lease_at, now),
            )
            self._event(
                cursor, "fleet_job_events", "job_id", chosen["job_id"],
                event_type="claimed", status="running", worker_id=worker_id,
                fence=fence, payload={"lease_expires_at": lease_at}, created_at=now,
            )
            connection.commit()
            return self.get_job(chosen["job_id"])
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def renew_job(
        self, job_id: str, *, worker_id: str, fence: int, lease_seconds: int = 60
    ) -> dict[str, int | bool]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT status, worker_id, fence, deadline_at FROM fleet_worker_jobs WHERE job_id = ? FOR UPDATE",
                (job_id,),
            ).fetchone()
            if row is None:
                raise LookupError("job not found")
            if row["worker_id"] != worker_id or int(row["fence"]) != int(fence):
                raise PermissionError("stale worker lease")
            if row["status"] == "cancelling":
                connection.commit()
                return {
                    "lease_expires_at": int(row["lease_expires_at"] or now),
                    "cancel_requested": True,
                }
            if row["status"] not in {"running", "verifying"}:
                raise ValueError("job lease is no longer renewable")
            lease_at = min(now + min(max(lease_seconds, 15), 300), int(row["deadline_at"]))
            if lease_at <= now:
                raise ValueError("job deadline expired")
            cursor.execute(
                "UPDATE fleet_worker_jobs SET lease_expires_at = ?, updated_at = ? WHERE job_id = ?",
                (lease_at, now, job_id),
            )
            cursor.execute(
                """UPDATE fleet_reservations SET lease_expires_at = ?
                   WHERE job_id = ? AND status = 'active' AND fence = ?""",
                (lease_at, job_id, fence),
            )
            connection.commit()
            return {"lease_expires_at": lease_at, "cancel_requested": False}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_job(
        self, job_id: str, *, worker_id: str, fence: int, ok: bool,
        result: dict[str, Any], error_code: str = "",
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_worker_jobs WHERE job_id = ? FOR UPDATE", (job_id,)
            ).fetchone()
            if row is None:
                raise LookupError("job not found")
            if row["worker_id"] != worker_id or int(row["fence"]) != int(fence):
                raise PermissionError("stale worker receipt")
            if row["status"] not in {"running", "verifying", "cancelling"}:
                return self._job(dict(row), cursor)
            cancelled = row["status"] == "cancelling"
            status = "cancelled" if cancelled else ("succeeded" if ok else "failed")
            stored_result = {"cancelled": True} if cancelled else result
            stored_error = "cancelled" if cancelled else error_code[:80]
            cursor.execute(
                """UPDATE fleet_worker_jobs SET status = ?, result_json = ?, error_code = ?,
                   lease_expires_at = NULL, updated_at = ? WHERE job_id = ?""",
                (status, canonical_json(stored_result), stored_error, now, job_id),
            )
            cursor.execute(
                """UPDATE fleet_reservations SET status = 'released', released_at = ?
                   WHERE job_id = ? AND status = 'active' AND fence = ?""",
                (now, job_id, fence),
            )
            self._event(
                cursor, "fleet_job_events", "job_id", job_id,
                event_type=("cancelled" if cancelled else ("completed" if ok else "failed")),
                status=status, worker_id=worker_id, fence=fence,
                payload=stored_result, created_at=now,
            )
            if row["kind"] == "preview.static":
                self._finish_preview(cursor, dict(row), result, status, now)
            connection.commit()
            return self.get_job(job_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _job(self, item: dict[str, Any], cursor: Any) -> dict[str, Any]:
        for key in ("payload_json", "constraints_json", "result_json"):
            item[key.removesuffix("_json")] = _decode(item.pop(key, "{}"), {})
        events = cursor.execute(
            """SELECT sequence, event_type, status, worker_id, fence, payload_json, created_at
               FROM fleet_job_events WHERE job_id = ? ORDER BY sequence""",
            (item["job_id"],),
        ).fetchall()
        item["events"] = []
        for event in events:
            decoded = dict(event)
            decoded["payload"] = _decode(decoded.pop("payload_json", "{}"), {})
            item["events"].append(decoded)
        return item

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute("SELECT * FROM fleet_worker_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._job(dict(row), cursor) if row else None
        finally:
            cursor.close()
            connection.close()

    def recent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT * FROM fleet_worker_jobs ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 200),),
            ).fetchall()
            return [self._job(dict(row), cursor) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def cancel_job(self, job_id: str, *, actor_id: str, origin_scope: str) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_worker_jobs WHERE job_id = ? FOR UPDATE", (job_id,)
            ).fetchone()
            if row is None:
                raise LookupError("job not found")
            if not actor_id.startswith("admin:") and (
                row["actor_id"] != actor_id or row["origin_scope"] != origin_scope
            ):
                raise PermissionError("job belongs to another scope")
            status = "cancelled" if row["status"] == "queued" else "cancelling"
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return self._job(dict(row), cursor)
            cursor.execute(
                "UPDATE fleet_worker_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status, now, job_id),
            )
            if status == "cancelled":
                cursor.execute(
                    "UPDATE fleet_reservations SET status = 'cancelled', released_at = ? WHERE job_id = ? AND status = 'active'",
                    (now, job_id),
                )
            if row["kind"] == "preview.static":
                cursor.execute(
                    """UPDATE fleet_previews SET state = 'disabled',
                       health_status = 'cancelled', updated_at = ? WHERE job_id = ?""",
                    (now, job_id),
                )
            self._event(
                cursor, "fleet_job_events", "job_id", job_id,
                event_type="cancel_requested", status=status,
                worker_id=str(row["worker_id"] or ""), fence=int(row["fence"]), created_at=now,
            )
            connection.commit()
            return self.get_job(job_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def store_artifact(
        self, *, actor_id: str, origin_scope: str, name: str, media_type: str,
        content_base64: str, job_id: str | None = None,
    ) -> dict[str, Any]:
        name = safe_artifact_name(name)
        try:
            content = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("artifact content is not valid base64") from exc
        if not content or len(content) > 25 * 1024 * 1024:
            raise ValueError("artifact must be between 1 byte and 25 MiB")
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = new_handle("artifact")
        target_dir = self.artifact_root / digest[:2]
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = target_dir / digest
        created_blob = False
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as output:
                output.write(content)
                temp_name = output.name
            os.chmod(temp_name, 0o400)
            os.replace(temp_name, target)
            created_blob = True
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_artifacts
                   (artifact_id, job_id, actor_id, origin_scope, name, media_type,
                    size_bytes, sha256, storage_ref, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?)""",
                (artifact_id, job_id, actor_id, origin_scope, name, media_type[:120],
                 len(content), digest, str(target), now),
            )
            connection.commit()
            return self.artifact(artifact_id) or {}
        except Exception:
            connection.rollback()
            if created_blob:
                target.unlink(missing_ok=True)
            raise
        finally:
            cursor.close()
            connection.close()

    def artifact(self, artifact_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute("SELECT * FROM fleet_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def artifact_bytes(self, artifact_id: str) -> tuple[dict[str, Any], bytes]:
        item = self.artifact(artifact_id)
        if item is None:
            raise LookupError("artifact not found")
        path = Path(str(item["storage_ref"]))
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise LookupError("artifact content unavailable") from exc
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("artifact checksum mismatch")
        return item, content

    def artifact_bytes_for_worker(
        self, artifact_id: str, *, job_id: str, worker_id: str, fence: int
    ) -> tuple[dict[str, Any], bytes]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                """SELECT payload_json, status, worker_id, fence
                   FROM fleet_worker_jobs WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            if row is None:
                raise LookupError("job not found")
            payload = _decode(row["payload_json"], {})
            if (
                row["status"] not in {"running", "verifying"}
                or row["worker_id"] != worker_id
                or int(row["fence"]) != int(fence)
                or payload.get("artifact_id") != artifact_id
            ):
                raise PermissionError("artifact is not assigned to this worker lease")
        finally:
            cursor.close()
            connection.close()
        return self.artifact_bytes(artifact_id)

    def validate_artifact(self, artifact_id: str, *, ok: bool) -> None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "UPDATE fleet_artifacts SET status = ?, validated_at = ? WHERE artifact_id = ?",
                ("validated" if ok else "rejected", int(time.time()), artifact_id),
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()

    def create_preview(self, record: dict[str, Any]) -> dict[str, Any]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_previews
                   (preview_id, job_id, artifact_id, actor_id, origin_scope, route,
                    state, expires_at, cleanup_policy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
                (record["preview_id"], record["job_id"], record["artifact_id"],
                 record["actor_id"], record["origin_scope"], record["route"],
                 record["expires_at"], record["cleanup_policy"],
                 record["created_at"], record["created_at"]),
            )
            connection.commit()
            return self.preview(record["preview_id"]) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _finish_preview(
        self, cursor: Any, job: dict[str, Any], result: dict[str, Any],
        status: str, now: int,
    ) -> None:
        payload = _decode(job["payload_json"], {})
        preview_id = str(payload.get("preview_id") or "")
        if not preview_id:
            return
        cursor.execute(
            """UPDATE fleet_previews SET worker_id = ?, public_url = ?, state = ?,
               health_status = ?, last_checked_at = ?, updated_at = ? WHERE preview_id = ?""",
            (job.get("worker_id"), str(result.get("public_url") or "") if status == "succeeded" else "",
             "active" if status == "succeeded" else ("disabled" if status == "cancelled" else "failed"),
             "healthy" if status == "succeeded" else status,
             now, now, preview_id),
        )

    def preview(self, preview_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute("SELECT * FROM fleet_previews WHERE preview_id = ?", (preview_id,)).fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def previews(self, limit: int = 50) -> list[dict[str, Any]]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE fleet_previews SET state = 'expired', health_status = 'expired',
                   updated_at = ? WHERE state = 'active' AND expires_at <= ?""",
                (now, now),
            )
            rows = cursor.execute(
                "SELECT * FROM fleet_previews ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 200),),
            ).fetchall()
            connection.commit()
            return [dict(row) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def reservations(self, limit: int = 100) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT * FROM fleet_reservations ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 200),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            cursor.close()
            connection.close()
