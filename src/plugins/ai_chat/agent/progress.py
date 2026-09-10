"""Read model for the complete task lifecycle, separate from execution state."""
from __future__ import annotations

from typing import Any

from .evidence import evidence_index, redact


def task_progress(task: Any, runs: list, evidence: list[dict], external: list[dict],
                  deliveries: list[dict], final: Any, *, revision: int) -> dict:
    contract = task.plan.get("contract", {})
    validation = task.result.get("validation", {}).get("acceptance", {})
    matrix = validation.get("task_outcome", {}) if isinstance(validation, dict) else {}
    findings, finished, next_verification, authorization = [], [], [], []
    for run in runs:
        if run.step_key.startswith("acceptance_r"):
            continue
        for key, target in (("findings", findings), ("completed", finished),
                            ("authorization", authorization), ("next_verification", next_verification)):
            for value in run.result.get(key, []):
                target.append({"run_id": run.run_id, "step": run.step_key, "value": value})
    operations = []
    waiting_approval = False
    approval_confirmed = False
    for item in external:
        response = item.get("response") or {}
        record = response.get("operation", response)
        waiting_approval |= record.get("status") == "awaiting_approval"
        approval_confirmed |= bool(record.get("approval_ref"))
        result = record.get("result") or {}
        request = item.get("request") or {}
        operations.append({"run_id": item["run_id"], "call_id": item["call_id"],
            "remote_path": item.get("remote_path"), "status": record.get("status", item["status"]),
            "host_id": record.get("host_id"), "updated_at": item["updated_at"],
            "arguments": redact(record.get("arguments") or request.get("tool_arguments") or {}),
            "summary": result.get("summary", ""), "verification": result.get("verification"),
            "error": record.get("error_code") or record.get("error") or ""})
    terminal = task.status in {"completed", "partial", "failed", "cancelled"}
    active = task.status in {"running", "planning", "verifying", "waiting_external"}
    file_rows = [item for item in deliveries if item["revision"] == revision]
    files_confirmed = not contract.get("delivery_required") or bool(file_rows) and all(item["state"] == "acknowledged" for item in file_rows)
    final_state = final.status if final else "pending"
    delivered = files_confirmed and final_state == "committed"
    inspected = [row for row in matrix.get("criteria", []) if row.get("kind") == "host_inspection"]
    inspection_complete = bool(inspected) and all(row["status"] == "passed" for row in inspected)
    stages = [
        {"key": "inspection", "label": "检查", "status": "completed" if inspection_complete else "running" if active else "unverified" if terminal else "pending",
         "detail": f"{len(evidence)} 条宿主工具证据"},
        {"key": "findings", "label": "发现问题", "status": "completed" if terminal or findings else "running" if active else "pending",
         "detail": f"{len(findings)} 条发现，{len(next_verification)} 项待补查"},
        {"key": "authorization", "label": "授权", "status": "waiting" if waiting_approval else "completed" if approval_confirmed else "unverified" if operations else "not_required",
         "detail": "等待本任务授权" if waiting_approval else "已记录批准凭据" if approval_confirmed else "操作记录未提供批准凭据" if operations else "未发生需授权的服务器操作"},
        {"key": "execution", "label": "执行", "status": task.result.get("execution_state") or ("completed" if task.status == "completed" else task.status),
         "detail": f"{len(finished)} 项已记录的完成工作"},
        {"key": "verification", "label": "复查", "status": matrix.get("status") or ("running" if task.status == "verifying" else "unverified" if terminal else "pending"),
         "detail": f"{sum(row.get('status') == 'passed' for row in matrix.get('criteria', []))}/{len(matrix.get('criteria', []))} 项通过"},
        {"key": "delivery", "label": "结果送达", "status": "committed" if delivered else "partial" if final_state == "committed" else final_state,
         "detail": "文字和要求的文件已确认送达" if delivered else "分别核对最终文字与文件回执"},
    ]
    return {"revision": revision, "stages": stages, "acceptance": matrix,
        "findings": findings, "completed": finished, "authorization": authorization,
        "next_verification": next_verification, "operations": operations,
        "evidence": evidence_index(evidence), "execution_status": task.status,
        "delivery_status": stages[-1]["status"], "files_confirmed": files_confirmed,
        "updated_at": max([task.updated_at, *(item["updated_at"] for item in external),
                           *(item["updated_at"] for item in file_rows), final.updated_at if final else 0])}
