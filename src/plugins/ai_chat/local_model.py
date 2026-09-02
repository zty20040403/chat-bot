from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any

import httpx

from .model_catalog import ModelProfile


class LocalModelControlError(RuntimeError):
    pass


class LocalModelRuntime:
    """Background readiness cache; chat requests never perform health I/O."""

    def __init__(
        self,
        profile: ModelProfile,
        *,
        interval_seconds: float = 15,
        timeout_seconds: float = 2,
        control_url: str = "",
        control_token: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.profile = profile
        self.interval = max(interval_seconds, 1)
        self.timeout = max(timeout_seconds, 0.1)
        self.control_url = control_url.rstrip("/")
        self._token = control_token.strip()
        self._client = client or httpx.AsyncClient(
            timeout=self.timeout, trust_env=False, follow_redirects=False,
        )
        self._lock = threading.RLock()
        self._revision = 0
        self._ready_generation = 0
        self._checked_monotonic = 0.0
        self._pending_action = ""
        self._remote: dict[str, Any] = {}
        self._state: dict[str, Any] = {
            "state": "unknown", "ready": False, "checked_at": None,
            "reason_code": "health_pending", "reason": "等待首次健康探测",
            "latency_ms": None,
        }

    @property
    def control_configured(self) -> bool:
        return bool(self.control_url and len(self._token) >= 32)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            if self._checked_monotonic and time.monotonic() - self._checked_monotonic > self.interval * 3 + self.timeout * 2:
                state.update(state="unknown", ready=False, reason_code="health_stale", reason="健康探测结果已过期，暂不使用千问")
            if self._pending_action:
                state.update(
                    state="starting" if self._pending_action == "start" else "stopping",
                    ready=False, reason_code="control_pending", reason="服务控制请求正在处理",
                )
            return {
                **state, "profile": self.profile.name, "model": self.profile.model,
                "control_configured": self.control_configured,
                "control_reachable": bool(self._remote.get("reachable")),
                "gpu": self._remote.get("gpu"),
                "service_state": self._remote.get("state", "unknown"),
                "control_checked_at": self._remote.get("checked_at"),
                "probe_interval_seconds": self.interval,
                "ready_generation": self._ready_generation,
            }

    def unavailable_reason(self, profile_name: str) -> tuple[str, str] | None:
        if profile_name != self.profile.name:
            return None
        state = self.snapshot()
        return None if state["ready"] else (state["reason_code"], state["reason"])

    async def probe_once(self) -> None:
        with self._lock:
            revision = self._revision
        remote: dict[str, Any] = {"reachable": False, "checked_at": int(time.time())}
        if self.control_configured:
            try:
                response = await self._client.get(
                    self.control_url + "/status",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("state") in {"stopped", "starting", "running", "stopping", "failed", "unknown"}:
                    remote.update(state=payload["state"], gpu=payload.get("gpu"), reachable=True)
            except (httpx.HTTPError, ValueError, TypeError):
                pass

        started = time.monotonic()
        state: dict[str, Any] = {"ready": False, "state": "unreachable", "reason_code": "health_unreachable", "reason": "千问推理接口不可达，服务可能未启动或网络异常"}
        try:
            headers = {"Authorization": f"Bearer {self.profile.api_key}"} if self.profile.api_key else {}
            response = await self._client.get(self.profile.base_url.rstrip("/") + "/models", headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                raise ValueError("invalid models response")
            if any(isinstance(item, dict) and item.get("id") == self.profile.model for item in items):
                state.update(ready=True, state="ready", reason_code="ready", reason="模型已就绪")
            else:
                state.update(state="loading", reason_code="model_not_loaded", reason="接口在线，但目标模型尚未加载")
        except httpx.HTTPStatusError as exc:
            state.update(state="unavailable", reason_code="health_http_error", reason=f"千问健康接口返回 HTTP {exc.response.status_code}")
        except (httpx.HTTPError, ValueError, TypeError):
            pass

        service_state = remote.get("state")
        if not state["ready"] or service_state in {"stopped", "stopping", "failed"}:
            statuses = {
                "stopped": ("stopped", "service_stopped", "Qwen 服务未启动"),
                "starting": ("starting", "service_starting", "Qwen 服务正在启动"),
                "running": ("loading", "model_loading", "Qwen 服务运行中，模型尚未就绪"),
                "stopping": ("stopping", "service_stopping", "Qwen 服务正在停止"),
                "failed": ("failed", "service_failed", "Qwen 服务启动失败"),
            }
            if service_state in statuses and not (service_state == "running" and state["reason_code"] == "health_http_error"):
                label, code, reason = statuses[service_state]
                state.update(state=label, ready=False, reason_code=code, reason=reason)
        state.update(checked_at=int(time.time()), latency_ms=round((time.monotonic() - started) * 1000, 1))
        with self._lock:
            # A probe begun before a start/stop command cannot undo its routing gate.
            if revision == self._revision:
                if state["ready"] and not self.snapshot()["ready"]:
                    self._ready_generation += 1
                self._state = state
                self._remote = remote
                self._checked_monotonic = time.monotonic()

    async def run_forever(self) -> None:
        while True:
            await self.probe_once()
            await asyncio.sleep(self.interval)

    def control(self, action: str, request_id: str) -> dict[str, Any]:
        if action not in {"start", "stop"}:
            raise LocalModelControlError("仅支持启动或停止千问")
        request_id = str(uuid.UUID(request_id))
        if not self.control_configured:
            raise LocalModelControlError("尚未配置 WSL 管理接口或管理 Token")
        with self._lock:
            self._revision += 1
            self._pending_action = action
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    self.control_url + "/" + action,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"request_id": request_id},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("request_id") != request_id or payload.get("accepted") is not True:
                    raise ValueError("invalid control response")
        except (httpx.HTTPError, ValueError) as exc:
            with self._lock:
                self._state.update(state="unknown", ready=False, reason_code="control_unknown", reason="控制请求结果未确认，等待后台探测；不要反复点击")
            raise LocalModelControlError("WSL 控制请求结果未确认，请等待状态探测；未自动重试") from exc
        else:
            with self._lock:
                self._state.update(
                    state="starting" if action == "start" else "stopping", ready=False,
                    reason_code="service_starting" if action == "start" else "service_stopping",
                    reason="Qwen 服务正在启动" if action == "start" else "Qwen 服务正在停止",
                )
            return {"accepted": True, "action": action, "request_id": request_id}
        finally:
            with self._lock:
                self._revision += 1
                self._pending_action = ""

    async def close(self) -> None:
        await self._client.aclose()
