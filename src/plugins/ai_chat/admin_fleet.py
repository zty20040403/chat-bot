from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .fleet_client import FleetControlError


_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service")
_TARGET_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class FleetDiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=64)
    host_id: str = Field(default="h610", min_length=1, max_length=64)
    target_id: str = Field(default="", max_length=64)
    subject: str = Field(default="", max_length=1000)


def _validate(value: str, pattern: re.Pattern[str], kind: str) -> str:
    if pattern.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail=f"invalid {kind}")
    return value


async def _safe_call(
    name: str,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[str, dict[str, object]]:
    try:
        payload = await operation()
    except FleetControlError as exc:
        payload = {
            "ok": False,
            "status": "unavailable",
            "error": {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            },
        }
    except Exception:
        payload = {
            "ok": False,
            "status": "unavailable",
            "error": {
                "code": "control_unavailable",
                "message": "集群控制服务暂时不可用",
                "retryable": True,
            },
        }
    return name, payload


def register_fleet_admin_routes(
    router: APIRouter,
    services: Any,
    authorize: Callable[[Optional[str]], None],
    versioned: Callable[[str, dict[str, object]], dict[str, object]],
) -> None:
    @router.get("/api/fleet")
    async def fleet(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        client = services.fleet_client
        if client is None:
            return versioned(
                "fleet",
                {
                    "configured": False,
                    "fleet": {"status": "unavailable", "inventory": []},
                    "backends": {"items": []},
                    "capabilities": {"capabilities": []},
                    "observations": {"items": []},
                    "diagnostic_templates": {"items": []},
                    "diagnostics": {"items": []},
                },
            )
        results = await asyncio.gather(
            _safe_call("fleet", client.fleet),
            _safe_call("backends", client.backends),
            _safe_call("capabilities", client.capabilities),
            _safe_call("observations", lambda: client.observations(limit=50)),
            _safe_call("diagnostic_templates", client.diagnostic_templates),
            _safe_call("diagnostics", lambda: client.diagnostics(limit=30)),
        )
        return versioned("fleet", {"configured": True, **dict(results)})

    def configured_client() -> Any:
        client = services.fleet_client
        if client is None:
            raise HTTPException(status_code=503, detail="集群控制服务尚未配置")
        return client

    @router.get("/api/fleet/hosts/{host_id}")
    async def fleet_host(
        host_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            return await configured_client().host(
                _validate(host_id, _HOST_RE, "host id")
            )
        except FleetControlError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @router.get("/api/fleet/hosts/{host_id}/units/{unit}")
    async def fleet_unit(
        host_id: str,
        unit: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            return await configured_client().unit(
                _validate(host_id, _HOST_RE, "host id"),
                _validate(unit, _UNIT_RE, "unit"),
            )
        except FleetControlError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @router.get("/api/fleet/hosts/{host_id}/units/{unit}/logs")
    async def fleet_logs(
        host_id: str,
        unit: str,
        lines: int = Query(default=50, ge=1, le=200),
        since_seconds: int = Query(default=3600, ge=1, le=86400),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            return await configured_client().logs(
                _validate(host_id, _HOST_RE, "host id"),
                _validate(unit, _UNIT_RE, "unit"),
                lines=lines,
                since_seconds=since_seconds,
            )
        except FleetControlError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @router.get("/api/fleet/diagnostics/{run_id}")
    async def fleet_diagnostic_detail(
        run_id: int,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            return await configured_client().diagnostic(run_id)
        except FleetControlError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @router.post("/api/fleet/diagnostics")
    async def run_fleet_diagnostic(
        request: FleetDiagnosticRequest,
        authorization: Optional[str] = Header(default=None),
        admin_actor: Optional[str] = Header(default=None, alias="X-Admin-Actor"),
    ) -> dict[str, object]:
        authorize(authorization)
        host_id = _validate(request.host_id, _HOST_RE, "host id")
        if request.target_id and _TARGET_RE.fullmatch(request.target_id) is None:
            raise HTTPException(status_code=422, detail="invalid target id")
        actor = " ".join(str(admin_actor or "admin-console").split())[:160]
        try:
            return await configured_client().run_diagnostic(
                template=request.template,
                host_id=host_id,
                target_id=request.target_id,
                subject=request.subject,
                requested_by=actor,
            )
        except FleetControlError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
