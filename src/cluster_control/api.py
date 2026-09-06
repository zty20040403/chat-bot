from __future__ import annotations

import asyncio
import hmac
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from prometheus_client import make_asgi_app
from pydantic import BaseModel, ConfigDict, Field

from .diagnostics import IncidentDiagnosticService
from .service import FleetControlService


_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service")
_TARGET_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class DiagnosticRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=64)
    host_id: str = Field(default="h610", min_length=1, max_length=64)
    target_id: str = Field(default="", max_length=64)
    subject: str = Field(default="", max_length=1000)
    requested_by: str = Field(default="kennethbot", min_length=1, max_length=200)


def _read_api_token(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Control credential unavailable") from exc
    token = raw.rstrip(b"\r\n")
    if (
        len(raw) >= 515
        or not 32 <= len(token) <= 512
        or any(byte < 33 or byte > 126 for byte in token)
    ):
        raise HTTPException(status_code=503, detail="Control credential invalid")
    return token.decode("ascii")


def create_app(
    service: FleetControlService,
    *,
    api_token_file: str | Path,
    diagnostics: IncidentDiagnosticService | None = None,
) -> FastAPI:
    token_path = Path(api_token_file)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if diagnostics is not None:
            await diagnostics.close()
        await service.close()

    app = FastAPI(
        title="Kennethbot Cluster Control",
        version="1",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount("/metrics", make_asgi_app(registry=service.metrics_registry))

    def authenticate(authorization: str = Header(default="")) -> None:
        expected = _read_api_token(token_path)
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def valid_host(host: str) -> str:
        if _HOST_RE.fullmatch(host) is None:
            raise HTTPException(status_code=422, detail="Invalid host id")
        return host

    def valid_unit(unit: str) -> str:
        if _UNIT_RE.fullmatch(unit) is None:
            raise HTTPException(status_code=422, detail="Invalid unit name")
        return unit

    auth = [Depends(authenticate)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/backends", dependencies=auth)
    async def backends() -> dict[str, object]:
        snapshot = await asyncio.to_thread(service.backend_snapshot)
        return {"items": [snapshot]}

    @app.get("/v1/capabilities", dependencies=auth)
    async def capabilities() -> dict[str, object]:
        return await service.capabilities()

    @app.get("/v1/fleet", dependencies=auth)
    async def fleet() -> dict[str, object]:
        return await service.fleet_overview()

    @app.get("/v1/hosts/{host}", dependencies=auth)
    async def host(host: str) -> dict[str, object]:
        return await service.host_facts(valid_host(host))

    @app.get("/v1/hosts/{host}/units/{unit}", dependencies=auth)
    async def unit(host: str, unit: str) -> dict[str, object]:
        return await service.unit_status(valid_host(host), valid_unit(unit))

    @app.get("/v1/hosts/{host}/units/{unit}/logs", dependencies=auth)
    async def logs(
        host: str,
        unit: str,
        lines: int = Query(default=50, ge=1, le=200),
        since_seconds: int = Query(default=3600, ge=1, le=86400),
    ) -> dict[str, object]:
        return await service.unit_logs(
            valid_host(host), valid_unit(unit), lines, since_seconds
        )

    @app.get("/v1/alerts", dependencies=auth)
    async def alerts() -> dict[str, object]:
        return await service.alerts()

    @app.get("/v1/failed-units", dependencies=auth)
    async def failed_units() -> dict[str, object]:
        return await service.failed_units()

    @app.get("/v1/observations", dependencies=auth)
    async def observations(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, object]:
        items = await asyncio.to_thread(service.recent_observations, limit=limit)
        return {"items": items}

    @app.get("/v1/diagnostics/templates", dependencies=auth)
    async def diagnostic_templates() -> dict[str, object]:
        return {"items": diagnostics.templates() if diagnostics is not None else []}

    @app.get("/v1/diagnostics", dependencies=auth)
    async def diagnostic_runs(
        limit: int = Query(default=30, ge=1, le=100),
    ) -> dict[str, object]:
        items = (
            await asyncio.to_thread(diagnostics.recent, limit=limit)
            if diagnostics is not None
            else []
        )
        return {"items": items}

    @app.get("/v1/diagnostics/{run_id}", dependencies=auth)
    async def diagnostic_detail(run_id: int) -> dict[str, object]:
        if diagnostics is None:
            raise HTTPException(status_code=503, detail="Diagnostics unavailable")
        result = await asyncio.to_thread(diagnostics.detail, run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Diagnostic run not found")
        return result

    @app.post("/v1/diagnostics", dependencies=auth)
    async def run_diagnostic(request: DiagnosticRunRequest) -> dict[str, object]:
        if diagnostics is None:
            raise HTTPException(status_code=503, detail="Diagnostics unavailable")
        host_id = valid_host(request.host_id)
        if request.target_id and _TARGET_RE.fullmatch(request.target_id) is None:
            raise HTTPException(status_code=422, detail="Invalid diagnostic target")
        try:
            return await diagnostics.run(
                template_key=request.template,
                host_id=host_id,
                target_id=request.target_id,
                subject=request.subject,
                requested_by=request.requested_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    return app
