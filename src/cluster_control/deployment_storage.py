from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence

from src.bot_storage import PostgresDatabase

from .deployment_contracts import approval_contract_hash
from .execution_contracts import canonical_json, new_handle


TERMINAL_DEPLOYMENT_STATES = frozenset(
    {"succeeded", "partial", "failed", "cancelled", "rolled_back", "needs_attention"}
)

TARGET_TRANSITIONS = {
    "pending": frozenset({"pending", "preflighting", "failed"}),
    "preflighting": frozenset({"preflighting", "build_ready", "failed"}),
    "build_ready": frozenset({"build_ready", "deploying", "skipped"}),
    "deploying": frozenset({"deploying", "verifying", "failed", "unknown"}),
    "verifying": frozenset({"verifying", "succeeded", "failed", "unknown"}),
    "unknown": frozenset({"unknown", "rollback_pending"}),
    "succeeded": frozenset({"succeeded", "rollback_pending"}),
    "failed": frozenset({"failed"}),
    "rollback_pending": frozenset(
        {"rollback_pending", "rolling_back", "rollback_failed"}
    ),
    "rolling_back": frozenset({"rolling_back", "rolled_back", "rollback_failed"}),
    "rolled_back": frozenset({"rolled_back"}),
    "rollback_failed": frozenset({"rollback_failed"}),
    "skipped": frozenset({"skipped"}),
}


