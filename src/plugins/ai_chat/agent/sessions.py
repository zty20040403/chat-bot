from __future__ import annotations

import json
import time
from typing import Any


class AgentSessionStoreMixin:
    """A session belongs to one task step, not to a reusable role template."""

    def agent_session(self, task_id: int, run_id: int, *, scope_key: str, requester_user_id: int) -> dict[str, Any]:
        with self._lock:
            owner = self._connection.execute(
                """SELECT r.run_id FROM subagent_runs r JOIN subagent_tasks t ON t.task_id = r.task_id
                   WHERE r.run_id = ? AND t.task_id = ? AND t.scope_key = ? AND t.requester_user_id = ?""",
                (run_id, task_id, scope_key, requester_user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("Agent session is outside the current task/owner scope")
            row = self._connection.execute(
                "SELECT version, transcript_json, model_profile FROM subagent_sessions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return {"version": 0, "messages": [], "model_profile": ""}
        return {"version": int(row["version"]), "messages": json.loads(row["transcript_json"]),
                "model_profile": str(row["model_profile"])}

    def save_agent_session(
        self, task_id: int, run_id: int, messages: list[dict[str, Any]], *,
        scope_key: str, requester_user_id: int, model_profile: str, expected_version: int,
    ) -> int:
        self.agent_session(task_id, run_id, scope_key=scope_key, requester_user_id=requester_user_id)
        if any(item.get("role") not in {"user", "assistant", "tool"} for item in messages):
            raise ValueError("Agent transcripts must not persist host system instructions")
        payload = json.dumps(messages, ensure_ascii=False)
        with self._transaction() as cursor:
            covered = int(cursor.execute("SELECT COALESCE(MAX(sequence),0) AS sequence FROM subagent_events WHERE run_id=?", (run_id,)).fetchone()["sequence"])
            if expected_version == 0:
                cursor.execute(
                    """INSERT INTO subagent_sessions (run_id, task_id, version, transcript_json, model_profile, updated_at, covered_sequence)
                       VALUES (?, ?, 1, ?, ?, ?, ?) ON CONFLICT(run_id) DO NOTHING""",
                    (run_id, task_id, payload, model_profile, int(time.time()), covered),
                )
            else:
                cursor.execute(
                    """UPDATE subagent_sessions SET version = version + 1, transcript_json = ?,
                       model_profile = ?, updated_at = ?, covered_sequence = ? WHERE run_id = ? AND task_id = ? AND version = ?""",
                    (payload, model_profile, int(time.time()), covered, run_id, task_id, expected_version),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Agent session version conflict; concurrent takeover is not allowed")
        return expected_version + 1


READ_AGENT_RESULT = {
    "type": "function", "function": {
        "name": "read_agent_result",
        "description": "分页读取此步骤直接依赖的上游完整结果。只允许当前任务已交接的 step_id，不可读其他会话。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "step_id": {"type": "string"},
                "section": {"type": "string", "enum": ["summary", "facts", "artifacts", "citations", "warnings", "unresolved"]},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["step_id", "section"],
        },
    },
}


def read_upstream_result(upstream, arguments) -> str:
    key, section = str(arguments.get("step_id", "")), str(arguments.get("section", ""))
    if key not in upstream or section not in {"summary", "facts", "artifacts", "citations", "warnings", "unresolved"}:
        return json.dumps({"ok": False, "error": "No authorized upstream result or section"})
    value = upstream[key].get(section, "" if section == "summary" else [])
    offset = max(int(arguments.get("offset", 0)), 0)
    limit = min(max(int(arguments.get("limit", 5)), 1), 20)
    if isinstance(value, str):
        limit *= 200
    elif not isinstance(value, list):
        value = [value]
    selected = value[offset:offset + limit]
    return json.dumps({"ok": True, "step_id": key, "section": section, "data": selected,
                       "total": len(value), "next_offset": offset + len(selected) if offset + len(selected) < len(value) else None}, ensure_ascii=False)


def upstream_index(upstream) -> str:
    return json.dumps({key: {
        "status": result.get("status", "partial"), "summary": str(result.get("summary", ""))[:160],
        "sections": {field: len(result.get(field) or []) for field in ("facts", "artifacts", "citations", "unresolved")},
        "read_with": "read_agent_result", "step_id": key,
    } for key, result in upstream.items()}, ensure_ascii=False, separators=(",", ":"))
