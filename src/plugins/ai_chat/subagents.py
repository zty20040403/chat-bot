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

from .ai_tools import ToolDefinition
from .deepseek import (
    DeepSeekTrace,
    ask_deepseek,
    ask_deepseek_json,
    ask_deepseek_with_tools,
)
from .model_catalog import ModelCatalog, ModelProfile


SubAgentRole = Literal[
    "supervisor",
    "researcher",
    "coder",
    "document",
    "media",
    "analyst",
    "operator",
]


@dataclass(frozen=True)
class AgentSpec:
    role: SubAgentRole
    title: str
    description: str
    instructions: str
    allowed_tools: frozenset[str]


COMMON_READ_TOOLS = frozenset(
    {
        "get_message_by_id",
        "search_messages",
        "context_expand",
        "context_search",
        "memory_list",
        "inspect_source",
        "inspect_shared_content",
        "get_shared_content",
        "view_forward",
        "view_bilibili",
        "list_recent_files",
        "job_status",
        "say",
    }
)
SANDBOX_TOOLS = frozenset(
    {
        "sandbox_create",
        "sandbox_list",
        "sandbox_exec",
        "sandbox_write_file",
        "sandbox_read_file",
        "sandbox_destroy",
        "import_file_to_sandbox",
        "send_file_from_sandbox",
        "send_image_from_sandbox",
        "job_cancel",
    }
)
BROWSER_TOOLS = frozenset(
    {
        "web_search",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_wait_for",
        "browser_scroll",
        "browser_close",
        "browser_clear",
    }
)


AGENT_SPECS: dict[SubAgentRole, AgentSpec] = {
    "supervisor": AgentSpec(
        role="supervisor",
        title="主控",
        description="拆分目标、检查依赖、验收结果并统一回复。",
        instructions="只负责任务设计和验收，不亲自调用执行工具。",
        allowed_tools=frozenset(),
    ),
    "researcher": AgentSpec(
        role="researcher",
        title="搜索",
        description="搜索互联网、浏览网页并交叉核实来源。",
        instructions=(
            "优先使用一手来源；区分事实、推断和未知信息。最终给出完整链接、"
            "关键事实、冲突信息和仍未确认的内容。"
        ),
        allowed_tools=COMMON_READ_TOOLS | BROWSER_TOOLS,
    ),
    "coder": AgentSpec(
        role="coder",
        title="代码",
        description="在隔离沙盒中编写、运行和验证代码。",
        instructions=(
            "所有代码和命令必须在任务沙盒中执行。完成前检查实际输出；需要交付时"
            "发送文件，并报告执行结果和未解决问题。"
        ),
        allowed_tools=COMMON_READ_TOOLS | BROWSER_TOOLS | SANDBOX_TOOLS | {"use_skill"},
    ),
    "document": AgentSpec(
        role="document",
        title="文件",
        description="读取群文件、PDF、表格和文档并生成交付物。",
        instructions=(
            "先取得真实文件，再解析内容；不得根据文件名猜测。生成文档后检查文件"
            "存在且可读取，并通过文件句柄交付。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | SANDBOX_TOOLS
            | {"read_image_text", "view_image", "use_skill"}
        ),
    ),
    "media": AgentSpec(
        role="media",
        title="媒体",
        description="理解图片、视频、字幕、语音和平台分享内容。",
        instructions=(
            "必须先实际读取媒体再评价。长视频先看元数据、字幕和关键帧；明确指出"
            "可观察内容、推断内容和无法确认的部分。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | BROWSER_TOOLS
            | {
                "read_image_text",
                "view_image",
                "view_video",
                "transcribe_voice",
            }
        ),
    ),
    "analyst": AgentSpec(
        role="analyst",
        title="分析",
        description="整理数据、比较证据、计算并形成可审计结论。",
        instructions=(
            "先确定统计口径，再计算和比较。结论必须对应证据；发现缺失数据时明确"
            "说明，不要用猜测补齐。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | SANDBOX_TOOLS
            | {"query_alerts", "pin_message", "group_members"}
        ),
    ),
    "operator": AgentSpec(
        role="operator",
        title="运维",
        description="检查 Kennethbot、告警、任务、数据库和运行状态。",
        instructions=(
            "默认只读检查。涉及停止、重启、删除或修改服务时必须遵守宿主审批策略；"
            "报告影响范围、当前状态和建议动作。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | {"query_alerts", "sandbox_list", "job_status", "group_members"}
        ),
    ),
}

