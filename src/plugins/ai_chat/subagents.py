from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from src.bot_storage import DatabaseSource, PostgresDatabase, open_store_connection

from .agent import (
    AGENT_SPECS,
    DEFAULT_AGENT_REGISTRY,
    WORKER_ROLES,
    AgentContext,
    AgentRegistry,
    AgentResult,
    AgentSpec,
    ContextPacket,
    SubAgentRole,
)
from .ai_tools import ToolDefinition
from .deepseek import (
    AgentLoopEvent,
    DeepSeekConfigError,
    DeepSeekTrace,
    ask_deepseek,
    ask_deepseek_json,
    ask_deepseek_with_tools,
)
from .model_catalog import ModelCatalog, ModelProfile


@dataclass(frozen=True)
class SubAgentRouteDecision:
    delegate: bool
    domains: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


_ROUTE_DOMAIN_PATTERNS: dict[str, re.Pattern[str]] = {
    "research": re.compile(
        r"(?:搜索|查(?:一下|找|资料|参数|来源|官网|文献|新闻)|调研|核实|考证|来源|链接)"
    ),
    "media": re.compile(r"(?:图片|这张图|截图|视频|音频|语音|字幕|帖子|分享)"),
    "analysis": re.compile(r"(?:分析|对比|比较|评估|统计|归纳|综合|核对)"),
    "document": re.compile(
        r"(?:pdf|报告|文档|表格|ppt|幻灯片|压缩包|交付物|发到群|发回来|生成文件)"
    ),
    "code": re.compile(r"(?:代码|编程|项目|修复|测试|构建|打包|脚本)"),
    "operations": re.compile(
        r"(?:部署|安装|配置|服务器|数据库|服务|告警|日志|监控|rebuild|重启)"
    ),
}
_ROUTE_SEQUENCE_PATTERN = re.compile(
    r"(?:先.+(?:再|然后|之后|最后)|(?:然后|再|接着|最后|并且|同时).+)"
)
_ROUTE_MULTI_SOURCE_PATTERN = re.compile(
    r"(?:(?:至少|多个|两个|三个|四个|多方).{0,6}(?:来源|网站|资料)|"
    r"(?:来源|网站|资料).{0,6}(?:对比|比较|交叉)|交叉核实)"
)
_ROUTE_LONG_ACTION_PATTERN = re.compile(
    r"(?:部署|完整项目|生成.{0,12}(?:pdf|报告|文档|文件)|"
    r"(?:修复|编写|修改).{0,12}(?:测试|构建|打包)|"
    r"(?:下载|读取).{0,12}(?:分析|整理|生成))"
)
_DELIVERY_REQUEST_PATTERN = re.compile(
    r"(?:发到群|发群里|发出来|发送到群|传到群|上传到群|发给我|交付(?:文件|pdf|文档))",
    re.IGNORECASE,
)
_SANDBOX_ARTIFACT_HANDLE_PATTERN = re.compile(
    r"^(s[0-9a-f]{6}):(/workspace/.+)$"
)


def route_subagent_request(
    user_text: str,
    *,
    has_media: bool = False,
) -> SubAgentRouteDecision:
    """Route obvious multi-stage work before the main ReAct loop starts."""

    normalized = re.sub(r"\s+", " ", user_text.strip().lower())
    if not normalized:
        return SubAgentRouteDecision(False)

    domains = {
        name
        for name, pattern in _ROUTE_DOMAIN_PATTERNS.items()
        if pattern.search(normalized)
    }
    if has_media and any(
        marker in normalized
        for marker in ("这", "看", "分析", "识别", "产品", "内容")
    ):
        domains.add("media")

    has_sequence = bool(_ROUTE_SEQUENCE_PATTERN.search(normalized))
    has_multi_source = bool(_ROUTE_MULTI_SOURCE_PATTERN.search(normalized))
    has_long_action = bool(_ROUTE_LONG_ACTION_PATTERN.search(normalized))
    reasons: list[str] = []

    if has_multi_source and "document" in domains:
        reasons.append("multi_source_artifact")
    if "document" in domains and len(domains - {"document"}) >= 2:
        reasons.append("cross_domain_artifact")
    if has_sequence and len(domains) >= 3:
        reasons.append("multi_stage_workflow")
    if has_long_action and (
        len(domains) >= 2 or bool(domains & {"code", "operations"})
    ):
        reasons.append("long_running_delivery")

    return SubAgentRouteDecision(
        bool(reasons),
        tuple(sorted(domains)),
        tuple(dict.fromkeys(reasons)),
    )


@dataclass(frozen=True)
class TaskStep:
    key: str
    role: SubAgentRole
    objective: str
    deliverable: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskRecord:
    task_id: int
    trace_id: str
    scope_key: str
    conversation_id: str
    requester_user_id: int
    trigger_message_id: int | None
    objective: str
    status: str
    plan: dict[str, Any]
    result: dict[str, Any]
    last_error: str
    cancel_requested: bool
    created_at: int
    updated_at: int
    finished_at: int | None

    @property
    def handle(self) -> str:
        return f"task#{self.task_id}"


@dataclass(frozen=True)
class RunRecord:
    run_id: int
    task_id: int
    step_key: str
    role: str
    objective: str
    deliverable: str
    dependencies: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    model_profile: str
    status: str
    attempt: int
    result: dict[str, Any]
    last_error: str
    created_at: int
    started_at: int | None
    finished_at: int | None

    @property
    def handle(self) -> str:
        return f"agent#{self.run_id}"


