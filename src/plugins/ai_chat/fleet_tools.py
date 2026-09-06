"""Compact, evidence-labelled projections for conversational status queries."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .fleet_client import FleetControlError


def requires_local_model_status(text: str) -> bool:
    return bool(
        re.search(r"千问|qwen", text, re.IGNORECASE)
        and re.search(
            r"状态|寄了|寄了吗|挂了|挂了吗|在线|能用|可用|连通|连不上|"
            r"启动|开了|开没|正常|现在|目前|还活|还在|关了",
            text,
        )
    )


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _error(payload: dict[str, Any]) -> dict[str, Any] | None:
    error = _object(payload.get("error"))
    return {
        key: error[key] for key in ("code", "message", "retryable") if key in error
    } or None


def _fresh(payload: dict[str, Any], now: int) -> bool:
    expires = payload.get("expires_at")
    return payload.get("status") == "fresh" and (expires is None or expires > now)


def _root_disk(host: dict[str, Any]) -> dict[str, Any] | None:
    pressure = _object(host.get("pressure"))
    values: dict[str, int] = {}
    for metric, label in (
        ("filesystem_size_bytes", "total_bytes"),
        ("filesystem_available_bytes", "available_bytes"),
    ):
        for sample in _items(_object(pressure.get(metric)).get("samples")):
            if _object(sample.get("labels")).get("mountpoint") != "/":
                continue
            value = sample.get("value")
            if (
                sample.get("state") == "available"
                and isinstance(value, (int, float))
                and value >= 0
            ):
                values[label] = int(value)
                break
    if "available_bytes" not in values:
        return None
    return {
        "mountpoint": "/",
        **values,
        "available_gib": round(values["available_bytes"] / 1024**3, 1),
    }


def summarize_fleet(
    payload: dict[str, Any], *, host_id: str = "", now: int | None = None
) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else now
    observations = {
        str(item.get("host")): item
        for item in _items(_object(payload.get("data")).get("hosts"))
    }
    inventory = _items(payload.get("inventory"))
    hosts = list(
        dict.fromkeys(
            [str(item["host_id"]) for item in inventory if item.get("host_id")]
            + list(observations)
        )
    )
    if host_id:
        hosts = [host_id] if host_id in hosts else []
    failures = _object(payload.get("failed_units"))
    failed_by_host = {
        str(item.get("host")): item
        for item in _items(_object(failures.get("data")).get("hosts"))
    }
    alerts_result = _object(payload.get("active_alerts"))
    alerts = _items(_object(alerts_result.get("data")).get("alerts"))
    summaries: list[dict[str, Any]] = []
    for name in hosts:
        host = observations.get(name, {})
        agent = _object(host.get("agent"))
        exporter = _object(host.get("exporter"))
        sample_at = exporter.get("sample_at_unix_seconds")
        exporter_fresh = (
            isinstance(sample_at, (int, float)) and 0 <= timestamp - sample_at <= 90
        )
        current = _fresh(payload, timestamp) and bool(host)
        agent_up = agent.get("state") == "reachable"
        exporter_up = exporter.get("state") == "up" and exporter_fresh
        online = current and (agent_up or exporter_up)
        failed = failed_by_host.get(name, {})
        units = _items(failed.get("units"))
        failure_count = (
            len(units)
            if _fresh(failures, timestamp)
            and failed.get("state") == "available"
            and isinstance(failed.get("units"), list)
            else None
        )
        if (
            failure_count is None
            and current
            and agent_up
            and isinstance(agent.get("failed_units"), int)
        ):
            failure_count = agent["failed_units"]
        host_alerts = [
            item
            for item in alerts
            if _object(item.get("labels")).get("instance") == name
        ]
        alert_count = len(host_alerts) if _fresh(alerts_result, timestamp) else None
        status = "online" if online else "stale" if host and not current else "unknown"
        summary = f"{name} 在线"
        if online:
            summary += "，监控采样正常" if exporter_up else "，监控采样未确认"
            if failure_count is not None:
                summary += f"，已授权服务中有 {failure_count} 个失败"
            if alert_count:
                summary += f"，另有 {alert_count} 条活动告警"
        elif not current:
            summary = f"{name} 暂无新鲜状态，不能据此判断关机"
        else:
            summary = f"{name} 当前观察信号不可达或不完整，原因尚未确认"
        summaries.append(
            {
                "host_id": name,
                "status": status,
                "summary": summary,
                "agent_state": agent.get("state", "unknown"),
                "agent_observed_at": agent.get("observed_at"),
                "exporter_state": exporter.get("state", "unknown"),
                "exporter_sample_at": sample_at,
                "root_disk": _root_disk(host) if current and exporter_fresh else None,
                "failed_service_count": failure_count,
                "failed_services": [
                    {
                        key: item[key]
                        for key in ("unit", "active_state", "sub_state")
                        if key in item
                    }
                    for item in units[:5]
                ],
                "active_alert_count": alert_count,
                "alerts": [
                    {
                        "name": _object(item.get("labels")).get("alertname"),
                        "severity": _object(item.get("labels")).get("severity"),
                        "summary": str(
                            _object(item.get("annotations")).get("summary", "")
                        )[:180],
                    }
                    for item in host_alerts[:3]
                ],
            }
        )
    return {
        "ok": bool(summaries) and any(item["status"] == "online" for item in summaries),
        "source": "maxops",
        "status": payload.get("status", "unavailable"),
        "observed_at": payload.get("observed_at"),
        "received_at": payload.get("received_at"),
        "hosts": summaries,
        "error": _error(payload),
        "scope": "服务与告警只覆盖已授权项；在线不代表所有业务均已验证。",
        "answer_guidance": "直接说明已有事实；单项查询失败不否定其他成功观测。状态已明确时不要要求用户重填主机名。",
    }


async def inspect_host(client: Any, host_id: str) -> dict[str, Any]:
    async def read(call: Any) -> dict[str, Any]:
        try:
            return await call
        except FleetControlError as exc:
            return {
                "status": "unavailable",
                "error": {"code": exc.code, "message": str(exc)},
            }

    facts_result, fleet_result = await asyncio.gather(
        read(client.host(host_id)), read(client.fleet())
    )
    summary = summarize_fleet(fleet_result, host_id=host_id)
    facts = _object(_object(facts_result.get("data")).get("facts"))
    summary["system"] = {
        "status": facts_result.get("status", "unavailable"),
        "observed_at": facts_result.get("observed_at"),
        **{
            key: facts[key]
            for key in (
                "kernel",
                "uptime_seconds",
                "profile_generation",
                "profile_matches_running",
            )
            if key in facts
        },
        "error": _error(facts_result),
    }
    if (
        _fresh(facts_result, int(time.time()))
        and _object(facts_result.get("data")).get("host") == host_id
    ):
        summary["ok"] = True
        if not any(item["status"] == "online" for item in summary["hosts"]):
            summary["summary"] = (
                f"{host_id} 在线，已成功读取实时系统信息；资源与告警概况未确认。"
            )
    return summary


async def model_status(context: Any, profile_name: str = "") -> dict[str, Any]:
    runtime = context.local_model
    name = profile_name.strip() or (runtime.profile.name if runtime else "qwen-local")
    profiles = {profile.name: profile for profile in context.model_catalog.profiles}
    profile = profiles.get(name)
    if profile is None:
        return {
            "ok": False,
            "error": "模型配置不存在。",
            "available_profiles": sorted(profiles),
        }
    readiness: dict[str, Any] | None = None
    if runtime is not None and runtime.profile.name == name:
        await runtime.probe_once()
        readiness = runtime.snapshot()
    health = context.llm_gateway.health_snapshot().get(name, {})
    success_at = int(health.get("last_success_at") or 0)
    failure_at = int(health.get("last_failure_at") or 0)
    last_response = (
        "failed"
        if failure_at >= success_at and failure_at
        else "succeeded"
        if success_at
        else "unknown"
    )
    if readiness is not None:
        summary = (
            "千问接口可访问，目标模型已列出"
            if readiness["ready"]
            else str(readiness["reason"])
        )
    else:
        summary = "未执行主动探测，以下为 Bot 进程启动后的实际请求记录"
    if last_response == "failed":
        summary += "；最近一次实际请求失败"
    elif last_response == "succeeded":
        summary += "；最近一次实际请求成功"
    else:
        summary += "；本进程暂无实际请求成功记录，尚不能确认生成回复正常"
    return {
        "ok": True,
        "source": "kennethbot-model-runtime",
        "profile": name,
        "model": profile.model,
        "checked_at": int(time.time()),
        "summary": summary,
        "readiness": {
            key: readiness.get(key)
            for key in (
                "state",
                "ready",
                "reason",
                "checked_at",
                "latency_ms",
                "service_state",
                "control_configured",
            )
        }
        if readiness
        else None,
        "requests": {
            "latest_result": last_response,
            "last_success_at": success_at or None,
            "last_failure_at": failure_at or None,
            **{
                key: health.get(key)
                for key in (
                    "request_count",
                    "total_successes",
                    "total_failures",
                    "average_latency_ms",
                    "last_error_kind",
                )
            },
        },
        "routing": {
            "simple_chat_selected": context.settings.model_simple_chat_profile == name,
            "circuit_state": health.get("status", "unknown"),
            "circuit_breaker_enabled": profile.circuit_breaker_enabled,
        },
        "answer_guidance": "先回答接口与模型的当前状态，再按时间说明最近生成结果。/models 成功不等于生成成功；历史失败不等于现在仍失败。未配置启停管理接口不代表推理接口不可用。",
    }
