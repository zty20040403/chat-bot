from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .scheduling import ResourcePolicyStore


class WorkerAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desired_availability: str
    reason: str = Field(default="", max_length=500)
    resource_version: int = Field(ge=1)


class WorkerCapacityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_gpu: bool = False
    cpu_limit_millis: int = Field(ge=0, le=128_000)
    memory_limit_bytes: int = Field(ge=0, le=128 * 1024**3)
    gpu_limit_slots: int = Field(default=0, ge=0, le=16)
    resource_version: int = Field(ge=1)


class BorrowGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str
    grantee_actor_id: str
    origin_scope: str
    allowed_kinds: list[str]
    valid_from: int | None = None
    valid_until: int
    cpu_millis: int = Field(default=500, ge=50, le=8000)
    memory_bytes: int = Field(
        default=256 * 1024**2,
        ge=16 * 1024**2,
        le=8 * 1024**3,
    )
    gpu_slots: int = Field(default=0, ge=0, le=8)
    priority: str = "normal"
    max_cost_microunits: int = Field(default=0, ge=0, le=10**12)


class ResourceStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    resource_version: int = Field(ge=1)


def build_resource_router(
    *,
    auth: list[Any],
    signed_principal: Callable[..., Any],
    policy_store: Callable[[], ResourcePolicyStore],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/resource-policies", dependencies=auth)
    async def resource_policy_list() -> dict[str, object]:
        return {"items": await asyncio.to_thread(policy_store().policies)}

    @router.post("/v1/resource-policies/{worker_id}/availability")
    async def resource_policy_availability(
        worker_id: str,
        body: WorkerAvailabilityRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                policy_store().set_worker_availability,
                worker_id,
                actor_id=principal[0],
                desired=body.desired_availability,
                reason=body.reason,
                expected_version=body.resource_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.post("/v1/resource-policies/{worker_id}/capacity")
    async def resource_policy_capacity(
        worker_id: str,
        body: WorkerCapacityRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                policy_store().configure_worker_capacity,
                worker_id,
                actor_id=principal[0],
                allow_gpu=body.allow_gpu,
                cpu_limit_millis=body.cpu_limit_millis,
                memory_limit_bytes=body.memory_limit_bytes,
                gpu_limit_slots=body.gpu_limit_slots,
                expected_version=body.resource_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.get("/v1/borrow-grants", dependencies=auth)
    async def borrow_grant_list(
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(policy_store().grants, limit=limit)
        }

    @router.post("/v1/borrow-grants")
    async def borrow_grant_create(
        body: BorrowGrantRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                policy_store().create_grant,
                body.model_dump(exclude_none=True),
                actor_id=principal[0],
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.post("/v1/borrow-grants/{grant_id}/status")
    async def borrow_grant_status(
        grant_id: str,
        body: ResourceStatusRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                policy_store().set_grant_status,
                grant_id,
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
