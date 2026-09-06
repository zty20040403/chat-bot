from __future__ import annotations

import time
from typing import Any, Mapping

from src.bot_storage import PostgresDatabase

from .execution_contracts import canonical_json, new_handle
from .job_kinds import WORKER_JOB_KINDS
from .scheduling import ResourceRequest, bounded_int, decode_json


class ResourcePolicyStore:
    """Persist owner availability, capacity limits, and finite borrow grants."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def ensure_worker_policy(
        self, worker_id: str, *, owner_actor_id: str, capacity: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO fleet_worker_policies
                   (worker_id, owner_actor_id, desired_availability, reason, allow_gpu,
                    cpu_limit_millis, memory_limit_bytes, gpu_limit_slots,
                    resource_version, updated_at)
                   VALUES (?, ?, 'available', '', FALSE, ?, ?, 0, 1, ?)
                   ON CONFLICT(worker_id) DO NOTHING""",
                (
                    worker_id, owner_actor_id,
                    max(int(capacity.get("cpu_millis") or 0), 0),
                    max(int(capacity.get("memory_bytes") or 0), 0), now,
                ),
            )
            connection.commit()
            return self.worker_policy(worker_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _policy(item: Mapping[str, Any]) -> dict[str, Any]:
        return dict(item)

    def worker_policy(self, worker_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_worker_policies WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            return self._policy(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def policies(self) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT * FROM fleet_worker_policies ORDER BY worker_id"
            ).fetchall()
            return [self._policy(row) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def set_worker_availability(
        self, worker_id: str, *, actor_id: str, desired: str, reason: str,
        expected_version: int,
    ) -> dict[str, Any]:
        if desired not in {"available", "draining", "unavailable"}:
            raise ValueError("invalid desired availability")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_worker_policies WHERE worker_id = ? FOR UPDATE",
                (worker_id,),
            ).fetchone()
            if row is None:
                raise LookupError("worker policy not found")
            if row["owner_actor_id"] != actor_id:
                raise PermissionError(
                    "only the registered resource owner can change availability"
                )
            if int(row["resource_version"]) != int(expected_version):
                raise ValueError("worker policy changed; refresh before editing")
            cursor.execute(
                """UPDATE fleet_worker_policies SET desired_availability = ?, reason = ?,
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE worker_id = ?""",
                (desired, reason.strip()[:500], now, worker_id),
            )
            if desired != "available":
                cursor.execute(
                    """UPDATE fleet_borrow_grants SET status = ?,
                       resource_version = resource_version + 1, updated_at = ?
                       WHERE worker_id = ? AND status = 'available'""",
                    (
                        "draining" if desired == "draining" else "revoked",
                        now,
                        worker_id,
                    ),
                )
            connection.commit()
            return self.worker_policy(worker_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def configure_worker_capacity(
        self, worker_id: str, *, actor_id: str, allow_gpu: bool,
        cpu_limit_millis: int, memory_limit_bytes: int, gpu_limit_slots: int,
        expected_version: int,
    ) -> dict[str, Any]:
        cpu = bounded_int(
            cpu_limit_millis, default=0, minimum=0, maximum=128_000,
            field="cpu_limit_millis",
        )
        memory = bounded_int(
            memory_limit_bytes, default=0, minimum=0, maximum=128 * 1024**3,
            field="memory_limit_bytes",
        )
        gpu = bounded_int(
            gpu_limit_slots, default=0, minimum=0, maximum=16,
            field="gpu_limit_slots",
        )
        if not allow_gpu and gpu:
            raise ValueError("gpu_limit_slots must be zero when GPU borrowing is disabled")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                """SELECT p.*, w.capacity_json FROM fleet_worker_policies p
                   JOIN fleet_workers w ON w.worker_id = p.worker_id
                   WHERE p.worker_id = ? FOR UPDATE""",
                (worker_id,),
            ).fetchone()
            if row is None:
                raise LookupError("worker policy not found")
            if row["owner_actor_id"] != actor_id:
                raise PermissionError(
                    "only the registered resource owner can change capacity"
                )
            if int(row["resource_version"]) != int(expected_version):
                raise ValueError("worker policy changed; refresh before editing")
            capacity = decode_json(row["capacity_json"], {})
            if (
                cpu > int(capacity.get("cpu_millis") or 0)
                or memory > int(capacity.get("memory_bytes") or 0)
                or gpu > int(capacity.get("gpu_slots") or 0)
            ):
                raise ValueError("owner policy cannot exceed the latest worker capacity")
            cursor.execute(
                """UPDATE fleet_worker_policies SET allow_gpu = ?,
                   cpu_limit_millis = ?, memory_limit_bytes = ?, gpu_limit_slots = ?,
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE worker_id = ?""",
                (bool(allow_gpu), cpu, memory, gpu, now, worker_id),
            )
            connection.commit()
            return self.worker_policy(worker_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_grant(
        self, raw: Mapping[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        worker_id = str(raw.get("worker_id") or "").strip()
        grantee = str(raw.get("grantee_actor_id") or "").strip()
        origin_scope = str(raw.get("origin_scope") or "").strip()
        now = int(time.time())
        valid_from = int(raw.get("valid_from") or now)
        valid_until = int(raw.get("valid_until") or 0)
        if not worker_id or not grantee or not origin_scope:
            raise ValueError("worker, grantee, and origin scope are required")
        if valid_from < now - 60 or valid_until <= valid_from:
            raise ValueError("invalid grant time window")
        if valid_until > now + 31 * 86_400:
            raise ValueError("borrow grants cannot exceed 31 days")
        kinds = raw.get("allowed_kinds")
        if not isinstance(kinds, list) or not kinds or any(
            not isinstance(item, str) or item not in WORKER_JOB_KINDS
            for item in kinds
        ):
            raise ValueError("allowed_kinds contains an unsupported worker job")
        request = ResourceRequest.parse(raw)
        grant_id = new_handle("grant")
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            policy = cursor.execute(
                "SELECT * FROM fleet_worker_policies WHERE worker_id = ? FOR UPDATE",
                (worker_id,),
            ).fetchone()
            if policy is None:
                raise LookupError("worker policy not found")
            if policy["owner_actor_id"] != actor_id:
                raise PermissionError(
                    "only the registered resource owner can grant capacity"
                )
            if policy["desired_availability"] != "available":
                raise ValueError("worker is not accepting new borrow grants")
            if request.cpu_millis > int(policy["cpu_limit_millis"]):
                raise ValueError("grant exceeds owner CPU policy")
            if request.memory_bytes > int(policy["memory_limit_bytes"]):
                raise ValueError("grant exceeds owner memory policy")
            if request.gpu_slots and (
                not bool(policy["allow_gpu"])
                or request.gpu_slots > int(policy["gpu_limit_slots"])
            ):
                raise PermissionError("GPU borrowing is not authorized by the owner")
            cursor.execute(
                """INSERT INTO fleet_borrow_grants
                   (grant_id, worker_id, owner_actor_id, grantee_actor_id, origin_scope,
                    status, allowed_kinds_json, valid_from, valid_until,
                    cpu_limit_millis, memory_limit_bytes, gpu_limit_slots,
                    budget_limit_microunits, max_priority, resource_version,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'available', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    grant_id, worker_id, actor_id, grantee, origin_scope,
                    canonical_json(list(dict.fromkeys(kinds))),
                    valid_from, valid_until, request.cpu_millis, request.memory_bytes,
                    request.gpu_slots, request.max_cost_microunits,
                    request.priority, now, now,
                ),
            )
            connection.commit()
            return self.grant(grant_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _grant(item: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        result["allowed_kinds"] = decode_json(
            result.pop("allowed_kinds_json", "[]"), []
        )
        return result

    def grant(self, grant_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_borrow_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            return self._grant(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def grants(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE fleet_borrow_grants SET status = 'expired',
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE status IN ('available','draining') AND valid_until <= ?""",
                (now, now),
            )
            rows = cursor.execute(
                """SELECT * FROM fleet_borrow_grants
                   ORDER BY updated_at DESC LIMIT ?""",
                (min(max(int(limit), 1), 200),),
            ).fetchall()
            connection.commit()
            return [self._grant(row) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def set_grant_status(
        self, grant_id: str, *, actor_id: str, status: str, expected_version: int
    ) -> dict[str, Any]:
        if status not in {"available", "draining", "revoked"}:
            raise ValueError("invalid grant status")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_borrow_grants WHERE grant_id = ? FOR UPDATE",
                (grant_id,),
            ).fetchone()
            if row is None:
                raise LookupError("borrow grant not found")
            if row["owner_actor_id"] != actor_id:
                raise PermissionError("only the grant owner can change it")
            if int(row["resource_version"]) != int(expected_version):
                raise ValueError("borrow grant changed; refresh before editing")
            if status == "available" and int(row["valid_until"]) <= now:
                raise ValueError("expired borrow grant cannot be reopened")
            cursor.execute(
                """UPDATE fleet_borrow_grants SET status = ?,
                   resource_version = resource_version + 1, updated_at = ?
                   WHERE grant_id = ?""",
                (status, now, grant_id),
            )
            connection.commit()
            return self.grant(grant_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