def _decode(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


class DeploymentStore:
    """Durable two-phase deployment ledger with host-scoped fencing."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    @staticmethod
    def _event(
        cursor: Any,
        deployment_id: str,
        *,
        event_type: str,
        status: str,
        actor_id: str,
        host_id: str = "",
        step: str = "",
        fence: int = 0,
        payload: Mapping[str, Any] | None = None,
        now: int,
    ) -> None:
        row = cursor.execute(
            """SELECT COALESCE(MAX(sequence), 0) + 1
               FROM fleet_deployment_events WHERE deployment_id = ?""",
            (deployment_id,),
        ).fetchone()
        cursor.execute(
            """INSERT INTO fleet_deployment_events
               (deployment_id, sequence, event_type, status, host_id, step,
                actor_id, fence, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deployment_id,
                int(row[0]) if row else 1,
                event_type,
                status,
                host_id,
                step,
                actor_id,
                int(fence),
                canonical_json(dict(payload or {})),
                int(now),
            ),
        )

    def prepare(
        self,
        record: Mapping[str, Any],
        targets: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            existing = cursor.execute(
                """SELECT deployment_id, contract_hash FROM fleet_deployments
                   WHERE actor_id = ? AND origin_scope = ? AND idempotency_key = ?""",
                (
                    record["actor_id"],
                    record["origin_scope"],
                    record["idempotency_key"],
                ),
            ).fetchone()
            if existing is not None:
                item = self._get(cursor, str(existing["deployment_id"]))
                if item["contract_hash"] != record["contract_hash"]:
                    raise ValueError(
                        "idempotency key already belongs to another deployment"
                    )
                return item
            cursor.execute(
                """INSERT INTO fleet_deployments
                   (deployment_id, actor_id, origin_scope, repository_id,
                    source_revision, expected_remote_revision, target_hosts_json,
                    requested_changes_json, strategy, canary_host_id,
                    failure_policy, contract_json, contract_hash, idempotency_key,
                    status, phase, deadline_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'preflight_queued', 'preflight', ?, ?, ?)""",
                (
                    record["deployment_id"],
                    record["actor_id"],
                    record["origin_scope"],
                    record["repository_id"],
                    record["source_revision"],
                    record["expected_remote_revision"],
                    canonical_json(record["target_hosts"]),
                    canonical_json(record["requested_changes"]),
                    record["strategy"],
                    record["canary_host_id"],
                    record["failure_policy"],
                    canonical_json(record["contract"]),
                    record["contract_hash"],
                    record["idempotency_key"],
                    int(record["deadline_at"]),
                    int(record["created_at"]),
                    int(record["created_at"]),
                ),
            )
            for ordinal, target in enumerate(targets):
                cursor.execute(
                    """INSERT INTO fleet_deployment_targets
                       (deployment_id, host_id, ordinal, status, step)
                       VALUES (?, ?, ?, 'pending', 'pending')""",
                    (record["deployment_id"], target["host_id"], ordinal),
                )
            self._event(
                cursor,
                str(record["deployment_id"]),
                event_type="prepared",
                status="preflight_queued",
                actor_id=str(record["actor_id"]),
                payload={
                    "repository_id": record["repository_id"],
                    "source_revision": record["source_revision"],
                },
                now=int(record["created_at"]),
            )
            connection.commit()
            return self.get(str(record["deployment_id"])) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _deployment(row: Mapping[str, Any], cursor: Any) -> dict[str, Any]:
        item = dict(row)
        for key, fallback in (
            ("target_hosts_json", []),
            ("requested_changes_json", []),
            ("contract_json", {}),
            ("preflight_json", {}),
            ("result_json", {}),
        ):
            item[key.removesuffix("_json")] = _decode(item.pop(key, None), fallback)
        target_rows = cursor.execute(
            """SELECT * FROM fleet_deployment_targets
               WHERE deployment_id = ? ORDER BY ordinal""",
            (item["deployment_id"],),
        ).fetchall()
        item["targets"] = []
        for target_row in target_rows:
            target = dict(target_row)
            target["verification"] = _decode(
                target.pop("verification_json", None),
                {},
            )
            item["targets"].append(target)
        event_rows = cursor.execute(
            """SELECT sequence, event_type, status, host_id, step, actor_id,
                      fence, payload_json, created_at
               FROM fleet_deployment_events
               WHERE deployment_id = ? ORDER BY sequence""",
            (item["deployment_id"],),
        ).fetchall()
        item["events"] = []
        for event_row in event_rows:
            event = dict(event_row)
            event["payload"] = _decode(event.pop("payload_json", None), {})
            item["events"].append(event)
        return item

    def _get(self, cursor: Any, deployment_id: str) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT * FROM fleet_deployments WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        if row is None:
            raise LookupError("deployment not found")
        return self._deployment(row, cursor)

    def get(self, deployment_id: str) -> dict[str, Any] | None:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_deployments WHERE deployment_id = ?",
                (deployment_id,),
            ).fetchone()
            return self._deployment(row, cursor) if row is not None else None
        finally:
            cursor.close()
            connection.close()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                """SELECT * FROM fleet_deployments
                   ORDER BY updated_at DESC LIMIT ?""",
                (min(max(int(limit), 1), 200),),
            ).fetchall()
            return [self._deployment(row, cursor) for row in rows]
        finally:
            cursor.close()
            connection.close()

    def _expire_stale(self, cursor: Any, now: int) -> None:
        expired = cursor.execute(
            """SELECT deployment_id, status, phase, fence FROM fleet_deployments
               WHERE status IN ('preflighting','deploying','verifying','rolling_back')
                 AND lease_expires_at <= ? FOR UPDATE""",
            (now,),
        ).fetchall()
        for row in expired:
            safe_retry = row["phase"] == "preflight"
            status = "preflight_queued" if safe_retry else "needs_attention"
            cursor.execute(
                """UPDATE fleet_deployments SET status = ?, deployer_id = NULL,
                   lease_expires_at = NULL, updated_at = ?, resource_version = resource_version + 1
                   WHERE deployment_id = ?""",
                (status, now, row["deployment_id"]),
            )
            cursor.execute(
                "DELETE FROM fleet_maintenance_locks WHERE deployment_id = ?",
                (row["deployment_id"],),
            )
            self._event(
                cursor,
                str(row["deployment_id"]),
                event_type="lease_expired",
                status=status,
                actor_id="system",
                fence=int(row["fence"]),
                payload={"safe_retry": safe_retry, "phase": row["phase"]},
                now=now,
            )
        deadline_rows = cursor.execute(
            """SELECT deployment_id FROM fleet_deployments
               WHERE status IN ('preflight_queued','awaiting_approval','queued')
                 AND deadline_at <= ? FOR UPDATE""",
            (now,),
        ).fetchall()
        for row in deadline_rows:
            cursor.execute(
                """UPDATE fleet_deployments SET status = 'failed', phase = 'complete',
                   result_json = ?, finished_at = ?, updated_at = ?,
                   resource_version = resource_version + 1
                   WHERE deployment_id = ?""",
                (
                    canonical_json({"error_code": "deadline_expired"}),
                    now,
                    now,
                    row["deployment_id"],
                ),
            )
            self._event(
                cursor,
                str(row["deployment_id"]),
                event_type="deadline_expired",
                status="failed",
                actor_id="system",
                now=now,
            )

    def claim(
        self,
        deployer_id: str,
        *,
        repository_ids: Sequence[str],
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        allowed = tuple(dict.fromkeys(str(item) for item in repository_ids))
        if not allowed:
            return None
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            self._expire_stale(cursor, now)
            placeholders = ",".join("?" for _ in allowed)
            rows = cursor.execute(
                f"""SELECT * FROM fleet_deployments
                    WHERE status IN ('preflight_queued','queued')
                      AND deadline_at > ? AND repository_id IN ({placeholders})
                    ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END,
                             created_at, deployment_id
                    FOR UPDATE SKIP LOCKED LIMIT 10""",
                (now, *allowed),
            ).fetchall()
            selected: Mapping[str, Any] | None = None
            for row in rows:
                if row["status"] == "queued":
                    hosts = _decode(row["target_hosts_json"], [])
                    host_placeholders = ",".join("?" for _ in hosts)
                    conflict = cursor.execute(
                        f"""SELECT host_id FROM fleet_maintenance_locks
                            WHERE host_id IN ({host_placeholders})
                              AND lease_expires_at > ? LIMIT 1""",
                        (*hosts, now),
                    ).fetchone()
                    if conflict is not None:
                        continue
                    approval = cursor.execute(
                        """SELECT * FROM fleet_deployment_approvals
                           WHERE approval_id = ? FOR UPDATE""",
                        (row["approval_ref"],),
                    ).fetchone()
                    if (
                        approval is None
                        or approval["contract_hash"] != row["contract_hash"]
                        or int(approval["resource_version"]) != int(row["resource_version"])
                        or int(approval["expires_at"]) <= now
                        or approval["consumed_at"] is not None
                    ):
                        cursor.execute(
                            """UPDATE fleet_deployments SET status = 'needs_attention',
                               phase = 'complete', result_json = ?, finished_at = ?,
                               updated_at = ?, resource_version = resource_version + 1
                               WHERE deployment_id = ?""",
                            (
                                canonical_json({"error_code": "approval_invalid"}),
                                now,
                                now,
                                row["deployment_id"],
                            ),
                        )
                        self._event(
                            cursor,
                            str(row["deployment_id"]),
                            event_type="approval_invalid",
                            status="needs_attention",
                            actor_id="system",
                            now=now,
                        )
                        continue
                selected = row
                break
            if selected is None:
                connection.commit()
                return None
            deployment_id = str(selected["deployment_id"])
            phase = "preflight" if selected["status"] == "preflight_queued" else "apply"
            status = "preflighting" if phase == "preflight" else "deploying"
            fence = int(selected["fence"]) + 1
            lease_until = now + min(max(int(lease_seconds), 30), 600)
            if phase == "apply":
                hosts = _decode(selected["target_hosts_json"], [])
                for host in hosts:
                    cursor.execute(
                        """INSERT INTO fleet_maintenance_locks
                           (host_id, deployment_id, deployer_id, fence,
                            lease_expires_at, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(host_id) DO UPDATE SET
                             deployment_id = EXCLUDED.deployment_id,
                             deployer_id = EXCLUDED.deployer_id,
                             fence = EXCLUDED.fence,
                             lease_expires_at = EXCLUDED.lease_expires_at,
                             updated_at = EXCLUDED.updated_at
                           WHERE fleet_maintenance_locks.lease_expires_at <= ?""",
                        (
                            host,
                            deployment_id,
                            deployer_id,
                            fence,
                            lease_until,
                            now,
                            now,
                            now,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("maintenance lock changed during claim")
                cursor.execute(
                    """UPDATE fleet_deployment_approvals SET consumed_at = ?
                       WHERE approval_id = ? AND consumed_at IS NULL""",
                    (now, selected["approval_ref"]),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("deployment approval was consumed concurrently")
            cursor.execute(
                """UPDATE fleet_deployments SET status = ?, phase = ?,
                   deployer_id = ?, lease_expires_at = ?, fence = ?, updated_at = ?
                   WHERE deployment_id = ?""",
                (status, phase, deployer_id, lease_until, fence, now, deployment_id),
            )
            self._event(
                cursor,
                deployment_id,
                event_type="claimed",
                status=status,
                actor_id=f"deployer:{deployer_id}",
                fence=fence,
                payload={"phase": phase, "lease_expires_at": lease_until},
                now=now,
            )
            connection.commit()
            return self.get(deployment_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _assert_lease(
        row: Mapping[str, Any] | None,
        *,
        deployer_id: str,
        fence: int,
        now: int,
    ) -> None:
        if row is None:
            raise LookupError("deployment not found")
        if (
            row["deployer_id"] != deployer_id
            or int(row["fence"]) != int(fence)
            or int(row["lease_expires_at"] or 0) <= now
        ):
            raise PermissionError("deployment lease is no longer owned")

    def renew(
        self,
        deployment_id: str,
        *,
        deployer_id: str,
        fence: int,
        lease_seconds: int = 120,
    ) -> dict[str, int | bool]:
        now = int(time.time())
        lease_until = now + min(max(int(lease_seconds), 30), 600)
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                """SELECT deployer_id, fence, lease_expires_at, cancel_requested
                   FROM fleet_deployments WHERE deployment_id = ? FOR UPDATE""",
                (deployment_id,),
            ).fetchone()
            self._assert_lease(row, deployer_id=deployer_id, fence=fence, now=now)
            cursor.execute(
                """UPDATE fleet_deployments SET lease_expires_at = ?, updated_at = ?
                   WHERE deployment_id = ?""",
                (lease_until, now, deployment_id),
            )
            cursor.execute(
                """UPDATE fleet_maintenance_locks SET lease_expires_at = ?, updated_at = ?
                   WHERE deployment_id = ? AND deployer_id = ? AND fence = ?""",
                (lease_until, now, deployment_id, deployer_id, fence),
            )
            connection.commit()
            return {
                "lease_expires_at": lease_until,
                "cancel_requested": bool(row["cancel_requested"]),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def update_target(
        self,
        deployment_id: str,
        host_id: str,
        *,
        deployer_id: str,
        fence: int,
        status: str,
        step: str,
        current_toplevel: str = "",
        target_toplevel: str = "",
        actual_toplevel: str = "",
        verification: Mapping[str, Any] | None = None,
        error_code: str = "",
    ) -> dict[str, Any]:
        if status not in TARGET_TRANSITIONS or not 1 <= len(step) <= 80:
            raise ValueError("invalid deployment target transition")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            lease = cursor.execute(
                """SELECT deployer_id, fence, lease_expires_at, status
                   FROM fleet_deployments WHERE deployment_id = ? FOR UPDATE""",
                (deployment_id,),
            ).fetchone()
            self._assert_lease(
                lease,
                deployer_id=deployer_id,
                fence=fence,
                now=now,
            )
            existing = cursor.execute(
                """SELECT status FROM fleet_deployment_targets
                   WHERE deployment_id = ? AND host_id = ? FOR UPDATE""",
                (deployment_id, host_id),
            ).fetchone()
            if existing is None:
                raise LookupError("deployment target not found")
            old_status = str(existing["status"])
            if status not in TARGET_TRANSITIONS[old_status]:
                raise ValueError(
                    f"invalid deployment target transition: {old_status} -> {status}"
                )
            started_at = now if status in {"preflighting", "deploying", "rolling_back"} else None
            finished_at = now if status in {
                "build_ready", "succeeded", "failed", "rolled_back",
                "rollback_failed", "skipped",
            } else None
            cursor.execute(
                """UPDATE fleet_deployment_targets SET status = ?, step = ?,
                   current_toplevel = CASE WHEN ? <> '' THEN ? ELSE current_toplevel END,
                   target_toplevel = CASE WHEN ? <> '' THEN ? ELSE target_toplevel END,
                   actual_toplevel = CASE WHEN ? <> '' THEN ? ELSE actual_toplevel END,
                   verification_json = CASE WHEN ? <> '{}' THEN ? ELSE verification_json END,
                   error_code = ?, started_at = COALESCE(started_at, ?),
                   finished_at = COALESCE(?, finished_at)
                   WHERE deployment_id = ? AND host_id = ?""",
                (
                    status,
                    step,
                    current_toplevel,
                    current_toplevel,
                    target_toplevel,
                    target_toplevel,
                    actual_toplevel,
                    actual_toplevel,
                    canonical_json(dict(verification or {})),
                    canonical_json(dict(verification or {})),
                    error_code[:120],
                    started_at,
                    finished_at,
                    deployment_id,
                    host_id,
                ),
            )
            parent_status = str(lease["status"])
            if parent_status != "cancelling":
                if status == "verifying":
                    parent_status = "verifying"
                elif status in {"rollback_pending", "rolling_back"}:
                    parent_status = "rolling_back"
                if parent_status != lease["status"]:
                    cursor.execute(
                        """UPDATE fleet_deployments SET status = ?, updated_at = ?
                           WHERE deployment_id = ?""",
                        (parent_status, now, deployment_id),
                    )
            self._event(
                cursor,
                deployment_id,
                event_type="target_updated",
                status=str(lease["status"]),
                actor_id=f"deployer:{deployer_id}",
                host_id=host_id,
                step=step,
                fence=fence,
                payload={"target_status": status, "error_code": error_code[:120]},
                now=now,
            )
            connection.commit()
            return self.get(deployment_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_phase(
        self,
        deployment_id: str,
        *,
        deployer_id: str,
        fence: int,
        ok: bool,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_deployments WHERE deployment_id = ? FOR UPDATE",
                (deployment_id,),
            ).fetchone()
            self._assert_lease(row, deployer_id=deployer_id, fence=fence, now=now)
            phase = str(row["phase"])
            if phase == "preflight" and row["status"] not in {
                "preflighting", "cancelling"
            }:
                raise ValueError("deployment is not in a completable preflight state")
            if phase == "apply" and row["status"] not in {
                "deploying", "verifying", "rolling_back", "cancelling"
            }:
                raise ValueError("deployment is not in a completable apply state")
            if phase not in {"preflight", "apply"}:
                raise ValueError("deployment phase cannot be completed")
            if phase == "preflight":
                target_states = [
                    str(item[0])
                    for item in cursor.execute(
                        """SELECT status FROM fleet_deployment_targets
                           WHERE deployment_id = ? ORDER BY ordinal""",
                        (deployment_id,),
                    ).fetchall()
                ]
                preflight_complete = bool(target_states) and all(
                    state == "build_ready" for state in target_states
                )
                if ok and not preflight_complete:
                    ok = False
                    result = {
                        **dict(result),
                        "error_code": "preflight_targets_incomplete",
                        "target_states": target_states,
                    }
                if bool(row["cancel_requested"]):
                    status = "cancelled"
                    next_phase = "complete"
                    finished_at = now
                    contract_hash = str(row["contract_hash"])
                elif not ok:
                    status = "failed"
                    next_phase = "complete"
                    finished_at = now
                    contract_hash = str(row["contract_hash"])
                else:
                    preflight = dict(result)
                    contract = _decode(row["contract_json"], {})
                    contract_hash = approval_contract_hash(contract, preflight)
                    status = "awaiting_approval"
                    next_phase = "apply"
                    finished_at = None
                    cursor.execute(
                        """UPDATE fleet_deployments SET preflight_json = ?
                           WHERE deployment_id = ?""",
                        (canonical_json(preflight), deployment_id),
                    )
            else:
                target_states = [
                    str(item[0])
                    for item in cursor.execute(
                        """SELECT status FROM fleet_deployment_targets
                           WHERE deployment_id = ? ORDER BY ordinal""",
                        (deployment_id,),
                    ).fetchall()
                ]
                if bool(row["cancel_requested"]) and any(
                    state in {"unknown", "rollback_failed"} for state in target_states
                ):
                    status = "needs_attention"
                elif bool(row["cancel_requested"]) and not any(
                    state == "succeeded" for state in target_states
                ):
                    status = (
                        "rolled_back"
                        if any(state == "rolled_back" for state in target_states)
                        else "cancelled"
                    )
                elif ok and target_states and all(state == "succeeded" for state in target_states):
                    status = "succeeded"
                elif any(
                    state in {"unknown", "rollback_failed"} for state in target_states
                ):
                    status = "needs_attention"
                elif any(state == "rolled_back" for state in target_states):
                    status = "rolled_back"
                elif any(state == "succeeded" for state in target_states):
                    status = "partial"
                else:
                    status = "failed"
                next_phase = "complete"
                finished_at = now
                contract_hash = str(row["contract_hash"])
            cursor.execute(
                """UPDATE fleet_deployments SET status = ?, phase = ?,
                   contract_hash = ?, result_json = ?, deployer_id = NULL,
                   lease_expires_at = NULL, updated_at = ?, finished_at = ?,
                   resource_version = resource_version + 1
                   WHERE deployment_id = ?""",
                (
                    status,
                    next_phase,
                    contract_hash,
                    canonical_json(dict(result)),
                    now,
                    finished_at,
                    deployment_id,
                ),
            )
            cursor.execute(
                "DELETE FROM fleet_maintenance_locks WHERE deployment_id = ?",
                (deployment_id,),
            )
            self._event(
                cursor,
                deployment_id,
                event_type=f"{phase}_completed",
                status=status,
                actor_id=f"deployer:{deployer_id}",
                fence=fence,
                payload={"ok": bool(ok)},
                now=now,
            )
            connection.commit()
            return self.get(deployment_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def approve(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        contract_hash: str,
        resource_version: int,
        expires_at: int,
    ) -> dict[str, Any]:
        if not actor_id.startswith("admin:"):
            raise PermissionError("only an administrator can approve deployments")
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_deployments WHERE deployment_id = ? FOR UPDATE",
                (deployment_id,),
            ).fetchone()
            if row is None:
                raise LookupError("deployment not found")
            if row["status"] != "awaiting_approval":
                raise ValueError("deployment is not awaiting approval")
            if (
                row["contract_hash"] != contract_hash
                or int(row["resource_version"]) != int(resource_version)
            ):
                raise ValueError("deployment preview changed; review it again")
            if int(row["deadline_at"]) <= now:
                raise ValueError("deployment deadline expired")
            approval_id = new_handle("deploy_approval")
            approved_version = int(row["resource_version"]) + 1
            cursor.execute(
                """INSERT INTO fleet_deployment_approvals
                   (approval_id, deployment_id, actor_id, contract_hash,
                    resource_version, approved_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval_id,
                    deployment_id,
                    actor_id,
                    contract_hash,
                    approved_version,
                    now,
                    min(int(expires_at), int(row["deadline_at"])),
                ),
            )
            cursor.execute(
                """UPDATE fleet_deployments SET approval_ref = ?, status = 'queued',
                   resource_version = ?, updated_at = ? WHERE deployment_id = ?""",
                (approval_id, approved_version, now, deployment_id),
            )
            self._event(
                cursor,
                deployment_id,
                event_type="approved",
                status="queued",
                actor_id=actor_id,
                payload={"approval_id": approval_id},
                now=now,
            )
            connection.commit()
            return self.get(deployment_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def cancel(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        origin_scope: str,
    ) -> dict[str, Any]:
        now = int(time.time())
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            row = cursor.execute(
                "SELECT * FROM fleet_deployments WHERE deployment_id = ? FOR UPDATE",
                (deployment_id,),
            ).fetchone()
            if row is None:
                raise LookupError("deployment not found")
            if not actor_id.startswith("admin:") and (
                row["actor_id"] != actor_id or row["origin_scope"] != origin_scope
            ):
                raise PermissionError("deployment belongs to another scope")
            if row["status"] in TERMINAL_DEPLOYMENT_STATES:
                return self._deployment(row, cursor)
            running = row["status"] in {
                "preflighting", "deploying", "verifying", "rolling_back"
            }
            status = "cancelling" if running else "cancelled"
            phase = row["phase"] if running else "complete"
            cursor.execute(
                """UPDATE fleet_deployments SET status = ?, phase = ?,
                   cancel_requested = ?, updated_at = ?,
                   finished_at = CASE WHEN ? THEN finished_at ELSE ? END,
                   resource_version = resource_version + 1
                   WHERE deployment_id = ?""",
                (status, phase, running, now, running, now, deployment_id),
            )
            if not running:
                cursor.execute(
                    "DELETE FROM fleet_maintenance_locks WHERE deployment_id = ?",
                    (deployment_id,),
                )
            self._event(
                cursor,
                deployment_id,
                event_type="cancel_requested",
                status=status,
                actor_id=actor_id,
                fence=int(row["fence"]),
                now=now,
            )
            connection.commit()
            return self.get(deployment_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