class SubAgentStore:
    def __init__(self, source: DatabaseSource) -> None:
        self._legacy_sqlite = not isinstance(source, PostgresDatabase)
        self.path, self._connection = open_store_connection(source)
        self._lock = threading.RLock()
        self._change_listener: Callable[[int], None] | None = None
        if self._legacy_sqlite:
            self._configure()
            self._migrate()
        self.recovered_tasks = self.recover_interrupted()

    def set_change_listener(
        self,
        listener: Callable[[int], None] | None,
    ) -> None:
        self._change_listener = listener

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_task(
        self,
        *,
        scope_key: str,
        conversation_id: str,
        requester_user_id: int,
        trigger_message_id: int | None,
        objective: str,
        max_parallelism: int,
        max_steps: int,
        now: int | None = None,
    ) -> TaskRecord:
        timestamp = int(time.time() if now is None else now)
        trace_id = uuid.uuid4().hex
        with self._transaction() as cursor:
            row = cursor.execute(
                """
                INSERT INTO subagent_tasks (
                    trace_id, scope_key, conversation_id, requester_user_id,
                    trigger_message_id, objective, status, priority,
                    max_parallelism, max_steps, plan_json, result_json,
                    last_error, cancel_requested, created_at, updated_at,
                    finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'received', 100, ?, ?, '{}', '{}',
                          '', ?, ?, ?, NULL)
                RETURNING *
                """,
                (
                    trace_id,
                    str(scope_key)[:300],
                    str(conversation_id)[:300],
                    int(requester_user_id),
                    trigger_message_id,
                    str(objective).strip(),
                    int(max_parallelism),
                    int(max_steps),
                    False,
                    timestamp,
                    timestamp,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("Sub-Agent task was not stored")
        task = self._task_row(row)
        self.append_event(task.task_id, "task.created", {"objective": task.objective})
        return task

    def set_task_state(
        self,
        task_id: int,
        status: str,
        *,
        plan: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        error: str = "",
        now: int | None = None,
    ) -> bool:
        timestamp = int(time.time() if now is None else now)
        finished_at = timestamp if status in {"completed", "partial", "failed", "cancelled"} else None
        assignments = ["status = ?", "last_error = ?", "updated_at = ?", "finished_at = ?"]
        values: list[Any] = [status, str(error)[:4000], timestamp, finished_at]
        if plan is not None:
            assignments.append("plan_json = ?")
            values.append(_json_dump(plan))
        if result is not None:
            assignments.append("result_json = ?")
            values.append(_json_dump(result))
        values.append(int(task_id))
        with self._transaction() as cursor:
            cursor.execute(
                f"UPDATE subagent_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                tuple(values),
            )
            changed = cursor.rowcount == 1
        if changed:
            self.append_event(task_id, f"task.{status}", {"error": str(error)[:1000]})
        return changed

    def create_run(
        self,
        task_id: int,
        step: TaskStep,
        *,
        allowed_tools: Sequence[str],
        model_profile: str,
        now: int | None = None,
    ) -> RunRecord:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                """
                INSERT INTO subagent_runs (
                    task_id, step_key, role, objective, deliverable,
                    dependencies_json, allowed_tools_json, model_profile,
                    status, attempt, result_json, last_error, created_at,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, '{}', '', ?, NULL, NULL)
                RETURNING *
                """,
                (
                    int(task_id),
                    step.key,
                    step.role,
                    step.objective,
                    step.deliverable,
                    _json_dump(list(step.dependencies)),
                    _json_dump(list(allowed_tools)),
                    str(model_profile),
                    timestamp,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("Sub-Agent run was not stored")
        run = self._run_row(row)
        self.append_event(
            task_id,
            "run.created",
            {"run_id": run.run_id, "step": run.step_key, "role": run.role},
            run_id=run.run_id,
        )
        return run

    def start_run(self, run_id: int, *, now: int | None = None) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT task_id FROM subagent_runs WHERE run_id = ?",
                (int(run_id),),
            ).fetchone()
            if row is None:
                return False
            cursor.execute(
                """
                UPDATE subagent_runs
                SET status = 'running', attempt = attempt + 1,
                    started_at = ?, finished_at = NULL, last_error = ''
                WHERE run_id = ? AND status = 'pending'
                """,
                (timestamp, int(run_id)),
            )
            changed = cursor.rowcount == 1
            task_id = int(row["task_id"])
        if changed:
            self.append_event(
                task_id,
                "run.running",
                {"run_id": int(run_id)},
                run_id=run_id,
                now=timestamp,
            )
        return changed

    def prepare_run_retry(
        self,
        run_id: int,
        *,
        max_attempts: int,
        now: int | None = None,
    ) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT task_id, attempt FROM subagent_runs WHERE run_id = ?",
                (int(run_id),),
            ).fetchone()
            if row is None or int(row["attempt"]) >= int(max_attempts):
                return False
            cursor.execute(
                """
                UPDATE subagent_runs
                SET status = 'pending', last_error = '', started_at = NULL,
                    finished_at = NULL
                WHERE run_id = ? AND status = 'failed'
                """,
                (int(run_id),),
            )
            changed = cursor.rowcount == 1
            task_id = int(row["task_id"])
            attempt = int(row["attempt"])
        if changed:
            self.append_event(
                task_id,
                "run.retry_scheduled",
                {
                    "run_id": int(run_id),
                    "attempt": attempt,
                    "max_attempts": int(max_attempts),
                    "scheduled_at": timestamp,
                },
                run_id=run_id,
                now=timestamp,
            )
        return changed

    def run_retry_safe(self, run_id: int) -> bool:
        """Only retry when no successful non-idempotent side effect was observed."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM subagent_events
                WHERE run_id = ? AND event_type = 'agent.tool_finished'
                ORDER BY sequence
                """,
                (int(run_id),),
            ).fetchall()
        for row in rows:
            payload = _json_object(row["payload_json"])
            if str(payload.get("idempotency") or "") not in {
                "pure",
                "idempotent",
            } and str(payload.get("state") or "") in {
                "succeeded",
                "handed-off",
                "outcome-unknown",
                "",
            }:
                return False
        return True

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: str = "",
        now: int | None = None,
    ) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT task_id FROM subagent_runs WHERE run_id = ?",
                (int(run_id),),
            ).fetchone()
            if row is None:
                return False
            cursor.execute(
                """
                UPDATE subagent_runs
                SET status = ?, result_json = ?, last_error = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _json_dump(result or {}),
                    str(error)[:4000],
                    timestamp,
                    int(run_id),
                ),
            )
            changed = cursor.rowcount == 1
            task_id = int(row["task_id"])
        if changed:
            self.append_event(
                task_id,
                f"run.{status}",
                {"run_id": int(run_id), "error": str(error)[:1000]},
                run_id=run_id,
            )
        return changed

    def settle_unfinished_runs(
        self,
        task_id: int,
        *,
        running_status: str,
        pending_status: str,
        error: str,
        now: int | None = None,
    ) -> int:
        timestamp = int(time.time() if now is None else now)
        transitions: list[tuple[int, str]] = []
        with self._transaction() as cursor:
            rows = cursor.execute(
                """
                SELECT run_id, status FROM subagent_runs
                WHERE task_id = ? AND status IN ('pending', 'running')
                ORDER BY run_id
                """,
                (int(task_id),),
            ).fetchall()
            for row in rows:
                status = (
                    running_status if str(row["status"]) == "running" else pending_status
                )
                cursor.execute(
                    """
                    UPDATE subagent_runs
                    SET status = ?, last_error = ?, finished_at = ?
                    WHERE run_id = ? AND status IN ('pending', 'running')
                    """,
                    (status, str(error)[:4000], timestamp, int(row["run_id"])),
                )
                if cursor.rowcount == 1:
                    transitions.append((int(row["run_id"]), status))
        for run_id, status in transitions:
            self.append_event(
                task_id,
                f"run.{status}",
                {"run_id": run_id, "error": str(error)[:1000]},
                run_id=run_id,
                now=timestamp,
            )
        return len(transitions)

    def append_event(
        self,
        task_id: int,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        run_id: int | None = None,
        now: int | None = None,
    ) -> None:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM subagent_events WHERE task_id = ?",
                (int(task_id),),
            ).fetchone()
            sequence = int(row["sequence"] if row is not None else 0) + 1
            cursor.execute(
                """
                INSERT INTO subagent_events (
                    task_id, run_id, sequence, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(task_id),
                    run_id,
                    sequence,
                    str(event_type)[:120],
                    _json_dump(payload or {}),
                    timestamp,
                ),
            )
        self._notify_changed(task_id)

    def _notify_changed(self, task_id: int) -> None:
        listener = self._change_listener
        if listener is None:
            return
        try:
            listener(int(task_id))
        except Exception:
            # Observability must never make the durable task transition fail.
            return

    def add_artifacts(
        self,
        task_id: int,
        run_id: int,
        artifacts: Sequence[object],
        *,
        now: int | None = None,
    ) -> None:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            for item in artifacts:
                raw = item if isinstance(item, Mapping) else {"handle": str(item)}
                handle = str(raw.get("handle") or "").strip()[:500]
                if not handle:
                    continue
                cursor.execute(
                    """
                    INSERT INTO subagent_artifacts (
                        task_id, run_id, kind, handle, name, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, handle) DO NOTHING
                    """,
                    (
                        int(task_id),
                        int(run_id),
                        str(raw.get("kind") or "artifact")[:80],
                        handle,
                        str(raw.get("name") or "")[:300],
                        _json_dump(dict(raw)),
                        timestamp,
                    ),
                )

    def append_checkpoint(
        self,
        task_id: int,
        phase: str,
        state: Mapping[str, Any],
        *,
        run_id: int | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            row = cursor.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS sequence
                FROM subagent_checkpoints WHERE task_id = ?
                """,
                (int(task_id),),
            ).fetchone()
            sequence = int(row["sequence"] if row is not None else 0) + 1
            stored = cursor.execute(
                """
                INSERT INTO subagent_checkpoints (
                    task_id, run_id, sequence, phase, state_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                RETURNING checkpoint_id
                """,
                (
                    int(task_id),
                    run_id,
                    sequence,
                    str(phase)[:120],
                    _json_dump(state),
                    timestamp,
                ),
            ).fetchone()
        checkpoint = {
            "checkpoint_id": int(stored["checkpoint_id"]),
            "task_id": int(task_id),
            "run_id": run_id,
            "sequence": sequence,
            "phase": str(phase)[:120],
            "state": dict(state),
            "created_at": timestamp,
        }
        self.append_event(
            task_id,
            "checkpoint.created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "phase": checkpoint["phase"],
            },
            run_id=run_id,
            now=timestamp,
        )
        return checkpoint

    def checkpoints(self, task_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM subagent_checkpoints WHERE task_id = ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (int(task_id), min(max(int(limit), 1), 1000)),
            ).fetchall()
        return [
            {
                "checkpoint_id": int(row["checkpoint_id"]),
                "task_id": int(row["task_id"]),
                "run_id": int(row["run_id"]) if row["run_id"] is not None else None,
                "sequence": int(row["sequence"]),
                "phase": str(row["phase"]),
                "state": _json_object(row["state_json"]),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def save_run_context(
        self,
        task_id: int,
        run_id: int,
        context: AgentContext,
        *,
        now: int | None = None,
    ) -> AgentContext:
        """Persist the immutable context visible to one Agent run."""

        timestamp = int(time.time() if now is None else now)
        payload = context.as_payload()
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO subagent_run_contexts (
                    task_id, run_id, role, scope_key, context_hash,
                    context_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    int(task_id),
                    int(run_id),
                    context.role,
                    context.scope_key,
                    context.context_hash,
                    _json_dump(payload),
                    timestamp,
                ),
            )
            inserted = cursor.rowcount == 1
            row = cursor.execute(
                """
                SELECT task_id, run_id, role, scope_key, context_hash, context_json
                FROM subagent_run_contexts WHERE run_id = ?
                """,
                (int(run_id),),
            ).fetchone()
        if row is None or int(row["task_id"]) != int(task_id):
            raise RuntimeError("Sub-Agent context does not belong to this task")
        stored = AgentContext.from_payload(_json_object(row["context_json"]))
        if (
            stored.role != str(row["role"])
            or stored.scope_key != str(row["scope_key"])
            or stored.context_hash != str(row["context_hash"])
        ):
            raise RuntimeError("Sub-Agent context snapshot failed integrity validation")
        if inserted:
            self.append_event(
                task_id,
                "run.context_frozen",
                {
                    "run_id": int(run_id),
                    "role": context.role,
                    "context_hash": context.context_hash,
                    "agent_definition_version": context.agent_definition_version,
                },
                run_id=run_id,
                now=timestamp,
            )
        return stored

    def run_context(self, run_id: int) -> AgentContext | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT context_json FROM subagent_run_contexts WHERE run_id = ?",
                (int(run_id),),
            ).fetchone()
        if row is None:
            return None
        return AgentContext.from_payload(_json_object(row["context_json"]))

    def run_contexts(self, task_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM subagent_run_contexts
                WHERE task_id = ? ORDER BY context_id
                """,
                (int(task_id),),
            ).fetchall()
        return [
            {
                "context_id": int(row["context_id"]),
                "task_id": int(row["task_id"]),
                "run_id": int(row["run_id"]),
                "role": str(row["role"]),
                "scope_key": str(row["scope_key"]),
                "context_hash": str(row["context_hash"]),
                "context": _json_object(row["context_json"]),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def request_cancel(self, task_id: int, *, now: int | None = None) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE subagent_tasks
                SET cancel_requested = ?, status = 'cancelling', updated_at = ?
                WHERE task_id = ? AND status IN (
                    'received', 'planning', 'running', 'verifying', 'interrupted'
                )
                """,
                (True, timestamp, int(task_id)),
            )
            changed = cursor.rowcount == 1
        if changed:
            self.append_event(task_id, "task.cancel_requested")
        return changed

    def cancellation_requested(self, task_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT cancel_requested FROM subagent_tasks WHERE task_id = ?",
                (int(task_id),),
            ).fetchone()
        return bool(row is not None and row["cancel_requested"])

    def prepare_resume(self, task_id: int, *, now: int | None = None) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE subagent_tasks
                SET status = 'running', last_error = '', updated_at = ?,
                    finished_at = NULL
                WHERE task_id = ? AND status = 'interrupted'
                    AND cancel_requested = ?
                """,
                (timestamp, int(task_id), False),
            )
            changed = cursor.rowcount == 1
            if changed:
                cursor.execute(
                    """
                    UPDATE subagent_runs
                    SET status = 'pending', last_error = '', started_at = NULL,
                        finished_at = NULL
                    WHERE task_id = ? AND status = 'interrupted'
                    """,
                    (int(task_id),),
                )
        if changed:
            self.append_event(
                task_id,
                "task.resumed",
                {"reason": "checkpoint_resume"},
                now=timestamp,
            )
        return changed

    def get(self, task_id: int) -> TaskRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM subagent_tasks WHERE task_id = ?",
                (int(task_id),),
            ).fetchone()
        return self._task_row(row) if row is not None else None

    def recent(self, *, limit: int = 100, scope_key: str = "") -> list[TaskRecord]:
        query = "SELECT * FROM subagent_tasks"
        params: list[Any] = []
        if scope_key:
            query += " WHERE scope_key = ?"
            params.append(str(scope_key))
        query += " ORDER BY created_at DESC, task_id DESC LIMIT ?"
        params.append(min(max(int(limit), 1), 500))
        with self._lock:
            rows = self._connection.execute(query, tuple(params)).fetchall()
        return [self._task_row(row) for row in rows]

    def runs(self, task_id: int) -> list[RunRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM subagent_runs WHERE task_id = ? ORDER BY run_id",
                (int(task_id),),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def events(self, task_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM subagent_events WHERE task_id = ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (int(task_id), min(max(int(limit), 1), 2000)),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "task_id": int(row["task_id"]),
                "run_id": int(row["run_id"]) if row["run_id"] is not None else None,
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "payload": _json_object(row["payload_json"]),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM subagent_tasks GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def recover_interrupted(self, *, now: int | None = None) -> int:
        timestamp = int(time.time() if now is None else now)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT task_id FROM subagent_tasks
                WHERE status IN ('received', 'planning', 'running', 'verifying', 'cancelling')
                ORDER BY task_id
                """
            ).fetchall()
        task_ids = [int(row["task_id"]) for row in rows]
        for task_id in task_ids:
            interrupted_runs: list[int] = []
            with self._transaction() as cursor:
                rows = cursor.execute(
                    """
                    SELECT run_id FROM subagent_runs
                    WHERE task_id = ? AND status = 'running'
                    ORDER BY run_id
                    """,
                    (task_id,),
                ).fetchall()
                interrupted_runs = [int(row["run_id"]) for row in rows]
                cursor.execute(
                    """
                    UPDATE subagent_runs
                    SET status = 'interrupted', last_error = ?, finished_at = NULL
                    WHERE task_id = ? AND status = 'running'
                    """,
                    ("机器人重启，等待从检查点恢复", task_id),
                )
                cursor.execute(
                    """
                    UPDATE subagent_tasks
                    SET status = 'interrupted', last_error = ?, updated_at = ?,
                        finished_at = NULL
                    WHERE task_id = ?
                    """,
                    ("机器人重启，等待从检查点恢复", timestamp, task_id),
                )
            for run_id in interrupted_runs:
                self.append_event(
                    task_id,
                    "run.interrupted",
                    {"run_id": run_id, "reason": "process_restart"},
                    run_id=run_id,
                    now=timestamp,
                )
            self.append_checkpoint(
                task_id,
                "process_interrupted",
                {
                    "reason": "process_restart",
                    "interrupted_runs": interrupted_runs,
                    "pending_runs_preserved": True,
                },
                now=timestamp,
            )
            self.append_event(
                task_id,
                "task.interrupted",
                {"reason": "process_restart"},
                now=timestamp,
            )
        return len(task_ids)

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if self.path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")

    def _migrate(self) -> None:
        with self._transaction() as cursor:
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS subagent_tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL UNIQUE,
                    scope_key TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    requester_user_id INTEGER NOT NULL,
                    trigger_message_id INTEGER,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    max_parallelism INTEGER NOT NULL,
                    max_steps INTEGER NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_tasks_scope_time
                    ON subagent_tasks(scope_key, created_at, task_id);
                CREATE INDEX IF NOT EXISTS idx_subagent_tasks_status_time
                    ON subagent_tasks(status, updated_at, task_id);
                CREATE TABLE IF NOT EXISTS subagent_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
                    step_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    deliverable TEXT NOT NULL DEFAULT '',
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
                    model_profile TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    UNIQUE(task_id, step_key)
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_task_status
                    ON subagent_runs(task_id, status, run_id);
                CREATE TABLE IF NOT EXISTS subagent_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
                    run_id INTEGER REFERENCES subagent_runs(run_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_events_task_sequence
                    ON subagent_events(task_id, sequence);
                CREATE TABLE IF NOT EXISTS subagent_artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
                    run_id INTEGER REFERENCES subagent_runs(run_id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    UNIQUE(task_id, handle)
                );
                CREATE TABLE IF NOT EXISTS subagent_checkpoints (
                    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
                    run_id INTEGER REFERENCES subagent_runs(run_id) ON DELETE SET NULL,
                    sequence INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_checkpoints_task_sequence
                    ON subagent_checkpoints(task_id, sequence);
                CREATE TABLE IF NOT EXISTS subagent_run_contexts (
                    context_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES subagent_tasks(task_id) ON DELETE CASCADE,
                    run_id INTEGER NOT NULL UNIQUE REFERENCES subagent_runs(run_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_run_contexts_task
                    ON subagent_run_contexts(task_id, run_id);
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()
            finally:
                cursor.close()

    @staticmethod
    def _task_row(row: Any) -> TaskRecord:
        return TaskRecord(
            task_id=int(row["task_id"]),
            trace_id=str(row["trace_id"]),
            scope_key=str(row["scope_key"]),
            conversation_id=str(row["conversation_id"]),
            requester_user_id=int(row["requester_user_id"]),
            trigger_message_id=(
                int(row["trigger_message_id"])
                if row["trigger_message_id"] is not None
                else None
            ),
            objective=str(row["objective"]),
            status=str(row["status"]),
            plan=_json_object(row["plan_json"]),
            result=_json_object(row["result_json"]),
            last_error=str(row["last_error"]),
            cancel_requested=bool(row["cancel_requested"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            finished_at=(
                int(row["finished_at"]) if row["finished_at"] is not None else None
            ),
        )

    @staticmethod
    def _run_row(row: Any) -> RunRecord:
        return RunRecord(
            run_id=int(row["run_id"]),
            task_id=int(row["task_id"]),
            step_key=str(row["step_key"]),
            role=str(row["role"]),
            objective=str(row["objective"]),
            deliverable=str(row["deliverable"]),
            dependencies=tuple(_json_list(row["dependencies_json"])),
            allowed_tools=tuple(_json_list(row["allowed_tools_json"])),
            model_profile=str(row["model_profile"]),
            status=str(row["status"]),
            attempt=int(row["attempt"]),
            result=_json_object(row["result_json"]),
            last_error=str(row["last_error"]),
            created_at=int(row["created_at"]),
            started_at=(int(row["started_at"]) if row["started_at"] is not None else None),
            finished_at=(int(row["finished_at"]) if row["finished_at"] is not None else None),
        )


ProgressCallback = Callable[[str], Awaitable[None]]
ToolExecutor = Callable[[str, dict[str, object]], Awaitable[str]]
WorkerOutcomeState = Literal["success", "partial", "failed", "skipped"]
WorkerReportedState = Literal["success", "partial", "failed"]


@dataclass(frozen=True)
class AgentExecutionHooks:
    approval_checker: Callable[[Any, str, dict[str, Any]], Any] | None = None
    handoff_tool: Callable[
        [str, dict[str, Any], str], Awaitable[str | None]
    ] | None = None
    compensate_tool: Callable[
        [str, dict[str, Any], str], Awaitable[str | None]
    ] | None = None


@dataclass(frozen=True)
class StepOutcome:
    step: TaskStep
    run: RunRecord
    result: dict[str, Any]
    trace: DeepSeekTrace
    state: WorkerOutcomeState = "success"
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.state == "success"

    @property
    def usable(self) -> bool:
        return self.state in {"success", "partial"}


class SubAgentCoordinator:
    def __init__(
        self,
        store: SubAgentStore,
        model_catalog: ModelCatalog,
        *,
        logger: Any,
        max_steps: int = 8,
        max_parallelism: int = 3,
        max_tool_rounds: int = 6,
        timeout_seconds: int = 600,
        profile_overrides: Mapping[str, str] | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.store = store
        self.model_catalog = model_catalog
        self.logger = logger
        self.max_steps = min(max(int(max_steps), 1), 12)
        self.max_parallelism = min(max(int(max_parallelism), 1), 6)
        self.max_tool_rounds = min(max(int(max_tool_rounds), 1), 12)
        self.timeout_seconds = min(max(int(timeout_seconds), 30), 3600)
        self.max_adaptive_repairs = min(self.max_parallelism, 2)
        self.profile_overrides = dict(profile_overrides or {})
        self.registry = registry or DEFAULT_AGENT_REGISTRY
        self._active: dict[int, asyncio.Task[Any]] = {}

    @staticmethod
    def manifest() -> list[dict[str, object]]:
        return DEFAULT_AGENT_REGISTRY.manifest()

    def cancel(self, task_id: int) -> bool:
        changed = self.store.request_cancel(task_id)
        running = self._active.get(int(task_id))
        if running is not None and not running.done():
            running.cancel()
            return True
        return changed

    async def resume(
        self,
        task_id: int,
        *,
        scope_key: str,
        requester_user_id: int,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None = None,
        progress: ProgressCallback | None = None,
        hooks: AgentExecutionHooks | None = None,
    ) -> str:
        """Resume an interrupted task without replanning or widening context."""

        task = self.store.get(task_id)
        if task is None:
            raise ValueError(f"Sub-Agent 任务 task#{task_id} 不存在。")
        if task.scope_key != scope_key or task.requester_user_id != requester_user_id:
            raise ValueError("不能恢复其他群或其他用户发起的 Sub-Agent 任务。")
        if task.status != "interrupted":
            raise ValueError(f"{task.handle} 当前状态是 {task.status}，不能断点续跑。")
        context = _checkpoint_context_packet(self.store.checkpoints(task_id))
        if context is None:
            raise RuntimeError(f"{task.handle} 缺少可恢复的上下文检查点。")
        if context.scope_key != task.scope_key:
            raise RuntimeError(f"{task.handle} 的检查点作用域不一致。")
        if not self.store.prepare_resume(task_id):
            raise RuntimeError(f"{task.handle} 未能进入恢复状态。")

        current = asyncio.current_task()
        if current is not None:
            self._active[task.task_id] = current
        self.store.append_checkpoint(
            task.task_id,
            "resume_started",
            {"mode": str(task.plan.get("mode") or "workflow")},
        )
        await self._notify_progress(
            progress,
            f"{task.handle} 正在从检查点继续，已完成步骤不会重跑。",
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self._resume_task(
                    task,
                    context=context,
                    selected_profile=selected_profile,
                    tools=tools,
                    execute_tool=execute_tool,
                    parent_trace=parent_trace,
                    progress=progress,
                    hooks=hooks,
                )
                resumed_task = self.store.get(task.task_id)
                self.store.append_checkpoint(
                    task.task_id,
                    "resume_completed",
                    {
                        "status": resumed_task.status if resumed_task is not None else "unknown"
                    },
                )
                return result
        except asyncio.CancelledError:
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="cancelled",
                pending_status="skipped",
                error="任务已取消",
            )
            self.store.set_task_state(task.task_id, "cancelled", error="任务已取消")
            raise
        except TimeoutError:
            message = f"任务恢复后超过 {self.timeout_seconds} 秒，已停止。"
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="failed",
                pending_status="skipped",
                error=message,
            )
            self.store.set_task_state(task.task_id, "failed", error=message)
            return f"{task.handle} {message}"
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="failed",
                pending_status="skipped",
                error=message,
            )
            self.store.set_task_state(task.task_id, "failed", error=message)
            self.logger.warning("Sub-Agent resume %s failed: %s", task.handle, message)
            return f"{task.handle} 恢复失败：{message}"
        finally:
            self._active.pop(task.task_id, None)

    async def run(
        self,
        *,
        scope_key: str,
        conversation_id: str,
        requester_user_id: int,
        trigger_message_id: int | None,
        objective: str,
        context: str,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None = None,
        progress: ProgressCallback | None = None,
        context_packet: ContextPacket | None = None,
        hooks: AgentExecutionHooks | None = None,
    ) -> str:
        task = self.store.create_task(
            scope_key=scope_key,
            conversation_id=conversation_id,
            requester_user_id=requester_user_id,
            trigger_message_id=trigger_message_id,
            objective=objective,
            max_parallelism=self.max_parallelism,
            max_steps=self.max_steps,
        )
        current = asyncio.current_task()
        if current is not None:
            self._active[task.task_id] = current
        packet = context_packet or ContextPacket.from_legacy(
            scope_key=scope_key,
            conversation_id=conversation_id,
            requester_user_id=requester_user_id,
            trigger_message_id=trigger_message_id,
            objective=objective,
            context=context,
        )
        self.store.append_checkpoint(
            task.task_id,
            "task_received",
            {"mode": "workflow", "context_packet": packet.as_payload()},
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._run_task(
                    task,
                    context=packet,
                    selected_profile=selected_profile,
                    tools=tools,
                    execute_tool=execute_tool,
                    parent_trace=parent_trace,
                    progress=progress,
                    hooks=hooks,
                )
        except asyncio.CancelledError:
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="cancelled",
                pending_status="skipped",
                error="任务已取消",
            )
            self.store.set_task_state(task.task_id, "cancelled", error="任务已取消")
            raise
        except TimeoutError:
            message = f"任务超过 {self.timeout_seconds} 秒，已停止。"
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="failed",
                pending_status="skipped",
                error=message,
            )
            self.store.set_task_state(task.task_id, "failed", error=message)
            return f"{task.handle} {message}"
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="failed",
                pending_status="skipped",
                error=message,
            )
            self.store.set_task_state(task.task_id, "failed", error=message)
            self.logger.warning("Sub-Agent task %s failed: %s", task.handle, message)
            return f"{task.handle} 执行失败：{message}"
        finally:
            self._active.pop(task.task_id, None)

    async def delegate(
        self,
        *,
        role: str,
        scope_key: str,
        conversation_id: str,
        requester_user_id: int,
        trigger_message_id: int | None,
        objective: str,
        context: str,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None = None,
        context_packet: ContextPacket | None = None,
        hooks: AgentExecutionHooks | None = None,
    ) -> dict[str, Any]:
        """Run one bounded specialist without planner and synthesis model calls."""

        spec = self.registry.worker(role)
        task = self.store.create_task(
            scope_key=scope_key,
            conversation_id=conversation_id,
            requester_user_id=requester_user_id,
            trigger_message_id=trigger_message_id,
            objective=objective,
            max_parallelism=1,
            max_steps=1,
        )
        step = TaskStep(
            key="delegate",
            role=spec.role,
            objective=objective,
            deliverable="向主 Agent 返回有证据的结构化结果",
        )
        plan = {"goal": objective, "mode": "delegate", "steps": [_step_payload(step)]}
        self.store.set_task_state(task.task_id, "running", plan=plan)
        profile = self._profile_for(spec.role, selected_profile)
        tools_by_name = {_tool_name(tool): tool for tool in tools if _tool_name(tool)}
        allowed = sorted(spec.allowed_tools & tools_by_name.keys())
        run = self.store.create_run(
            task.task_id,
            step,
            allowed_tools=allowed,
            model_profile=profile.name,
        )
        packet = context_packet or ContextPacket.from_legacy(
            scope_key=scope_key,
            conversation_id=conversation_id,
            requester_user_id=requester_user_id,
            trigger_message_id=trigger_message_id,
            objective=objective,
            context=context,
        )
        self.store.append_checkpoint(
            task.task_id,
            "delegate_ready",
            {
                "mode": "delegate",
                "plan": plan,
                "context_packet": packet.as_payload(),
            },
            run_id=run.run_id,
        )
        current = asyncio.current_task()
        if current is not None:
            self._active[task.task_id] = current
        try:
            async with asyncio.timeout(min(self.timeout_seconds, spec.timeout_seconds)):
                outcome = await self._run_step_reliably(
                    task,
                    step,
                    run,
                    context=packet,
                    upstream={},
                    selected_profile=selected_profile,
                    tools_by_name=tools_by_name,
                    execute_tool=execute_tool,
                    hooks=hooks,
                )
                deliveries = await self._deliver_requested_artifacts(
                    task,
                    {step.key: outcome},
                    execute_tool=execute_tool,
                    delivered_artifacts=set(),
                    progress=None,
                )
                delivery_failed = any(not bool(item.get("ok")) for item in deliveries)
                if outcome.state == "failed":
                    status = "failed"
                elif outcome.state == "partial" or delivery_failed:
                    status = "partial"
                else:
                    status = "completed"
                result = {
                    "mode": "delegate",
                    "role": spec.role,
                    "agent": run.handle,
                    "result": outcome.result,
                    "deliveries": deliveries,
                }
                self.store.set_task_state(
                    task.task_id,
                    status,
                    result=result,
                    error=outcome.error,
                )
                self.store.append_checkpoint(
                    task.task_id,
                    "delegate_completed",
                    result,
                    run_id=run.run_id,
                )
                return {"task": task.handle, "status": status, **result}
        except TimeoutError:
            message = f"{spec.title} Agent 超过 {min(self.timeout_seconds, spec.timeout_seconds)} 秒。"
            self.store.settle_unfinished_runs(
                task.task_id,
                running_status="failed",
                pending_status="skipped",
                error=message,
            )
            self.store.set_task_state(task.task_id, "failed", error=message)
            return {
                "task": task.handle,
                "status": "failed",
                "mode": "delegate",
                "role": spec.role,
                "error": message,
            }
        finally:
            self._active.pop(task.task_id, None)

    async def _run_task(
        self,
        task: TaskRecord,
        *,
        context: ContextPacket,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None,
        progress: ProgressCallback | None,
        hooks: AgentExecutionHooks | None,
    ) -> str:
        self.store.set_task_state(task.task_id, "planning")
        await self._notify_progress(
            progress,
            f"{task.handle} · 主控 Agent：正在拆解目标、安排依赖和验收标准。",
        )
        planner_trace = DeepSeekTrace(trace_id=task.trace_id)
        planner_profile = self._profile_for("supervisor", selected_profile)
        plan_payload = await ask_deepseek_json(
            _planner_prompt(self.max_steps, self.registry),
            _planner_input(task.objective, context),
            profile=planner_profile,
            trace=planner_trace,
        )
        _merge_trace(parent_trace, planner_trace)
        steps = _validate_plan(plan_payload, task.objective, self.max_steps)
        normalized_plan = {
            "goal": task.objective,
            "steps": [_step_payload(step) for step in steps],
        }
        self.store.set_task_state(task.task_id, "running", plan=normalized_plan)
        self.store.append_checkpoint(
            task.task_id,
            "plan_ready",
            {
                "mode": "workflow",
                "plan": normalized_plan,
                "context_packet": context.as_payload(),
            },
        )
        runs: dict[str, RunRecord] = {}
        tools_by_name = {_tool_name(tool): tool for tool in tools if _tool_name(tool)}
        for step in steps:
            profile = self._profile_for(step.role, selected_profile)
            allowed = sorted(
                self.registry.worker(step.role).allowed_tools & tools_by_name.keys()
            )
            runs[step.key] = self.store.create_run(
                task.task_id,
                step,
                allowed_tools=allowed,
                model_profile=profile.name,
            )
        labels = "、".join(self.registry.worker(step.role).title for step in steps)
        await self._notify_progress(
            progress,
            f"{task.handle} 已拆成 {len(steps)} 步：{labels}。",
        )

        return await self._execute_workflow(
            task,
            steps=steps,
            runs=runs,
            context=context,
            selected_profile=selected_profile,
            tools_by_name=tools_by_name,
            execute_tool=execute_tool,
            parent_trace=parent_trace,
            progress=progress,
            hooks=hooks,
        )

    async def _resume_task(
        self,
        task: TaskRecord,
        *,
        context: ContextPacket,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None,
        progress: ProgressCallback | None,
        hooks: AgentExecutionHooks | None,
    ) -> str:
        stored_runs = self.store.runs(task.task_id)
        if not stored_runs:
            self.store.append_event(
                task.task_id,
                "task.replanning_after_restart",
                {"reason": "interrupted_before_runs_created"},
            )
            return await self._run_task(
                task,
                context=context,
                selected_profile=selected_profile,
                tools=tools,
                execute_tool=execute_tool,
                parent_trace=parent_trace,
                progress=progress,
                hooks=hooks,
            )
        interrupted_ids = _interrupted_run_ids(self.store.checkpoints(task.task_id))
        for run in stored_runs:
            if run.run_id not in interrupted_ids or self.store.run_retry_safe(run.run_id):
                continue
            error = "进程中断前已发生不可安全重复的副作用，结果未知，已阻止自动续跑。"
            result = {
                "status": "failed",
                "summary": "",
                "warnings": [error],
                "unresolved": [run.objective],
                "metadata": {
                    "failure_kind": "outcome_unknown",
                    "retryable": False,
                },
            }
            self.store.finish_run(run.run_id, "failed", result=result, error=error)
            self.store.append_event(
                task.task_id,
                "run.resume_blocked",
                {"run_id": run.run_id, "reason": "non_idempotent_side_effect"},
                run_id=run.run_id,
            )
        stored_runs = self.store.runs(task.task_id)
        steps = [_step_from_run(run) for run in stored_runs]
        runs = {run.step_key: run for run in stored_runs}
        completed = {
            run.step_key: _outcome_from_run(task, run)
            for run in stored_runs
            if run.status in {"succeeded", "partial", "failed", "skipped", "cancelled"}
        }
        tools_by_name = {_tool_name(tool): tool for tool in tools if _tool_name(tool)}
        mode = str(task.plan.get("mode") or "workflow")
        if mode == "delegate":
            run = stored_runs[0]
            step = steps[0]
            outcome = completed.get(step.key)
            if outcome is None:
                outcome = await self._run_step_reliably(
                    task,
                    step,
                    run,
                    context=context,
                    upstream={},
                    selected_profile=selected_profile,
                    tools_by_name=tools_by_name,
                    execute_tool=execute_tool,
                    hooks=hooks,
                )
            deliveries = await self._deliver_requested_artifacts(
                task,
                {step.key: outcome},
                execute_tool=execute_tool,
                delivered_artifacts=_delivered_artifact_keys(self.store.checkpoints(task.task_id)),
                progress=progress,
            )
            delivery_failed = any(not bool(item.get("ok")) for item in deliveries)
            if outcome.state == "failed":
                status = "failed"
            elif outcome.state == "partial" or delivery_failed:
                status = "partial"
            else:
                status = "completed"
            result = {
                "mode": "delegate",
                "role": step.role,
                "agent": run.handle,
                "result": outcome.result,
                "deliveries": deliveries,
            }
            self.store.set_task_state(task.task_id, status, result=result, error=outcome.error)
            if status == "completed":
                return f"{task.handle} 已从检查点恢复并完成。"
            if status == "partial":
                return f"{task.handle} 已从检查点恢复，但只完成了一部分。"
            return f"{task.handle} 未能安全恢复：{outcome.error or '步骤失败'}"

        return await self._execute_workflow(
            task,
            steps=steps,
            runs=runs,
            context=context,
            selected_profile=selected_profile,
            tools_by_name=tools_by_name,
            execute_tool=execute_tool,
            parent_trace=parent_trace,
            progress=progress,
            initial_completed=completed,
            hooks=hooks,
        )

    async def _execute_workflow(
        self,
        task: TaskRecord,
        *,
        steps: Sequence[TaskStep],
        runs: Mapping[str, RunRecord],
        context: ContextPacket,
        selected_profile: ModelProfile,
        tools_by_name: Mapping[str, ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None,
        progress: ProgressCallback | None,
        initial_completed: Mapping[str, StepOutcome] | None = None,
        hooks: AgentExecutionHooks | None = None,
    ) -> str:
        completed: dict[str, StepOutcome] = dict(initial_completed or {})
        _apply_completed_repairs(completed)
        pending = {step.key: step for step in steps}
        for key in completed:
            pending.pop(key, None)
        delivered_artifacts = _delivered_artifact_keys(
            self.store.checkpoints(task.task_id)
        )
        adaptive_repairs_used = sum(
            1
            for item in self.store.checkpoints(task.task_id)
            if str(item.get("phase") or "") == "adaptive_repair_planned"
        )

        async def tracked_execute_tool(
            name: str,
            arguments: dict[str, object],
        ) -> str:
            raw_result = await execute_tool(name, arguments)
            if name == "send_file_from_sandbox" and _tool_result_ok(raw_result):
                key = _artifact_key_from_arguments(arguments)
                if key is not None:
                    delivered_artifacts.add(key)
            return raw_result

        while pending:
            if self.store.cancellation_requested(task.task_id):
                raise asyncio.CancelledError
            settled = [
                step
                for step in pending.values()
                if all(dependency in completed for dependency in step.dependencies)
            ]
            blocked = [
                step
                for step in settled
                if any(not completed[dependency].usable for dependency in step.dependencies)
            ]
            for step in blocked:
                outcome = self._skip_step(task, step, runs[step.key], completed)
                completed[step.key] = outcome
                pending.pop(step.key, None)
                await self._notify_progress(
                    progress,
                    f"{task.handle} · {self.registry.worker(step.role).title} Agent："
                    "因上游步骤失败，已跳过。",
                )

            ready = [step for step in settled if step not in blocked]
            if not ready and not blocked:
                raise RuntimeError("任务依赖图无法继续执行")
            ready = ready[: self.max_parallelism]
            for step in ready:
                await self._notify_progress(
                    progress,
                    f"{task.handle} · {self.registry.worker(step.role).title} Agent："
                    f"{step.objective[:120]}",
                )
            if not ready:
                continue
            outcomes = await asyncio.gather(
                *(
                    self._run_step_reliably(
                        task,
                        step,
                        runs[step.key],
                        context=context,
                        upstream={
                            dependency: completed[dependency].result
                            for dependency in step.dependencies
                        },
                        selected_profile=selected_profile,
                        tools_by_name=tools_by_name,
                        execute_tool=tracked_execute_tool,
                        hooks=hooks,
                    )
                    for step in ready
                )
            )
            for outcome in outcomes:
                completed[outcome.step.key] = outcome
                repaired_step = _repair_target(outcome.step.key)
                if repaired_step and outcome.usable:
                    completed[repaired_step] = outcome
                pending.pop(outcome.step.key, None)
                _merge_trace(parent_trace, outcome.trace)
            for failed in [item for item in outcomes if item.state == "failed"]:
                if adaptive_repairs_used >= self.max_adaptive_repairs:
                    break
                attempted, repair = await self._attempt_adaptive_repair(
                    task,
                    failed,
                    context=context,
                    completed=completed,
                    selected_profile=selected_profile,
                    tools_by_name=tools_by_name,
                    execute_tool=tracked_execute_tool,
                    parent_trace=parent_trace,
                    progress=progress,
                    hooks=hooks,
                    repair_number=adaptive_repairs_used + 1,
                )
                if attempted:
                    adaptive_repairs_used += 1
                if repair is not None:
                    completed[repair.step.key] = repair
                    if repair.usable:
                        completed[failed.step.key] = repair
            self.store.append_checkpoint(
                task.task_id,
                "step_batch_completed",
                {
                    "completed": {
                        key: {
                            "role": item.step.role,
                            "status": item.state,
                            "result": item.result,
                            "error": item.error,
                        }
                        for key, item in completed.items()
                    },
                    "pending": sorted(pending),
                },
            )
            finished = "、".join(
                f"{self.registry.worker(item.step.role).title}{_outcome_progress_label(item)}"
                for item in outcomes
            )
            await self._notify_progress(
                progress,
                f"{task.handle} 进度：{finished}。",
            )

        delivery_results = await self._deliver_requested_artifacts(
            task,
            completed,
            execute_tool=tracked_execute_tool,
            delivered_artifacts=delivered_artifacts,
            progress=progress,
        )

        self.store.set_task_state(task.task_id, "verifying")
        await self._notify_progress(
            progress,
            f"{task.handle} · 主控 Agent：正在核对各 Agent 的结果并整理最终答复。",
        )
        final_trace = DeepSeekTrace(trace_id=task.trace_id)
        final_profile = self._profile_for("supervisor", selected_profile)
        final_input = _synthesis_input(task.objective, completed)
        final_text = await ask_deepseek(
            final_input,
            [],
            profile=final_profile,
            tool_context=(
                "你是 Sub-Agent 主控。检查各步骤是否真正完成原始目标，再给用户一个"
                "直接、自然的最终答复。明确说明失败和未解决事项；不要暴露内部 JSON，"
                "不要声称没有证据的工作已经完成。"
            ),
            trace=final_trace,
        )
        _merge_trace(parent_trace, final_trace)
        degraded = [item for item in completed.values() if not item.succeeded]
        delivery_failed = any(not bool(item.get("ok")) for item in delivery_results)
        result = {
            "answer": final_text,
            "deliveries": delivery_results,
            "steps": {
                key: {
                    "role": outcome.step.role,
                    "status": outcome.state,
                    "result": outcome.result,
                    "error": outcome.error,
                }
                for key, outcome in completed.items()
            },
        }
        status = "partial" if degraded or delivery_failed else "completed"
        self.store.set_task_state(task.task_id, status, result=result)
        self.store.append_checkpoint(task.task_id, "workflow_completed", result)
        return f"{task.handle}\n{final_text}" if final_text else f"{task.handle} 已完成。"

    async def _attempt_adaptive_repair(
        self,
        task: TaskRecord,
        failed: StepOutcome,
        *,
        context: ContextPacket,
        completed: Mapping[str, StepOutcome],
        selected_profile: ModelProfile,
        tools_by_name: Mapping[str, ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None,
        progress: ProgressCallback | None,
        hooks: AgentExecutionHooks | None,
        repair_number: int,
    ) -> tuple[bool, StepOutcome | None]:
        if not self.store.run_retry_safe(failed.run.run_id):
            self.store.append_event(
                task.task_id,
                "repair.blocked",
                {
                    "failed_step": failed.step.key,
                    "reason": "non_idempotent_side_effect",
                },
                run_id=failed.run.run_id,
            )
            return False, None
        supervisor_trace = DeepSeekTrace(trace_id=task.trace_id)
        try:
            decision = await ask_deepseek_json(
                _repair_planner_prompt(self.registry),
                _repair_planner_input(task.objective, failed),
                profile=self._profile_for("supervisor", selected_profile),
                trace=supervisor_trace,
            )
        except Exception as exc:
            self.store.append_event(
                task.task_id,
                "repair.planning_failed",
                {
                    "failed_step": failed.step.key,
                    "error": (str(exc) or exc.__class__.__name__)[:1000],
                },
                run_id=failed.run.run_id,
            )
            return False, None
        finally:
            _merge_trace(parent_trace, supervisor_trace)

        action = str(decision.get("action") or "accept_failure").strip().casefold()
        role = str(decision.get("role") or failed.step.role).strip()
        objective = str(decision.get("objective") or "").strip()
        if action != "repair" or role not in WORKER_ROLES or not objective:
            self.store.append_event(
                task.task_id,
                "repair.declined",
                {
                    "failed_step": failed.step.key,
                    "reason": str(decision.get("reason") or "no safe repair")[:1000],
                },
                run_id=failed.run.run_id,
            )
            return False, None

        repair_key = f"{failed.step.key}__repair_{repair_number}"[:80]
        repair_step = TaskStep(
            key=repair_key,
            role=role,  # type: ignore[arg-type]
            objective=objective[:4000],
            deliverable=(
                str(decision.get("deliverable") or failed.step.deliverable).strip()
                or failed.step.deliverable
            )[:1000],
            dependencies=failed.step.dependencies,
        )
        spec = self.registry.worker(role)
        profile = self._profile_for(role, selected_profile)
        run = self.store.create_run(
            task.task_id,
            repair_step,
            allowed_tools=sorted(spec.allowed_tools & tools_by_name.keys()),
            model_profile=profile.name,
        )
        current_plan = dict(self.store.get(task.task_id).plan)  # type: ignore[union-attr]
        adaptive_steps = list(current_plan.get("adaptive_steps") or [])
        adaptive_steps.append(
            {
                **_step_payload(repair_step),
                "depends_on": [failed.step.key],
                "replaces": failed.step.key,
                "reason": str(decision.get("reason") or "")[:1000],
            }
        )
        current_plan["adaptive_steps"] = adaptive_steps
        self.store.set_task_state(task.task_id, "running", plan=current_plan)
        self.store.append_checkpoint(
            task.task_id,
            "adaptive_repair_planned",
            {
                "failed_step": failed.step.key,
                "repair_step": _step_payload(repair_step),
                "repair_run_id": run.run_id,
                "reason": str(decision.get("reason") or "")[:1000],
            },
            run_id=run.run_id,
        )
        await self._notify_progress(
            progress,
            f"{task.handle} · 主控 Agent：{failed.step.key} 失败，已追加一次受限修复。",
        )
        upstream = {
            dependency: completed[dependency].result
            for dependency in repair_step.dependencies
            if dependency in completed
        }
        upstream["failed_attempt"] = failed.result
        repair = await self._run_step_reliably(
            task,
            repair_step,
            run,
            context=context,
            upstream=upstream,
            selected_profile=selected_profile,
            tools_by_name=tools_by_name,
            execute_tool=execute_tool,
            hooks=hooks,
        )
        repair.result.setdefault("metadata", {})["replaces_step"] = failed.step.key
        self.store.append_checkpoint(
            task.task_id,
            "adaptive_repair_completed",
            {
                "failed_step": failed.step.key,
                "repair_step": repair.step.key,
                "repair_run_id": repair.run.run_id,
                "status": repair.state,
            },
            run_id=repair.run.run_id,
        )
        _merge_trace(parent_trace, repair.trace)
        return True, repair

    async def _deliver_requested_artifacts(
        self,
        task: TaskRecord,
        completed: Mapping[str, StepOutcome],
        *,
        execute_tool: ToolExecutor,
        delivered_artifacts: set[tuple[str, str]],
        progress: ProgressCallback | None,
    ) -> list[dict[str, Any]]:
        if not _DELIVERY_REQUEST_PATTERN.search(task.objective):
            return []

        deliveries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for outcome in completed.values():
            artifacts = outcome.result.get("artifacts")
            if not isinstance(artifacts, list):
                continue
            for raw_artifact in artifacts:
                if not isinstance(raw_artifact, dict):
                    continue
                handle = str(raw_artifact.get("handle") or "").strip()
                parsed = _sandbox_artifact_key(handle)
                if parsed is None or parsed in seen:
                    continue
                seen.add(parsed)
                sandbox_id, path = parsed
                filename = str(raw_artifact.get("name") or "").strip()
                if parsed in delivered_artifacts:
                    payload: dict[str, Any] = {
                        "ok": True,
                        "already_delivered": True,
                        "handle": handle,
                        "filename": filename,
                    }
                else:
                    await self._notify_progress(
                        progress,
                        f"{task.handle} · 主控 Agent：正在发送交付文件"
                        f"{f' {filename}' if filename else ''}。",
                    )
                    try:
                        raw_result = await execute_tool(
                            "send_file_from_sandbox",
                            {
                                "sandbox_id": sandbox_id,
                                "path": path,
                                "filename": filename,
                            },
                        )
                        payload = _tool_result_payload(raw_result)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    payload.setdefault("handle", handle)
                    payload.setdefault("filename", filename)
                raw_artifact["delivery"] = payload
                deliveries.append(payload)
                self.store.append_checkpoint(
                    task.task_id,
                    "artifact_delivery",
                    {
                        "run_id": outcome.run.run_id,
                        "sandbox_id": sandbox_id,
                        "path": path,
                        "filename": filename,
                        "ok": bool(payload.get("ok")),
                        "already_delivered": bool(payload.get("already_delivered")),
                        "error": str(payload.get("error") or "")[:1000],
                    },
                    run_id=outcome.run.run_id,
                )

                if bool(payload.get("ok")):
                    facts = outcome.result.setdefault("facts", [])
                    if isinstance(facts, list):
                        facts.append(f"交付文件已发送：{filename or path}")
                else:
                    warnings = outcome.result.setdefault("warnings", [])
                    if isinstance(warnings, list):
                        warnings.append(
                            "交付文件发送失败："
                            + str(payload.get("error") or "未知原因")
                        )
        return deliveries

    async def _run_step(
        self,
        task: TaskRecord,
        step: TaskStep,
        run: RunRecord,
        *,
        context: ContextPacket,
        upstream: Mapping[str, Mapping[str, Any]],
        selected_profile: ModelProfile,
        tools_by_name: Mapping[str, ToolDefinition],
        execute_tool: ToolExecutor,
        hooks: AgentExecutionHooks | None = None,
    ) -> StepOutcome:
        profile = self._profile_for(step.role, selected_profile)
        spec = self.registry.worker(step.role)
        agent_context = self.store.run_context(run.run_id)
        if agent_context is None:
            agent_context = self.store.save_run_context(
                task.task_id,
                run.run_id,
                context.for_agent(spec, upstream=upstream),
            )
        self.store.start_run(run.run_id)
        allowed_tools = [
            tools_by_name[name]
            for name in sorted(spec.allowed_tools)
            if name in tools_by_name
        ]
        trace = DeepSeekTrace(trace_id=task.trace_id)

        async def record_agent_event(event: AgentLoopEvent) -> None:
            self.store.append_event(
                task.task_id,
                f"agent.{event.kind}",
                {
                    "agent_sequence": event.sequence,
                    "tool_name": event.tool_name,
                    "arguments": event.arguments,
                    "result": event.result[:8000],
                    "state": event.state,
                    "note": event.note[:4000],
                    "call_id": event.call_id,
                    "fingerprint": event.fingerprint,
                    "risk": event.risk,
                    "idempotency": event.idempotency,
                    "side_effects": list(event.side_effects),
                    "execution_mode": event.execution_mode,
                    "approval": event.approval,
                    "duration_ms": event.duration_ms,
                },
                run_id=run.run_id,
            )

        try:
            answer = await ask_deepseek_with_tools(
                _worker_input(task.objective, step, agent_context),
                [],
                allowed_tools,
                execute_tool,
                profile=profile,
                max_tool_rounds=self.max_tool_rounds,
                tool_context=_worker_prompt(spec),
                trace=trace,
                event_sink=record_agent_event,
                approval_checker=(hooks.approval_checker if hooks else None),
                handoff_tool=(hooks.handoff_tool if hooks else None),
                compensate_tool=(hooks.compensate_tool if hooks else None),
            )
            result = _parse_worker_result(answer)
            state = _worker_outcome_state(result)
            error = _worker_failure_message(result) if state == "failed" else ""
            self.store.finish_run(
                run.run_id,
                {
                    "success": "succeeded",
                    "partial": "partial",
                    "failed": "failed",
                }[state],
                result=result,
                error=error,
            )
            artifacts = result.get("artifacts")
            if isinstance(artifacts, list):
                self.store.add_artifacts(task.task_id, run.run_id, artifacts)
            return StepOutcome(
                step=step,
                run=run,
                result=result,
                trace=trace,
                state=state,
                error=error,
            )
        except asyncio.CancelledError:
            self.store.finish_run(run.run_id, "cancelled", error="任务已取消")
            raise
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            retryable = _is_retryable_worker_exception(exc)
            result = {
                "status": "failed",
                "summary": "",
                "facts": [],
                "artifacts": [],
                "citations": [],
                "warnings": [error],
                "unresolved": [step.objective],
                "confidence": 0.0,
                "metadata": {
                    "failure_kind": "exception",
                    "exception_type": exc.__class__.__name__,
                    "retryable": retryable,
                },
            }
            self.store.finish_run(
                run.run_id,
                "failed",
                result=result,
                error=error,
            )
            return StepOutcome(
                step=step,
                run=run,
                result=result,
                trace=trace,
                state="failed",
                error=error,
            )

    async def _run_step_reliably(
        self,
        task: TaskRecord,
        step: TaskStep,
        run: RunRecord,
        *,
        context: ContextPacket,
        upstream: Mapping[str, Mapping[str, Any]],
        selected_profile: ModelProfile,
        tools_by_name: Mapping[str, ToolDefinition],
        execute_tool: ToolExecutor,
        hooks: AgentExecutionHooks | None = None,
    ) -> StepOutcome:
        spec = self.registry.worker(step.role)
        while True:
            outcome = await self._run_step(
                task,
                step,
                run,
                context=context,
                upstream=upstream,
                selected_profile=selected_profile,
                tools_by_name=tools_by_name,
                execute_tool=execute_tool,
                hooks=hooks,
            )
            if outcome.usable or not _retryable_outcome(outcome):
                return outcome
            if not self.store.run_retry_safe(run.run_id):
                outcome.result.setdefault("warnings", []).append(
                    "该 Agent 已产生不可安全重复的副作用，已停止自动重试。"
                )
                self.store.append_event(
                    task.task_id,
                    "run.retry_blocked",
                    {"run_id": run.run_id, "reason": "non_idempotent_side_effect"},
                    run_id=run.run_id,
                )
                return outcome
            current = next(
                (item for item in self.store.runs(task.task_id) if item.run_id == run.run_id),
                run,
            )
            if not self.store.prepare_run_retry(
                run.run_id,
                max_attempts=spec.max_attempts,
            ):
                return outcome
            self.store.append_checkpoint(
                task.task_id,
                "run_retry",
                {
                    "run_id": run.run_id,
                    "role": step.role,
                    "next_attempt": current.attempt + 1,
                    "reason": outcome.error[:1000],
                },
                run_id=run.run_id,
            )
            await asyncio.sleep(min(2 ** max(current.attempt - 1, 0), 4))

    def _skip_step(
        self,
        task: TaskRecord,
        step: TaskStep,
        run: RunRecord,
        completed: Mapping[str, StepOutcome],
    ) -> StepOutcome:
        failed_dependencies = [
            dependency
            for dependency in step.dependencies
            if dependency in completed and not completed[dependency].usable
        ]
        error = "上游步骤未成功：" + "、".join(failed_dependencies)
        result = {
            "status": "skipped",
            "summary": "",
            "facts": [],
            "artifacts": [],
            "citations": [],
            "warnings": [error],
            "unresolved": [step.objective],
            "confidence": 0.0,
        }
        self.store.finish_run(run.run_id, "skipped", result=result, error=error)
        return StepOutcome(
            step=step,
            run=run,
            result=result,
            trace=DeepSeekTrace(trace_id=task.trace_id),
            state="skipped",
            error=error,
        )

    async def _notify_progress(
        self,
        progress: ProgressCallback | None,
        message: str,
    ) -> None:
        if progress is None:
            return
        try:
            await progress(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Sub-Agent progress update failed: %s", exc)

    def _profile_for(self, role: str, default: ModelProfile) -> ModelProfile:
        requested = str(self.profile_overrides.get(role) or "").strip()
        if not requested:
            return default
        try:
            return self.model_catalog.resolve(requested)
        except ValueError:
            self.logger.warning(
                "Unknown Sub-Agent model profile %s for role %s; using %s",
                requested,
                role,
                default.name,
            )
            return default


def parse_profile_overrides(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("AI_SUBAGENT_PROFILES_JSON must be a JSON object")
    result: dict[str, str] = {}
    for role, profile in payload.items():
        clean_role = str(role).strip()
        clean_profile = str(profile).strip()
        if clean_role not in AGENT_SPECS:
            raise ValueError(f"Unknown Sub-Agent role: {clean_role}")
        if clean_profile:
            result[clean_role] = clean_profile
    return result


def _planner_prompt(
    max_steps: int,
    registry: AgentRegistry = DEFAULT_AGENT_REGISTRY,
) -> str:
    roles = "\n".join(
        f"- {role}: {registry.worker(role).description}"
        for role in registry.worker_roles
    )
    return f"""你是 Kennethbot 的任务主控。把用户目标拆成最少且足够的可执行步骤。
只允许以下固定角色：
{roles}

规则：
1. 最多 {max_steps} 步，不要为了展示多 Agent 强行拆分。
2. 能由一个角色完成就只创建一步。
3. dependencies 只能引用本计划里的步骤 id，形成无环图。
4. 每步必须有可验证的 objective 和 deliverable。
5. supervisor 不得作为执行步骤。

输出 JSON：
{{"goal":"...","steps":[{{"id":"step_id","agent":"researcher","depends_on":[],"objective":"...","deliverable":"..."}}]}}"""


def _planner_input(objective: str, context: ContextPacket) -> str:
    return (
        f"[用户目标]\n{objective}\n\n"
        f"[宿主筛选的任务上下文]\n{context.render_for_planner()}"
    )


def _validate_plan(payload: Mapping[str, Any], objective: str, max_steps: int) -> list[TaskStep]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return [_fallback_step(objective)]
    steps: list[TaskStep] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps[:max_steps], start=1):
        if not isinstance(raw, Mapping):
            continue
        key = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw.get("id") or f"step_{index}"))[:80]
        role = str(raw.get("agent") or raw.get("role") or "").strip()
        step_objective = str(raw.get("objective") or "").strip()
        if not key or key in seen or role not in WORKER_ROLES or not step_objective:
            continue
        dependencies = tuple(
            str(item).strip()
            for item in raw.get("depends_on", [])
            if str(item).strip()
        ) if isinstance(raw.get("depends_on", []), list) else ()
        steps.append(
            TaskStep(
                key=key,
                role=role,  # type: ignore[arg-type]
                objective=step_objective[:4000],
                deliverable=str(raw.get("deliverable") or "可验证的任务结果")[:1000],
                dependencies=dependencies,
            )
        )
        seen.add(key)
    if not steps:
        return [_fallback_step(objective)]
    keys = {step.key for step in steps}
    if any(dependency not in keys for step in steps for dependency in step.dependencies):
        raise RuntimeError("任务计划引用了不存在的依赖步骤")
    _assert_acyclic(steps)
    return steps


def _assert_acyclic(steps: Sequence[TaskStep]) -> None:
    remaining = {step.key: set(step.dependencies) for step in steps}
    resolved: set[str] = set()
    while remaining:
        ready = [key for key, deps in remaining.items() if deps <= resolved]
        if not ready:
            raise RuntimeError("任务计划包含循环依赖")
        for key in ready:
            resolved.add(key)
            remaining.pop(key)


def _fallback_step(objective: str) -> TaskStep:
    lowered = objective.lower()
    if re.search(r"视频|图片|截图|语音|b站|小红书|抖音", lowered):
        role: SubAgentRole = "media"
    elif re.search(r"代码|项目|程序|脚本|部署|编译|测试", lowered):
        role = "coder"
    elif re.search(r"pdf|文件|文档|表格|试卷", lowered):
        role = "document"
    elif re.search(r"告警|服务器|数据库|服务状态|运维", lowered):
        role = "operator"
    elif re.search(r"统计|比较|分析|排名|数据", lowered):
        role = "analyst"
    else:
        role = "researcher"
    return TaskStep(
        key="main",
        role=role,
        objective=objective,
        deliverable="完成用户目标并给出可验证结果",
    )


def _worker_prompt(spec: AgentSpec) -> str:
    return f"""[Sub-Agent 角色]
你是 {spec.title} Agent。{spec.description}
{spec.instructions}

你只处理分配给你的步骤，不重新规划整个任务，也不能创建其他 Agent。
工具执行结果是事实来源；工具失败时如实记录。完成后只输出一个 JSON 对象：
{{
  "status": "success 或 partial",
  "summary": "完成了什么",
  "facts": ["关键事实"],
  "artifacts": [{{"handle":"工具返回的完整句柄","kind":"file","name":"名称"}}],
  "citations": ["完整来源链接或消息句柄"],
  "warnings": ["限制和风险"],
  "unresolved": ["尚未解决的问题"],
  "confidence": 0.0
}}
不要使用 Markdown 代码围栏包裹 JSON。"""


def _worker_input(
    goal: str,
    step: TaskStep,
    context: AgentContext,
) -> str:
    return (
        f"[总目标]\n{goal}\n\n"
        f"[你的步骤]\n{step.objective}\n\n"
        f"[交付标准]\n{step.deliverable}\n\n"
        f"[你的独立上下文快照]\n{context.rendered_context}\n\n"
        f"[上下文版本]\nagent-v{context.agent_definition_version} "
        f"sha256:{context.context_hash}"
    )


def _repair_planner_prompt(registry: AgentRegistry) -> str:
    roles = "\n".join(
        f"- {role}: {registry.worker(role).description}"
        for role in registry.worker_roles
    )
    return f"""你是 Kennethbot 的故障恢复主控。只有原步骤失败后才会调用你。
判断是否值得追加一次有明确边界的修复步骤。不要重画整个计划，不要重复已完成工作，
也不要为了看起来积极而盲目重试。可用角色：
{roles}

输出 JSON：
{{"action":"repair 或 accept_failure","role":"researcher","objective":"可独立验收的修复目标","deliverable":"交付标准","reason":"原因"}}"""


def _repair_planner_input(goal: str, failed: StepOutcome) -> str:
    return (
        f"[原始目标]\n{goal}\n\n"
        f"[失败步骤]\n{_json_dump(_step_payload(failed.step))}\n\n"
        f"[失败结果]\n{_json_dump(failed.result)}\n\n"
        f"[错误]\n{failed.error}"
    )


def _repair_target(step_key: str) -> str | None:
    match = re.fullmatch(r"(.+)__repair_[1-9][0-9]*", step_key)
    return match.group(1) if match else None


def _apply_completed_repairs(completed: dict[str, StepOutcome]) -> None:
    for key, outcome in tuple(completed.items()):
        target = _repair_target(key)
        if target and outcome.usable:
            completed[target] = outcome


def _checkpoint_context_packet(
    checkpoints: Sequence[Mapping[str, Any]],
) -> ContextPacket | None:
    for checkpoint in reversed(checkpoints):
        state = checkpoint.get("state")
        if not isinstance(state, Mapping):
            continue
        payload = state.get("context_packet")
        if isinstance(payload, Mapping):
            return ContextPacket.from_payload(payload)
    return None


def _delivered_artifact_keys(
    checkpoints: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    delivered: set[tuple[str, str]] = set()
    for checkpoint in checkpoints:
        if str(checkpoint.get("phase") or "") != "artifact_delivery":
            continue
        state = checkpoint.get("state")
        if not isinstance(state, Mapping) or not bool(state.get("ok")):
            continue
        sandbox_id = str(state.get("sandbox_id") or "").strip()
        path = str(state.get("path") or "").strip()
        if sandbox_id and path:
            delivered.add((sandbox_id, path))
    return delivered


def _interrupted_run_ids(
    checkpoints: Sequence[Mapping[str, Any]],
) -> set[int]:
    for checkpoint in reversed(checkpoints):
        if str(checkpoint.get("phase") or "") != "process_interrupted":
            continue
        state = checkpoint.get("state")
        if not isinstance(state, Mapping):
            return set()
        raw = state.get("interrupted_runs")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return set()
        return {
            int(item)
            for item in raw
            if str(item).strip().isdigit() and int(item) > 0
        }
    return set()


def _step_from_run(run: RunRecord) -> TaskStep:
    return TaskStep(
        key=run.step_key,
        role=run.role,  # type: ignore[arg-type]
        objective=run.objective,
        deliverable=run.deliverable,
        dependencies=run.dependencies,
    )


def _outcome_from_run(task: TaskRecord, run: RunRecord) -> StepOutcome:
    states: dict[str, WorkerOutcomeState] = {
        "succeeded": "success",
        "partial": "partial",
        "failed": "failed",
        "cancelled": "failed",
        "skipped": "skipped",
    }
    return StepOutcome(
        step=_step_from_run(run),
        run=run,
        result=dict(run.result),
        trace=DeepSeekTrace(trace_id=task.trace_id),
        state=states.get(run.status, "failed"),
        error=run.last_error,
    )


def _parse_worker_result(answer: str) -> dict[str, Any]:
    return AgentResult.parse(answer).as_payload()


def _normalize_worker_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    return AgentResult.from_payload(payload).as_payload()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _worker_outcome_state(result: Mapping[str, Any]) -> WorkerReportedState:
    status = str(result.get("status") or "success").strip().casefold()
    if status == "partial":
        return "partial"
    if status in {"failed", "failure", "error", "cancelled", "skipped"}:
        return "failed"
    return "success"


def _retryable_outcome(outcome: StepOutcome) -> bool:
    metadata = outcome.result.get("metadata")
    return bool(
        outcome.state == "failed"
        and isinstance(metadata, Mapping)
        and metadata.get("retryable") is True
    )


def _is_retryable_worker_exception(exc: BaseException) -> bool:
    if isinstance(exc, (DeepSeekConfigError, ValueError, PermissionError)):
        return False
    if isinstance(
        exc,
        (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError),
    ):
        return True
    normalized = str(exc).casefold()
    return any(
        marker in normalized
        for marker in (
            "timeout",
            "timed out",
            "temporar",
            "connection",
            "network",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
            "模型",
            "provider",
        )
    )


def _worker_failure_message(result: Mapping[str, Any]) -> str:
    for key in ("summary", "unresolved", "warnings"):
        value = result.get(key)
        if isinstance(value, list) and value:
            return str(value[0])[:4000]
        if isinstance(value, str) and value.strip():
            return value.strip()[:4000]
    return "Sub-Agent 报告执行失败"


def _outcome_progress_label(outcome: StepOutcome) -> str:
    return {
        "success": "完成",
        "partial": "部分完成",
        "failed": "失败",
        "skipped": "跳过",
    }[outcome.state]


def _sandbox_artifact_key(handle: str) -> tuple[str, str] | None:
    matched = _SANDBOX_ARTIFACT_HANDLE_PATTERN.fullmatch(handle.strip())
    if matched is None:
        return None
    return matched.group(1), matched.group(2)


def _artifact_key_from_arguments(
    arguments: Mapping[str, object],
) -> tuple[str, str] | None:
    sandbox_id = str(arguments.get("sandbox_id") or "").strip()
    path = str(arguments.get("path") or "").strip()
    if not path.startswith("/workspace/"):
        path = "/workspace/" + path.lstrip("/")
    return _sandbox_artifact_key(f"{sandbox_id}:{path}")


def _tool_result_payload(raw_result: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": str(raw_result)[:500] or "工具没有返回结果"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "工具返回格式无效"}
    return dict(payload)


def _tool_result_ok(raw_result: str) -> bool:
    return bool(_tool_result_payload(raw_result).get("ok"))


def _synthesis_input(objective: str, completed: Mapping[str, StepOutcome]) -> str:
    payload = {
        key: {
            "role": outcome.step.role,
            "objective": outcome.step.objective,
            "deliverable": outcome.step.deliverable,
            "status": outcome.state,
            "result": outcome.result,
            "error": outcome.error,
        }
        for key, outcome in completed.items()
    }
    return f"[原始目标]\n{objective}\n\n[各 Agent 结果]\n{_json_dump(payload)}"


def _step_payload(step: TaskStep) -> dict[str, object]:
    return {
        "id": step.key,
        "agent": step.role,
        "depends_on": list(step.dependencies),
        "objective": step.objective,
        "deliverable": step.deliverable,
    }


def _tool_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function")
    return str(function.get("name") or "") if isinstance(function, Mapping) else ""


def _merge_trace(parent: DeepSeekTrace | None, child: DeepSeekTrace) -> None:
    if parent is None:
        return
    parent.input_tokens += child.input_tokens
    parent.output_tokens += child.output_tokens
    parent.total_tokens += child.total_tokens
    parent.model_routes.extend(child.model_routes)
    parent.messages.extend(child.messages)


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_list(value: object) -> list[str]:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []
