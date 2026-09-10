"""Bounded report correction without granting a second execution pass."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .evidence import evidence_index
from .outcomes import successful_evidence, validate_report


REPORT_CORRECTION_PROMPT = """你处于只读报告校验阶段，不是执行任务的 Agent。
只核对并纠正 original_report 的数据、字段和证据引用，保留原交付 JSON 结构。
工具只读取宿主已保存的证据；读取命令、写文件或发送回执不等于再次执行这些操作。
summary、findings、completed 和 status 仍描述原执行步骤，不是描述这次纠错。
不要因为本阶段没有重新生成文件就新增未完成项；只有发现真实内容缺陷时才记录需要修复。
保留实际未完成工作和原文件，不能编造执行结果或通过丢弃结论绕过校验。
每条引用必须完整读取；被宿主指出漏读时补读这些证据，不要重新执行任务。
最终只返回完整 JSON。"""


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
        "只能读取本轮宿主证据；先用 read_task_evidence_batch 批量读取本轮需要引用的证据，每批最多8条。"
        "每条从 offset=0 开始；next_offset 非空就继续批量读取下一页，直到全部读完，"
        "再修正编号、对象和观测时间。只看索引、猜测编号不能通过纠错。"
        "单条可用 read_task_evidence；两种工具读取权限一致。先列齐要核对的引用，一次请求多条，"
        "不要为每条小证据单独调用一轮模型。不要重复读取已经读完整的证据。"
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
    for field in ("cluster_artifact_refs", "review_artifact_references"):
        corrected.setdefault("metadata", {}).pop(field, None)
        references = original.get("metadata", {}).get(field)
        if references:
            corrected["metadata"][field] = deepcopy(references)
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
