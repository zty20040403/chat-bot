"""Versioned task controls and a durable fence for external deliveries."""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class JobFence:
    job_id: int
    owner: str
    attempt: int
    check: Callable[[], bool]

    def assert_owned(self) -> None:
        if not self.check():
            raise LeaseLost("Sub-Agent worker no longer owns its task lease")


class LeaseLost(BaseException):
    """Must escape ordinary model/tool retry handlers."""


active_job_fence: ContextVar[JobFence | None] = ContextVar("subagent_job_fence", default=None)
active_task_id: ContextVar[int | None] = ContextVar("subagent_task_id", default=None)
active_model_policy: ContextVar[dict[str, Any]] = ContextVar("subagent_model_policy", default={})


def assert_job_owned() -> None:
    fence = active_job_fence.get()
    if fence:
        fence.assert_owned()


CONTROL_SQL = """
CREATE TABLE IF NOT EXISTS subagent_controls (
    task_id INTEGER PRIMARY KEY REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
    version INTEGER NOT NULL, revision INTEGER NOT NULL,
    policy_json TEXT NOT NULL, dispatch_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS subagent_deliveries (
    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, delivery_key TEXT NOT NULL,
    state TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, revision, delivery_key)
);
"""


class TaskControlStoreMixin:
    def run_resume_safe(self, run_id: int) -> bool:
        with self._lock:
            events = self._connection.execute("SELECT sequence, event_type, payload_json FROM subagent_events WHERE run_id=? AND event_type IN ('agent.tool_started','agent.tool_finished') ORDER BY sequence", (run_id,)).fetchall()
            session = self._connection.execute("SELECT transcript_json, covered_sequence FROM subagent_sessions WHERE run_id=?", (run_id,)).fetchone()
        acknowledged = {m.get("tool_call_id") for m in json.loads(session["transcript_json"]) if m.get("role") == "tool"} if session else set()
        pending = {}
        for event in events:
            value = json.loads(event["payload_json"])
            if value.get("idempotency") in {"pure", "idempotent"}:
                continue
            if not session or int(event["sequence"]) > int(session["covered_sequence"]):
                return False
            call_id = value.get("call_id")
            if not call_id:
                return False
            if event["event_type"] == "agent.tool_started":
                pending[call_id] = value
            elif value.get("state") == "succeeded" and call_id in acknowledged:
                pending.pop(call_id, None)
            else:
                return False
        return not pending

    def interrupt_task(self, task_id: int) -> None:
        running = [r.run_id for r in self.runs(task_id) if r.status in {"running", "interrupted"}]
        with self._transaction() as cursor:
            cursor.execute("UPDATE subagent_runs SET status='interrupted' WHERE task_id=? AND status='running'", (task_id,))
            cursor.execute("UPDATE subagent_tasks SET status='interrupted', finished_at=NULL WHERE task_id=?", (task_id,))
        self.append_checkpoint(task_id, "process_interrupted", {"interrupted_runs": running, "reason": "worker_takeover"})

    def control(self, task_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM subagent_controls WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return {"version": 0, "revision": 1, "policy": {"mode": "auto"}, "dispatch": {}}
        return {"version": int(row["version"]), "revision": int(row["revision"]),
                "policy": json.loads(row["policy_json"]), "dispatch": json.loads(row["dispatch_json"])}

    def update_control(self, task_id: int, *, expected_version: int, policy=None, dispatch=None, revision=None) -> dict[str, Any]:
        current = self.control(task_id)
        if current["version"] != expected_version:
            raise ValueError("Task version changed; refresh before editing")
        value = {**current, "version": expected_version + 1}
        for key, update in (("policy", policy), ("dispatch", dispatch), ("revision", revision)):
            if update is not None:
                value[key] = update
        with self._transaction() as cursor:
            if policy is not None:
                lock = "" if self._legacy_sqlite else " FOR UPDATE"
                task = cursor.execute("SELECT status FROM subagent_tasks WHERE task_id=?" + lock, (task_id,)).fetchone()
                if task is None or task["status"] in {"running", "planning", "verifying", "cancelling", "revising"}:
                    raise ValueError("Task started before the model update; wait until it stops")
            if expected_version == 0:
                cursor.execute("""INSERT INTO subagent_controls
                    (task_id, version, revision, policy_json, dispatch_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO NOTHING""",
                    (task_id, value["version"], value["revision"], json.dumps(value["policy"]), json.dumps(value["dispatch"]), int(time.time())))
            else:
                cursor.execute("""UPDATE subagent_controls SET version=?, revision=?, policy_json=?, dispatch_json=?, updated_at=?
                    WHERE task_id=? AND version=?""",
                    (value["version"], value["revision"], json.dumps(value["policy"]), json.dumps(value["dispatch"]), int(time.time()), task_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError("Task version changed; refresh before editing")
            if expected_version == 0 and dispatch:
                cursor.execute("UPDATE subagent_tasks SET status='queued' WHERE task_id=? AND status='received'", (task_id,))
        self._notify_changed(task_id)
        return value

    def begin_delivery(self, task_id: int, key: str, payload: dict[str, Any]) -> bool:
        revision = self.control(task_id)["revision"]
        with self._transaction() as cursor:
            cursor.execute("""INSERT INTO subagent_deliveries
                (task_id, revision, delivery_key, state, payload_json, updated_at)
                VALUES (?, ?, ?, 'sending', ?, ?) ON CONFLICT DO NOTHING""",
                (task_id, revision, key, json.dumps(payload, ensure_ascii=False), int(time.time())))
            return cursor.rowcount == 1

    def finish_delivery(self, task_id: int, key: str, state: str, payload: dict[str, Any], *, revision: int | None = None) -> None:
        if state not in {"acknowledged", "unknown", "rejected"}:
            raise ValueError("Invalid delivery state")
        if revision is None:
            revision = self.control(task_id)["revision"]
        with self._transaction() as cursor:
            cursor.execute("""UPDATE subagent_deliveries SET state=?, payload_json=?, updated_at=?
                WHERE task_id=? AND revision=? AND delivery_key=?""",
                (state, json.dumps(payload, ensure_ascii=False), int(time.time()), task_id, revision, key))
        self._notify_changed(task_id)

    def deliveries(self, task_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM subagent_deliveries WHERE task_id=? ORDER BY revision, updated_at", (task_id,)).fetchall()
        return [{"key": row["delivery_key"], "revision": row["revision"], "state": row["state"],
                 "payload": json.loads(row["payload_json"]), "updated_at": row["updated_at"]} for row in rows]

    def dispatchable_tasks(self) -> list[int]:
        with self._lock:
            rows = self._connection.execute("""SELECT t.task_id FROM subagent_tasks t JOIN subagent_controls c ON t.task_id=c.task_id
                WHERE t.status IN ('queued', 'interrupted') AND t.cancel_requested=?
                ORDER BY t.created_at LIMIT 100""", (False,)).fetchall()
        return [int(row["task_id"]) for row in rows]

    def uncertain_deliveries(self) -> list[int]:
        with self._lock:
            rows = self._connection.execute("SELECT DISTINCT task_id FROM subagent_deliveries WHERE state IN ('unknown','sending') AND updated_at<? LIMIT 10", (int(time.time()) - 60,)).fetchall()
        return [int(row["task_id"]) for row in rows]
