from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .api_resources import ResourceStatusRequest
from .execution_service import ClusterExecutionService
from .reliability import ReliabilityStore


class IncidentResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, object] = Field(default_factory=dict)
    resource_version: int = Field(ge=1)


class RunbookCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    host_id: str = Field(default="", max_length=64)
    service_ref: str = Field(default="", max_length=160)
    symptoms: str = Field(min_length=1, max_length=4000)
    confirmed_cause: str = Field(min_length=1, max_length=4000)
    resolution: list[dict[str, object] | str]
    applicability: dict[str, object]
    evidence_refs: list[str]
    status: str = "draft"
    confidence: str = "unknown"


class GuardianRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str
    host_id: str
    service_ref: str = ""
    mode: str = "observe"
    starts_at: int | None = None
    expires_at: int
    interval_seconds: int = Field(default=60, ge=15, le=86_400)
    failure_threshold: int = Field(default=3, ge=1, le=20)
    max_actions: int = Field(default=0, ge=0, le=20)
    probe_policy: dict[str, object] = Field(default_factory=dict)
    authorized_action: dict[str, object] = Field(default_factory=dict)


class CaseSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    host_id: str = Field(default="", max_length=64)
    service_ref: str = Field(default="", max_length=160)
    current_facts: dict[str, object] = Field(default_factory=dict)
    candidate_case_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=10, ge=1, le=50)


def build_reliability_router(
    *,
    auth: list[Any],
    signed_principal: Callable[..., Any],
    reliability_store: Callable[[], ReliabilityStore],
    execution_service: Callable[[], ClusterExecutionService],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/incidents", dependencies=auth)
    async def incidents(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(
                reliability_store().incidents,
                limit=limit,
            )
        }

    @router.get("/v1/incidents/{incident_id}", dependencies=auth)
    async def incident(incident_id: str) -> dict[str, object]:
        item = await asyncio.to_thread(reliability_store().incident, incident_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return item

    @router.post("/v1/incidents/{incident_id}/resolve")
    async def incident_resolve(
        incident_id: str,
        body: IncidentResolveRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                reliability_store().resolve_incident,
                incident_id,
                actor_id=principal[0],
                summary=body.summary,
                evidence=body.evidence,
                expected_version=body.resource_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.get("/v1/runbook-cases", dependencies=auth)
    async def runbook_cases(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(
                reliability_store().cases,
                limit=limit,
            )
        }

    @router.post("/v1/runbook-cases")
    async def runbook_case_create(
        body: RunbookCaseRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                reliability_store().create_case,
                body.model_dump(),
                actor_id=principal[0],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.post("/v1/runbook-cases/search", dependencies=auth)
    async def runbook_case_search(body: CaseSearchRequest) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(
                reliability_store().search_cases,
                body.query,
                host_id=body.host_id,
                service_ref=body.service_ref,
                current_facts=body.current_facts,
                candidate_case_ids=body.candidate_case_ids,
                limit=body.limit,
            )
        }

    @router.get("/v1/guardians", dependencies=auth)
    async def guardians(
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(
                reliability_store().guardians,
                limit=limit,
            )
        }

    @router.get("/v1/guardians/{guardian_id}")
    async def guardian_detail(
        guardian_id: str,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        item = await asyncio.to_thread(
            reliability_store().guardian,
            guardian_id,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Guardian not found")
        if item.get("actor_id") != principal[0]:
            raise HTTPException(
                status_code=403,
                detail="Guardian belongs to another administrator",
            )
        return item

    @router.post("/v1/guardians")
    async def guardian_create(
        body: GuardianRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                reliability_store().create_guardian,
                body.model_dump(exclude_none=True),
                actor_id=principal[0],
                origin_scope=principal[1],
                known_targets=set(execution_service().diagnostic_targets),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.post("/v1/guardians/{guardian_id}/status")
    async def guardian_status(
        guardian_id: str,
        body: ResourceStatusRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                reliability_store().set_guardian_status,
                guardian_id,
                actor_id=principal[0],
                status=body.status,
                expected_version=body.resource_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    return router