WORKER_ROLES = tuple(role for role in AGENT_SPECS if role != "supervisor")


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
        if self._legacy_sqlite:
            self._configure()
            self._migrate()
        self.recovered_tasks = self.recover_interrupted()

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
            cursor.execute(
                """
                UPDATE subagent_runs
                SET status = 'running', attempt = attempt + 1,
                    started_at = ?, finished_at = NULL, last_error = ''
                WHERE run_id = ? AND status = 'pending'
                """,
                (timestamp, int(run_id)),
            )
            return cursor.rowcount == 1

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

    def request_cancel(self, task_id: int, *, now: int | None = None) -> bool:
        timestamp = int(time.time() if now is None else now)
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE subagent_tasks
                SET cancel_requested = ?, status = 'cancelling', updated_at = ?
                WHERE task_id = ? AND status IN ('received', 'planning', 'running', 'verifying')
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
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE subagent_runs SET status = 'failed',
                    last_error = 'bot restarted while this agent was running',
                    finished_at = ? WHERE status = 'running'
                """,
                (timestamp,),
            )
            cursor.execute(
                """
                UPDATE subagent_tasks SET status = 'failed',
                    last_error = 'bot restarted while this task was running',
                    updated_at = ?, finished_at = ?
                WHERE status IN ('received', 'planning', 'running', 'verifying', 'cancelling')
                """,
                (timestamp, timestamp),
            )
            return max(int(cursor.rowcount), 0)

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


@dataclass(frozen=True)
class StepOutcome:
    step: TaskStep
    run: RunRecord
    result: dict[str, Any]
    trace: DeepSeekTrace
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


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
    ) -> None:
        self.store = store
        self.model_catalog = model_catalog
        self.logger = logger
        self.max_steps = min(max(int(max_steps), 1), 12)
        self.max_parallelism = min(max(int(max_parallelism), 1), 6)
        self.max_tool_rounds = min(max(int(max_tool_rounds), 1), 12)
        self.timeout_seconds = min(max(int(timeout_seconds), 30), 3600)
        self.profile_overrides = dict(profile_overrides or {})
        self._active: dict[int, asyncio.Task[Any]] = {}

    @staticmethod
    def manifest() -> list[dict[str, object]]:
        return [
            {
                "role": spec.role,
                "title": spec.title,
                "description": spec.description,
                "allowed_tools": sorted(spec.allowed_tools),
            }
            for spec in AGENT_SPECS.values()
        ]

    def cancel(self, task_id: int) -> bool:
        changed = self.store.request_cancel(task_id)
        running = self._active.get(int(task_id))
        if running is not None and not running.done():
            running.cancel()
            return True
        return changed

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
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._run_task(
                    task,
                    context=context,
                    selected_profile=selected_profile,
                    tools=tools,
                    execute_tool=execute_tool,
                    parent_trace=parent_trace,
                    progress=progress,
                )
        except asyncio.CancelledError:
            self.store.set_task_state(task.task_id, "cancelled", error="任务已取消")
            raise
        except TimeoutError:
            message = f"任务超过 {self.timeout_seconds} 秒，已停止。"
            self.store.set_task_state(task.task_id, "failed", error=message)
            return f"{task.handle} {message}"
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.store.set_task_state(task.task_id, "failed", error=message)
            self.logger.warning("Sub-Agent task %s failed: %s", task.handle, message)
            return f"{task.handle} 执行失败：{message}"
        finally:
            self._active.pop(task.task_id, None)

    async def _run_task(
        self,
        task: TaskRecord,
        *,
        context: str,
        selected_profile: ModelProfile,
        tools: Sequence[ToolDefinition],
        execute_tool: ToolExecutor,
        parent_trace: DeepSeekTrace | None,
        progress: ProgressCallback | None,
    ) -> str:
        self.store.set_task_state(task.task_id, "planning")
        if progress is not None:
            await progress(
                f"{task.handle} · 主控 Agent：正在拆解目标、安排依赖和验收标准。"
            )
        planner_trace = DeepSeekTrace(trace_id=task.trace_id)
        planner_profile = self._profile_for("supervisor", selected_profile)
        plan_payload = await ask_deepseek_json(
            _planner_prompt(self.max_steps),
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
        runs: dict[str, RunRecord] = {}
        tools_by_name = {_tool_name(tool): tool for tool in tools if _tool_name(tool)}
        for step in steps:
            profile = self._profile_for(step.role, selected_profile)
            allowed = sorted(AGENT_SPECS[step.role].allowed_tools & tools_by_name.keys())
            runs[step.key] = self.store.create_run(
                task.task_id,
                step,
                allowed_tools=allowed,
                model_profile=profile.name,
            )
        if progress is not None:
            labels = "、".join(AGENT_SPECS[step.role].title for step in steps)
            await progress(f"{task.handle} 已拆成 {len(steps)} 步：{labels}。")

        completed: dict[str, StepOutcome] = {}
        pending = {step.key: step for step in steps}
        while pending:
            if self.store.cancellation_requested(task.task_id):
                raise asyncio.CancelledError
            ready = [
                step
                for step in pending.values()
                if all(dependency in completed for dependency in step.dependencies)
            ]
            if not ready:
                raise RuntimeError("任务依赖图无法继续执行")
            ready = ready[: self.max_parallelism]
            if progress is not None:
                for step in ready:
                    await progress(
                        f"{task.handle} · {AGENT_SPECS[step.role].title} Agent："
                        f"{step.objective[:120]}"
                    )
            outcomes = await asyncio.gather(
                *(
                    self._run_step(
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
                        execute_tool=execute_tool,
                    )
                    for step in ready
                )
            )
            for outcome in outcomes:
                completed[outcome.step.key] = outcome
                pending.pop(outcome.step.key, None)
                _merge_trace(parent_trace, outcome.trace)
            if progress is not None:
                finished = "、".join(
                    f"{AGENT_SPECS[item.step.role].title}{'完成' if item.succeeded else '失败'}"
                    for item in outcomes
                )
                await progress(f"{task.handle} 进度：{finished}。")

        self.store.set_task_state(task.task_id, "verifying")
        if progress is not None:
            await progress(
                f"{task.handle} · 主控 Agent：正在核对各 Agent 的结果并整理最终答复。"
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
        failures = [item for item in completed.values() if not item.succeeded]
        result = {
            "answer": final_text,
            "steps": {
                key: {
                    "role": outcome.step.role,
                    "status": "succeeded" if outcome.succeeded else "failed",
                    "result": outcome.result,
                    "error": outcome.error,
                }
                for key, outcome in completed.items()
            },
        }
        status = "partial" if failures else "completed"
        self.store.set_task_state(task.task_id, status, result=result)
        return f"{task.handle}\n{final_text}" if final_text else f"{task.handle} 已完成。"

    async def _run_step(
        self,
        task: TaskRecord,
        step: TaskStep,
        run: RunRecord,
        *,
        context: str,
        upstream: Mapping[str, Mapping[str, Any]],
        selected_profile: ModelProfile,
        tools_by_name: Mapping[str, ToolDefinition],
        execute_tool: ToolExecutor,
    ) -> StepOutcome:
        self.store.start_run(run.run_id)
        profile = self._profile_for(step.role, selected_profile)
        spec = AGENT_SPECS[step.role]
        allowed_tools = [
            tools_by_name[name]
            for name in sorted(spec.allowed_tools)
            if name in tools_by_name
        ]
        trace = DeepSeekTrace(trace_id=task.trace_id)
        try:
            answer = await ask_deepseek_with_tools(
                _worker_input(task.objective, step, context, upstream),
                [],
                allowed_tools,
                execute_tool,
                profile=profile,
                max_tool_rounds=self.max_tool_rounds,
                tool_context=_worker_prompt(spec),
                trace=trace,
            )
            result = _parse_worker_result(answer)
            self.store.finish_run(run.run_id, "succeeded", result=result)
            artifacts = result.get("artifacts")
            if isinstance(artifacts, list):
                self.store.add_artifacts(task.task_id, run.run_id, artifacts)
            return StepOutcome(step=step, run=run, result=result, trace=trace)
        except asyncio.CancelledError:
            self.store.finish_run(run.run_id, "cancelled", error="任务已取消")
            raise
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            self.store.finish_run(run.run_id, "failed", error=error)
            return StepOutcome(
                step=step,
                run=run,
                result={"status": "failed", "summary": "", "warnings": [error]},
                trace=trace,
                error=error,
            )

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


def _planner_prompt(max_steps: int) -> str:
    roles = "\n".join(
        f"- {role}: {AGENT_SPECS[role].description}" for role in WORKER_ROLES
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


def _planner_input(objective: str, context: str) -> str:
    return (
        f"[用户目标]\n{objective}\n\n"
        f"[必要会话上下文]\n{context[-12000:] if context else '无'}"
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
    context: str,
    upstream: Mapping[str, Mapping[str, Any]],
) -> str:
    return (
        f"[总目标]\n{goal}\n\n"
        f"[你的步骤]\n{step.objective}\n\n"
        f"[交付标准]\n{step.deliverable}\n\n"
        f"[上游结果]\n{_json_dump(upstream)}\n\n"
        f"[必要会话上下文]\n{context[-10000:] if context else '无'}"
    )


def _parse_worker_result(answer: str) -> dict[str, Any]:
    content = answer.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3].rstrip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {
            "status": "success",
            "summary": answer.strip(),
            "facts": [],
            "artifacts": [],
            "citations": [],
            "warnings": ["Agent 返回了非结构化结果，主控已保留原文。"],
            "unresolved": [],
            "confidence": 0.5,
        }
    if not isinstance(payload, dict):
        raise RuntimeError("Sub-Agent result must be a JSON object")
    return payload


def _synthesis_input(objective: str, completed: Mapping[str, StepOutcome]) -> str:
    payload = {
        key: {
            "role": outcome.step.role,
            "objective": outcome.step.objective,
            "deliverable": outcome.step.deliverable,
            "status": "succeeded" if outcome.succeeded else "failed",
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
