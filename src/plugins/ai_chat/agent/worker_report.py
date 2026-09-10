"""Bounded report correction without granting a second execution pass."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .evidence import evidence_index
from .outcomes import successful_evidence, validate_report


def separate_cluster_artifacts(result: dict, evidence: list[dict]) -> dict:
    """Keep proved cluster uploads as references, not QQ file attachments."""
    result = deepcopy(result)
    uploads = {}
    for item in evidence:
        payload = item["payload"]
        if (item["tool_name"] == "cluster_artifact_upload" and successful_evidence(item)
                and re.fullmatch(r"artifact_[a-f0-9]{32}", str(payload.get("artifact_id", "")))
                and payload.get("status") == "staged"
                and re.fullmatch(r"[a-f0-9]{64}", str(payload.get("sha256", "")))):
            uploads[payload["artifact_id"]] = item
    files, external = [], []
    for artifact in result.get("artifacts", []):
        source = uploads.get(artifact.get("handle"))
        if source is None:
            files.append(artifact)
            continue
        payload = source["payload"]
        external.append({"handle": payload["artifact_id"], "kind": "cluster_artifact",
                         "name": payload.get("name", ""), "sha256": payload["sha256"],
                         "size": payload.get("size_bytes"), "evidence_ref": source["evidence_id"],
                         "delivery": "not_a_qq_receipt"})
    result["artifacts"] = files
    # This field is host-owned; never retain model-authored proof metadata.
    result.setdefault("metadata", {}).pop("cluster_artifact_refs", None)
    if external:
        result["metadata"]["cluster_artifact_refs"] = external
    return result


def checked_report(result: dict, evidence: list[dict], *, required: bool) -> dict:
    return validate_report(deepcopy(result), evidence, required=required)


def report_refs(value) -> set[str]:
    if isinstance(value, dict):
        refs = value.get("evidence_refs", [])
        found = {ref for ref in refs if isinstance(ref, str)} if isinstance(refs, list) else set()
        for item in value.values():
            found.update(report_refs(item))
        return found
    if isinstance(value, list):
        return set().union(*(report_refs(item) for item in value))
    return set()


def checked_correction(result: dict, evidence: list[dict], read_refs: set[str], *, required: bool) -> dict:
    checked = checked_report(result, evidence, required=required)
    missing = report_refs(result) - read_refs
    if missing:
        error = "纠错未完整读取引用的证据：" + ", ".join(sorted(missing))
        checked["report_validation"]["status"] = "incomplete"
        checked["report_validation"]["errors"].append(error)
        checked.setdefault("warnings", []).append(error)
        if checked.get("status") == "success":
            checked["status"] = "partial"
    return checked


def correction_input(original: dict, evidence: list[dict], errors: list[str]) -> str:
    return (
        "[只读交付纠错]\n你已经执行完本步骤。只纠正下面报告的字段和证据引用，不重新执行任务。"
        "只能读取本轮宿主证据；报告引用的每条证据都必须调用 read_task_evidence 从 offset=0 读完全部分页，"
        "再修正编号、对象和观测时间。只看索引、猜测编号不能通过纠错。"
        "历史上下文的 evidence# 不能直接沿用。recorded_at 是收取时间，不一定是来源采样时间。"
        "保留真实未完成项、失败和已生成文件；不能通过清空结论或删除文件来通过校验。"
        "不能新建文件、修改文件、执行命令、发送或申请授权。文件正文错误必须留给后续修复步骤。"
        "最终按原交付结构输出完整 JSON，不编造证据。\n"
        + json.dumps({"errors": errors, "original_report": original,
                      "allowed_evidence": evidence_index(evidence)}, ensure_ascii=False)
    )


def retain_execution_facts(original: dict, corrected: dict) -> dict:
    """A read-only edit cannot create/remove files or resolve unfinished execution."""
    corrected = deepcopy(corrected)
    corrected["artifacts"] = deepcopy(original.get("artifacts", []))
    corrected.setdefault("metadata", {}).pop("cluster_artifact_refs", None)
    external = original.get("metadata", {}).get("cluster_artifact_refs")
    if external:
        corrected["metadata"]["cluster_artifact_refs"] = deepcopy(external)
    for key in ("warnings", "unresolved", "handoff"):
        corrected[key] = list(dict.fromkeys([*original.get(key, []), *corrected.get(key, [])]))
    if original.get("status") in {"partial", "failed"}:
        corrected["status"] = original["status"]
    if corrected.get("unresolved") and corrected.get("status") == "success":
        corrected["status"] = "partial"
    # Refusing unsupported conclusions is fine, silently dropping all of them is not.
    for field in ("findings", "completed"):
        if original.get(field) and not corrected.get(field) and not corrected.get("unresolved"):
            corrected.setdefault("_report_missing_fields", []).append(field)
    return corrected
