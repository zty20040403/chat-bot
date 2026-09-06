from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .auth import CredentialFileAuthenticator
from .deployment_service import DeploymentService


class DeploymentPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: str
    source_revision: str
    expected_remote_revision: str = ""
    target_hosts: list[str]
    requested_changes: list[str]
    strategy: str = "serial"
    canary_host_id: str = ""
    failure_policy: str = "pause"
    deadline_at: int | None = None
    idempotency_key: str


class DeploymentApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_hash: str = Field(pattern="^[a-f0-9]{64}$")
    resource_version: int = Field(ge=1)


class DeployerLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fence: int = Field(ge=1)


class DeploymentTargetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fence: int = Field(ge=1)
    status: str
    step: str = Field(min_length=1, max_length=80)
    current_toplevel: str = Field(default="", max_length=500)
    target_toplevel: str = Field(default="", max_length=500)
    actual_toplevel: str = Field(default="", max_length=500)
    verification: dict[str, object] = Field(default_factory=dict)
    error_code: str = Field(default="", max_length=120)


class DeploymentCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fence: int = Field(ge=1)
    ok: bool
    result: dict[str, object] = Field(default_factory=dict)


def build_deployment_router(
    *,
    auth: list[Any],
    signed_principal: Callable[..., Any],
    deployment_service: Callable[[], DeploymentService],
    deployer_authenticator: CredentialFileAuthenticator | None,
) -> APIRouter:
    router = APIRouter()

    def deployer_principal(authorization: str = Header(default="")) -> str:
        deployer_id = (
            deployer_authenticator.authenticate(authorization)
            if deployer_authenticator is not None
            else None
        )
        if deployer_id is None:
            raise HTTPException(status_code=401, detail="Unauthorized deployer")
        return deployer_id

    @router.get("/v1/deployment-capabilities", dependencies=auth)
    async def deployment_capabilities() -> dict[str, object]:
        return deployment_service().capabilities()

    @router.get("/v1/deployments", dependencies=auth)
    async def deployments(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(
                deployment_service().store.recent,
                limit,
            )
        }

    @router.post("/v1/deployments/prepare")
    async def prepare_deployment(
        body: DeploymentPrepareRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                deployment_service().prepare,
                body.model_dump(exclude_none=True, exclude_defaults=False),
                actor_id=principal[0],
                origin_scope=principal[1],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.get("/v1/deployments/{deployment_id}")
    async def deployment_detail(
        deployment_id: str,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            item = await asyncio.to_thread(
                deployment_service().visible,
                deployment_id,
                actor_id=principal[0],
                origin_scope=principal[1],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        if item is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        return item

    @router.post("/v1/deployments/{deployment_id}/approve")
    async def approve_deployment(
        deployment_id: str,
        body: DeploymentApproveRequest,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                deployment_service().approve,
                deployment_id,
                actor_id=principal[0],
                contract_hash=body.contract_hash,
                resource_version=body.resource_version,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.post("/v1/deployments/{deployment_id}/cancel")
    async def cancel_deployment(
        deployment_id: str,
        principal: tuple[str, str] = Depends(signed_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                deployment_service().store.cancel,
                deployment_id,
                actor_id=principal[0],
                origin_scope=principal[1],
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @router.post("/v1/deployer/claim")
    async def claim_deployment(
        deployer_id: str = Depends(deployer_principal),
    ) -> dict[str, object]:
        try:
            item = await asyncio.to_thread(deployment_service().claim, deployer_id)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"deployment": item}

    @router.post("/v1/deployer/deployments/{deployment_id}/renew")
    async def renew_deployment(
        deployment_id: str,
        body: DeployerLeaseRequest,
        deployer_id: str = Depends(deployer_principal),
    ) -> dict[str, int | bool]:
        try:
            return await asyncio.to_thread(
                deployment_service().store.renew,
                deployment_id,
                deployer_id=deployer_id,
                fence=body.fence,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.post("/v1/deployer/deployments/{deployment_id}/targets/{host_id}")
    async def update_deployment_target(
        deployment_id: str,
        host_id: str,
        body: DeploymentTargetUpdateRequest,
        deployer_id: str = Depends(deployer_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                deployment_service().update_target,
                deployment_id,
                host_id,
                deployer_id=deployer_id,
                **body.model_dump(),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.post("/v1/deployer/deployments/{deployment_id}/complete")
    async def complete_deployment(
        deployment_id: str,
        body: DeploymentCompleteRequest,
        deployer_id: str = Depends(deployer_principal),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                deployment_service().complete,
                deployment_id,
                deployer_id=deployer_id,
                fence=body.fence,
                ok=body.ok,
                result=body.result,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    return router
