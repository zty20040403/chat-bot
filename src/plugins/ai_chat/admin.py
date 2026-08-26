from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from .admin_control import (
    AdminControlStore,
    AdminVersionConflict,
    MutationResult,
    parse_expected_version,
)
from .admin_dashboard import ADMIN_FAVICON_SVG, admin_asset_path, dashboard_html
from .conversation_scope import ConversationScope
from .tool_policy import (
    TOOL_POLICIES,
    admin_tool_manifest,
    configure_tool_overrides,
    set_tool_enabled,
    tool_enabled,
)


@dataclass(frozen=True)
class AdminServices:
    version: str
    started_at: int
    delivery_store: Any = None
    usage_store: Any = None
    running_tasks: Any = None
    job_store: Any = None
    job_worker: Any = None
    bridge_router: Any = None
    bridge_state: Any = None
    browser_manager: Any = None
    background_tasks: Any = None
    model_catalog: Any = None
    llm_gateway: Any = None
    model_preferences: Any = None
    user_profiles: Any = None
    message_ledger: Any = None
    settings: Any = None
    sandbox_manager: Any = None
    sticker_inventory: Any = None
    media_library: Any = None
    source_store: Any = None
    media_cleanup: Any = None
    vision_worker: Any = None
    turn_journal: Any = None
    database: Any = None
    telemetry: Any = None


@dataclass(frozen=True)
class AdminMutationContext:
    expected_version: int | None
    actor: str


class ModelSelectionRequest(BaseModel):
    profile: str | None = None


class GroupEnabledRequest(BaseModel):
    enabled: bool


class CleanupConfirmationRequest(BaseModel):
    confirmation_token: str


class ToolEnabledRequest(BaseModel):
    enabled: bool


class MediaReviewRequest(BaseModel):
    state: str


class AdminEventBroker:
    def __init__(
        self,
        version_provider: Callable[[], dict[str, int]] | None = None,
    ) -> None:
        self._sequence = 0
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._version_provider = version_provider

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, *resources: str) -> None:
        normalized = sorted({str(item).strip() for item in resources if item})
        if not normalized:
            return
        self._sequence += 1
        payload: dict[str, object] = {
            "sequence": self._sequence,
            "type": "resources.changed",
            "resources": normalized,
            "timestamp": int(time.time()),
        }
        if self._version_provider is not None:
            versions = self._version_provider()
            payload["versions"] = {
                resource: versions.get(resource, 0) for resource in normalized
            }
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                continue


def register_admin(
    app: Any,
    services: AdminServices,
    *,
    path: str = "/bot-admin",
    token: str = "",
) -> None:
    prefix = "/" + path.strip("/")
    router = APIRouter(prefix=prefix)
    expected_token = token.strip()
    control_store = AdminControlStore(services.database)
    configure_tool_overrides(control_store.tool_overrides())
    event_broker = AdminEventBroker(control_store.versions)

    def authorize(authorization: Optional[str] = Header(default=None)) -> None:
        if not expected_token:
            return
        supplied = ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

    def mutation_context(
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        admin_actor: Optional[str] = Header(default=None, alias="X-Admin-Actor"),
    ) -> AdminMutationContext:
        try:
            expected_version = parse_expected_version(if_match)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return AdminMutationContext(
            expected_version=expected_version,
            actor=" ".join(str(admin_actor or "admin-console").split())[:160],
        )

    def mutate(
        context: AdminMutationContext,
        resource_key: str,
        *,
        action: str,
        target: str = "",
        before: object = None,
        operation: Callable[[int], Any],
    ) -> MutationResult:
        try:
            return control_store.mutate(
                resource_key,
                expected_version=context.expected_version,
                actor=context.actor,
                action=action,
                target=target,
                before=before,
                operation=operation,
            )
        except AdminVersionConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resource_version_conflict",
                    "resource": exc.resource_key,
                    "expected_version": exc.expected,
                    "current_version": exc.current,
                },
            ) from None

    def mutation_payload(result: MutationResult, **payload: object) -> dict[str, object]:
        return {
            "ok": True,
            **payload,
            "resource": result.resource_key,
            "resource_version": result.resource_version,
        }

    def require_changed(changed: bool, status_code: int, detail: str) -> bool:
        if not changed:
            raise HTTPException(status_code=status_code, detail=detail)
        return True

    def versioned(resource_key: str, payload: dict[str, object]) -> dict[str, object]:
        return {
            **payload,
            "api_version": "v1",
            "resource": resource_key,
            "resource_version": control_store.version(resource_key),
        }

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return dashboard_html(prefix, services.version, bool(expected_token))

    @router.get("/assets/{asset_path:path}", include_in_schema=False)
    async def dashboard_asset(asset_path: str) -> FileResponse:
        resolved = admin_asset_path(asset_path)
        if resolved is None:
            raise HTTPException(status_code=404, detail="admin asset not found")
        return FileResponse(
            resolved,
            headers={"Cache-Control": "public, max-age=300"},
        )

    @router.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> Response:
        return Response(
            content=ADMIN_FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.get("/api/resource-versions")
    def resource_versions(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return {
            "api_version": "v1",
            "persistent": control_store.persistent,
            "versions": control_store.versions(),
        }

    @router.get("/api/overview", dependencies=[])
    def overview(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        deliveries = (
            services.delivery_store.stats()
            if services.delivery_store is not None
            else {}
        )
        tasks = (
            len(services.running_tasks.list_all())
            if services.running_tasks is not None
            else 0
        )
        durable_jobs = (
            services.job_store.stats()
            if services.job_store is not None
            else {}
        )
        return {
            "version": services.version,
            "uptime_seconds": max(int(time.time()) - services.started_at, 0),
            "deliveries": deliveries,
            "running_tasks": tasks,
            "durable_jobs": durable_jobs,
            "background_tasks": (
                {
                    "running": list(services.background_tasks.running()),
                    "failures": services.background_tasks.failures(),
                }
                if services.background_tasks is not None
                else {"running": [], "failures": {}}
            ),
            "models": _model_overview(
                services.model_catalog,
                services.llm_gateway,
            ),
            "bridges": (
                services.bridge_state.stats()
                if services.bridge_state is not None
                else {}
            ),
            "browser": (
                services.browser_manager.stats()
                if services.browser_manager is not None
                else {"active_sessions": 0, "persistent_profiles": 0}
            ),
        }

    @router.get("/api/events")
    async def events(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        authorize(authorization)

        async def stream() -> AsyncIterator[str]:
            queue = event_broker.subscribe()
            ready = {
                "sequence": 0,
                "type": "ready",
                "resources": [],
                "timestamp": int(time.time()),
                "versions": control_store.versions(),
            }
            try:
                yield "retry: 2000\n"
                yield f"data: {json.dumps(ready, separators=(',', ':'))}\n\n"
                while not await request.is_disconnected():
                    try:
                        payload = await asyncio.wait_for(
                            queue.get(),
                            timeout=15.0,
                        )
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    encoded = json.dumps(payload, separators=(",", ":"))
                    yield f"data: {encoded}\n\n"
            finally:
                event_broker.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "close",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/databases")
    def databases(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.database is None:
            return {
                "available": False,
                "overall": "unconfigured",
                "checked_at": int(time.time()),
                "writable_node": None,
                "nodes": [],
                "pool": {},
            }
        return services.database.topology_snapshot()

    @router.get("/api/platforms")
    async def platforms(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return {
            "mirrors": (
                services.bridge_router.describe()
                if services.bridge_router is not None
                else []
            ),
            "evidence": (
                services.bridge_state.stats()
                if services.bridge_state is not None
                else {}
            ),
        }

    @router.get("/api/deliveries")
    def deliveries(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.delivery_store is None:
            return {"items": [], "configured": False}
        items = []
        for item in services.delivery_store.recent_summaries(limit=limit):
            payload = asdict(item)
            payload["handle"] = item.handle
            items.append(payload)
        return {"items": items, "configured": True}

    @router.post("/api/deliveries/{delivery_id}/retry")
    async def retry_delivery(
        delivery_id: int,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        result = mutate(
            mutation_info,
            "deliveries",
            action="delivery.retry",
            target=f"delivery#{delivery_id}",
            operation=lambda _version: require_changed(
                bool(
                    services.delivery_store is not None
                    and services.delivery_store.requeue(delivery_id)
                ),
                409,
                "delivery cannot be retried from its current state",
            ),
        )
        event_broker.publish("deliveries", "overview")
        return mutation_payload(result, delivery_id=delivery_id)

    @router.post("/api/deliveries/{delivery_id}/cancel")
    async def cancel_delivery(
        delivery_id: int,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        result = mutate(
            mutation_info,
            "deliveries",
            action="delivery.cancel",
            target=f"delivery#{delivery_id}",
            operation=lambda _version: require_changed(
                bool(
                    services.delivery_store is not None
                    and services.delivery_store.cancel(delivery_id)
                ),
                409,
                "delivery cannot be cancelled from its current state",
            ),
        )
        event_broker.publish("deliveries", "overview")
        return mutation_payload(result, delivery_id=delivery_id)

    @router.get("/api/usage")
    def usage(
        days: int = Query(default=14, ge=1, le=365),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.usage_store is None:
            return {"items": [], "configured": False}
        return {
            "items": services.usage_store.daily_summary(days=days),
            "configured": True,
        }

    @router.get("/api/tasks")
    async def tasks(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.running_tasks is None:
            return {"items": []}
        return {
            "items": [
                {
                    "task_id": item.task_id,
                    "conversation_id": item.conversation_id,
                    "group_id": item.group_id,
                    "user_id": item.user_id,
                    "message_id": item.message_id,
                    "summary": item.summary,
                    "elapsed_seconds": item.elapsed_seconds,
                }
                for item in services.running_tasks.list_all()
            ]
        }

    @router.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        result = mutate(
            mutation_info,
            "tasks",
            action="task.cancel",
            target=task_id,
            operation=lambda _version: require_changed(
                bool(
                    services.running_tasks is not None
                    and services.running_tasks.cancel_any(task_id) is not None
                ),
                404,
                "task not found",
            ),
        )
        event_broker.publish("tasks", "overview")
        return mutation_payload(result, task_id=task_id)

    @router.get("/api/jobs")
    def durable_jobs(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.job_store is None:
            return {"items": [], "counts": {}, "configured": False}
        return {
            "items": [
                {
                    "job_id": item.job_id,
                    "handle": item.handle,
                    "kind": item.kind,
                    "scope_key": item.scope_key,
                    "status": item.status,
                    "priority": item.priority,
                    "attempts": item.attempts,
                    "max_attempts": item.max_attempts,
                    "next_attempt_at": item.next_attempt_at,
                    "lease_owner": item.lease_owner,
                    "lease_until": item.lease_until,
                    "last_error": item.last_error,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "finished_at": item.finished_at,
                }
                for item in services.job_store.recent_summaries(limit=limit)
            ],
            "counts": services.job_store.stats(),
            "configured": True,
        }

    @router.post("/api/jobs/{job_id}/cancel")
    def cancel_durable_job(
        job_id: int,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        result = mutate(
            mutation_info,
            "jobs",
            action="job.cancel",
            target=f"job#{job_id}",
            operation=lambda _version: require_changed(
                bool(
                    services.job_worker.cancel(job_id)
                    if services.job_worker is not None
                    else (
                        services.job_store is not None
                        and services.job_store.cancel(job_id)
                    )
                ),
                404,
                "job is not cancellable",
            ),
        )
        event_broker.publish("jobs", "overview")
        return mutation_payload(result, job_id=job_id)

    @router.post("/api/jobs/{job_id}/retry")
    def retry_durable_job(
        job_id: int,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        result = mutate(
            mutation_info,
            "jobs",
            action="job.retry",
            target=f"job#{job_id}",
            operation=lambda _version: require_changed(
                bool(
                    services.job_store is not None
                    and services.job_store.requeue(job_id)
                ),
                404,
                "job is not retryable",
            ),
        )
        event_broker.publish("jobs", "overview")
        return mutation_payload(result, job_id=job_id)

    @router.get("/api/sandboxes")
    async def sandboxes(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.sandbox_manager is None:
            return {
                "items": [],
                "active_commands": 0,
                "configured": False,
                "available": False,
            }
        try:
            snapshot = await services.sandbox_manager.admin_snapshot()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "items": [],
                "active_commands": 0,
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }

        tasks_by_conversation: dict[str, list[dict[str, object]]] = {}
        if services.running_tasks is not None:
            for item in services.running_tasks.list_all():
                tasks_by_conversation.setdefault(
                    str(item.conversation_id), []
                ).append(
                    {
                        "task_id": item.task_id,
                        "summary": item.summary,
                        "elapsed_seconds": item.elapsed_seconds,
                    }
                )
        for raw_item in snapshot.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            owner = str(raw_item.get("owner", ""))
            raw_item["agent_tasks"] = tasks_by_conversation.get(owner, [])
        return {
            **snapshot,
            "configured": True,
            "available": True,
        }

    @router.get("/api/stickers")
    def stickers(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if not callable(services.sticker_inventory):
            return {
                "counts": {},
                "items": [],
                "configured": False,
                "available": False,
            }
        try:
            inventory = services.sticker_inventory()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "counts": {},
                "items": [],
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {
            **inventory,
            "configured": True,
            "available": True,
        }

    @router.get("/api/tools")
    def tools(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return versioned(
            "tools",
            {
                "items": admin_tool_manifest(),
                "configured": True,
                "available": True,
            },
        )

    @router.put("/api/tools/{tool_name}/enabled")
    def set_tool_permission(
        tool_name: str,
        selection: ToolEnabledRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if tool_name not in TOOL_POLICIES:
            raise HTTPException(status_code=404, detail="工具不存在")

        def update_tool(next_version: int) -> dict[str, object]:
            persisted = control_store.set_tool_override(
                tool_name,
                selection.enabled,
                actor=mutation_info.actor,
                resource_version=next_version,
            )
            set_tool_enabled(tool_name, selection.enabled)
            return persisted

        result = mutate(
            mutation_info,
            "tools",
            action="tool.enabled.set",
            target=tool_name,
            before={"enabled": tool_enabled(tool_name)},
            operation=update_tool,
        )
        event_broker.publish("tools", "overview")
        return mutation_payload(
            result,
            tool_name=tool_name,
            enabled=selection.enabled,
        )

    @router.get("/api/group-models")
    def group_models(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return versioned(
            "groups",
            _group_model_overview(
                services.model_catalog,
                services.model_preferences,
                services.settings,
                services.message_ledger,
                services.user_profiles,
            ),
        )

    @router.put("/api/group-models/{group_id}/default")
    async def set_group_model(
        group_id: int,
        selection: ModelSelectionRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="模型偏好存储不可用")
        profile = _admin_model_profile(services.model_catalog, selection.profile)

        def update_group_model(_version: int) -> dict[str, object]:
            _preserve_admin_model_selections(
                services.model_catalog,
                services.model_preferences,
                services.settings,
                group_id,
            )
            if profile is None:
                services.model_preferences.clear_group_default(group_id)
            else:
                services.model_preferences.set_group_default(group_id, profile.name)
            return {"profile": profile.name if profile is not None else None}

        result = mutate(
            mutation_info,
            "groups",
            action="group.model.set",
            target=str(group_id),
            before={"profile": services.model_preferences.get_group_default(group_id)},
            operation=update_group_model,
        )
        event_broker.publish("groups", "overview")
        return mutation_payload(
            result,
            scope="group",
            group_id=group_id,
            profile=profile.name if profile is not None else None,
        )

    @router.put("/api/group-models/{group_id}/enabled")
    async def set_group_enabled(
        group_id: int,
        selection: GroupEnabledRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="群状态存储不可用")
        result = mutate(
            mutation_info,
            "groups",
            action="group.enabled.set",
            target=str(group_id),
            before={
                "enabled": services.model_preferences.get_group_enabled_override(
                    group_id
                )
            },
            operation=lambda _version: (
                services.model_preferences.set_group_enabled(
                    group_id,
                    selection.enabled,
                )
                or {"enabled": selection.enabled}
            ),
        )
        event_broker.publish("groups", "overview")
        return mutation_payload(
            result,
            group_id=group_id,
            enabled=selection.enabled,
        )

    @router.put("/api/group-models/{group_id}/vision-auto-describe")
    async def set_group_vision_auto_describe(
        group_id: int,
        selection: GroupEnabledRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="群状态存储不可用")
        result = mutate(
            mutation_info,
            "groups",
            action="group.vision-auto-describe.set",
            target=str(group_id),
            before={
                "enabled": (
                    services.model_preferences.get_group_vision_auto_describe_override(
                        group_id
                    )
                )
            },
            operation=lambda _version: (
                services.model_preferences.set_group_vision_auto_describe(
                    group_id,
                    selection.enabled,
                )
                or {"vision_auto_describe": selection.enabled}
            ),
        )
        event_broker.publish("groups", "media", "overview")
        return mutation_payload(
            result,
            group_id=group_id,
            vision_auto_describe=selection.enabled,
        )

    @router.put("/api/group-models/{group_id}/users/{user_id}")
    async def set_group_user_model(
        group_id: int,
        user_id: int,
        selection: ModelSelectionRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0 or user_id <= 0:
            raise HTTPException(status_code=422, detail="群号和 QQ 号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="模型偏好存储不可用")
        profile = _admin_model_profile(services.model_catalog, selection.profile)
        conversation_id = f"group:{group_id}:user:{user_id}"

        def update_user_model(_version: int) -> dict[str, object]:
            if profile is None:
                services.model_preferences.clear(conversation_id)
            else:
                services.model_preferences.set(conversation_id, profile.name)
            return {"profile": profile.name if profile is not None else None}

        result = mutate(
            mutation_info,
            "groups",
            action="group.user-model.set",
            target=f"{group_id}:{user_id}",
            before={
                "profile": services.model_preferences.get_explicit(conversation_id)
                if callable(getattr(services.model_preferences, "get_explicit", None))
                else dict(services.model_preferences.items()).get(conversation_id)
            },
            operation=update_user_model,
        )
        event_broker.publish("groups", "overview")
        return mutation_payload(
            result,
            scope="group_user",
            group_id=group_id,
            user_id=user_id,
            profile=profile.name if profile is not None else None,
        )

    @router.get("/api/media")
    def media(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.media_library is None:
            return {
                "counts": {},
                "items": [],
                "jobs": [],
                "vision": {"counts": {}, "jobs": []},
                "configured": False,
                "available": False,
            }
        try:
            snapshot = services.media_library.admin_snapshot(limit=limit)
            vision = (
                services.vision_worker.admin_snapshot(limit=limit)
                if services.vision_worker is not None
                else {"counts": {}, "jobs": []}
            )
            cleanup = (
                services.media_cleanup.preview()
                if services.media_cleanup is not None
                else {
                    "candidate_count": 0,
                    "candidate_bytes": 0,
                    "ordinary_links": 0,
                    "confirmation_token": "",
                    "recent_runs": [],
                }
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "counts": {},
                "items": [],
                "jobs": [],
                "vision": {"counts": {}, "jobs": []},
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {
            **snapshot,
            "vision": vision,
            "cleanup": cleanup,
            "configured": True,
            "available": True,
            "api_version": "v1",
            "resource": "media",
            "resource_version": control_store.version("media"),
        }

    @router.put("/api/media/{media_id}/review")
    def review_media(
        media_id: int,
        review: MediaReviewRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if media_id <= 0:
            raise HTTPException(status_code=422, detail="媒体编号必须是正整数")
        if review.state not in {"approved", "pending", "rejected"}:
            raise HTTPException(status_code=422, detail="审核状态无效")
        if services.media_library is None or not callable(
            getattr(services.media_library, "set_review_state", None)
        ):
            raise HTTPException(status_code=503, detail="媒体审核服务不可用")
        try:
            result = mutate(
                mutation_info,
                "media",
                action="media.review.set",
                target=f"media#{media_id}",
                before={"requested_state": review.state},
                operation=lambda _version: services.media_library.set_review_state(
                    media_id,
                    review.state,
                ),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:500]) from None
        event_broker.publish("media", "stickers", "overview")
        reviewed = result.value if isinstance(result.value, dict) else {}
        return mutation_payload(result, **reviewed)

    @router.get("/api/sources")
    def sources(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.source_store is None:
            return {
                "counts": {},
                "platforms": [],
                "items": [],
                "configured": False,
                "available": False,
            }
        try:
            snapshot = services.source_store.admin_snapshot(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "counts": {},
                "platforms": [],
                "items": [],
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {
            **snapshot,
            "configured": True,
            "available": True,
        }

    @router.delete("/api/media/legacy-images")
    async def cleanup_legacy_images(
        confirmation: CleanupConfirmationRequest,
        mutation_info: AdminMutationContext = Depends(mutation_context),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.media_cleanup is None:
            raise HTTPException(status_code=503, detail="旧图片清理服务不可用")
        try:
            result = mutate(
                mutation_info,
                "media",
                action="media.legacy-cleanup",
                target="legacy-images",
                operation=lambda _version: services.media_cleanup.apply(
                    confirmation.confirmation_token,
                ),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:500]) from None
        event_broker.publish("media", "overview")
        report = result.value if isinstance(result.value, dict) else {}
        return mutation_payload(result, **report)

    @router.get("/api/context-plans")
    def context_plans(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.turn_journal is None:
            return {"items": [], "configured": False, "available": False}
        try:
            items = services.turn_journal.recent_context_plans(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "items": [],
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {"items": items, "configured": True, "available": True}

    @router.get("/api/traces")
    def traces(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.turn_journal is None:
            return versioned(
                "traces",
                {"items": [], "configured": False, "available": False},
            )
        try:
            items = services.turn_journal.recent_trace_summaries(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return versioned(
                "traces",
                {
                    "items": [],
                    "configured": True,
                    "available": False,
                    "error": str(exc)[:500],
                },
            )
        return versioned(
            "traces",
            {"items": items, "configured": True, "available": True},
        )

    @router.get("/api/audit")
    def audit_log(
        limit: int = Query(default=200, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return {
            "api_version": "v1",
            "persistent": control_store.persistent,
            "items": control_store.audit(limit=limit),
            "versions": control_store.versions(),
        }

    @router.get(
        "/api/turn-replay/{scope_kind}/{scope_native_id}/{turn_ordinal}"
    )
    def turn_replay(
        scope_kind: str,
        scope_native_id: str,
        turn_ordinal: int,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.turn_journal is None:
            raise HTTPException(status_code=503, detail="回合日志不可用")
        if scope_kind not in {"group", "private"} or turn_ordinal <= 0:
            raise HTTPException(status_code=422, detail="回放范围或回合号无效")
        try:
            scope = ConversationScope(
                "onebot-v11",
                scope_kind,  # type: ignore[arg-type]
                scope_native_id,
            )
            steps = services.turn_journal.replay_steps(scope, turn_ordinal)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:500]) from None
        if not steps:
            raise HTTPException(status_code=404, detail="当前范围看不到这个回合")
        return {
            "scope_key": scope.key,
            "turn_handle": f"t#{turn_ordinal}",
            "mode": "audit-only",
            "steps": list(steps),
        }

    @router.get("/api/observability")
    async def observability(
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        process: dict[str, object] = {
            "generated_at": int(time.time()),
            "window": "process",
            "totals": {},
            "models": [],
            "tools": [],
            "deliveries": [],
            "stages": [],
            "fallback_routes": [],
        }
        if services.telemetry is not None:
            try:
                process = services.telemetry.admin_snapshot(
                    running_tasks=services.running_tasks,
                    delivery_store=services.delivery_store,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                pass

        traces: list[dict[str, object]] = []
        traces_available = services.turn_journal is not None
        if traces_available:
            try:
                traces = services.turn_journal.recent_trace_summaries(limit=limit)
            except (OSError, RuntimeError, TypeError, ValueError):
                traces_available = False

        settings = services.settings
        prometheus_url = str(getattr(settings, "prometheus_url", "") or "")
        alertmanager_url = str(getattr(settings, "alertmanager_url", "") or "")
        prometheus, alerts = await asyncio.gather(
            _prometheus_health(prometheus_url),
            _alertmanager_alerts(alertmanager_url),
        )
        return {
            "process": process,
            "prometheus": prometheus,
            "alertmanager": alerts,
            "traces": {
                "configured": services.turn_journal is not None,
                "available": traces_available,
                "items": traces,
            },
        }

    legacy_api_prefix = f"{prefix}/api"
    for route in tuple(router.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith(
            f"{legacy_api_prefix}/"
        ):
            continue
        router.add_api_route(
            "/api/v1" + route.path.removeprefix(legacy_api_prefix),
            route.endpoint,
            methods=sorted(route.methods or {"GET"}),
            name=f"v1_{route.name}",
            response_class=route.response_class,
            status_code=route.status_code,
            include_in_schema=True,
        )

    app.include_router(router)


async def _prometheus_health(base_url: str) -> dict[str, object]:
    if not base_url:
        return {"configured": False, "available": False, "up": None}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/api/v1/query",
                params={"query": 'up{job="kennethbot"}'},
            )
            response.raise_for_status()
            payload = response.json()
        result = payload.get("data", {}).get("result", [])
        values = [
            float(item.get("value", [0, 0])[1])
            for item in result
            if isinstance(item, dict)
        ]
        return {
            "configured": True,
            "available": True,
            "up": bool(values) and all(value >= 1 for value in values),
            "targets": len(values),
            "checked_at": int(time.time()),
        }
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "configured": True,
            "available": False,
            "up": None,
            "error": str(exc)[:240],
            "checked_at": int(time.time()),
        }


async def _alertmanager_alerts(base_url: str) -> dict[str, object]:
    if not base_url:
        return {
            "configured": False,
            "available": False,
            "active_count": 0,
            "items": [],
        }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/api/v2/alerts",
                params={"active": "true", "silenced": "false", "inhibited": "false"},
            )
            response.raise_for_status()
            payload = response.json()
        raw_alerts = payload if isinstance(payload, list) else []
        items = [_safe_alert(item) for item in raw_alerts if isinstance(item, dict)]
        return {
            "configured": True,
            "available": True,
            "active_count": len(items),
            "items": items[:100],
            "checked_at": int(time.time()),
        }
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "configured": True,
            "available": False,
            "active_count": 0,
            "items": [],
            "error": str(exc)[:240],
            "checked_at": int(time.time()),
        }


def _safe_alert(raw: dict[str, Any]) -> dict[str, object]:
    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    annotations = (
        raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
    )
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    return {
        "name": str(labels.get("alertname") or "Alert")[:160],
        "severity": str(labels.get("severity") or "warning")[:32],
        "instance": str(labels.get("instance") or "")[:160],
        "job": str(labels.get("job") or "")[:120],
        "state": str(status.get("state") or "active")[:32],
        "summary": str(annotations.get("summary") or "")[:300],
        "description": str(annotations.get("description") or "")[:500],
        "starts_at": str(raw.get("startsAt") or "")[:64],
        "ends_at": str(raw.get("endsAt") or "")[:64],
        "fingerprint": str(raw.get("fingerprint") or "")[:128],
    }


def _model_overview(
    catalog: Any,
    gateway: Any = None,
) -> dict[str, object]:
    if catalog is None:
        return {"default": "", "profiles": []}
    health = (
        gateway.health_snapshot()
        if gateway is not None and hasattr(gateway, "health_snapshot")
        else {}
    )
    return {
        "default": str(catalog.default_name),
        "profiles": [
            {
                "name": profile.name,
                "provider": profile.provider,
                "protocol": profile.protocol,
                "model": profile.model,
                "configured": profile.configured,
                "fallback_profiles": list(profile.fallback_profiles),
                "health": health.get(
                    profile.name,
                    {
                        "status": "unknown",
                        "consecutive_failures": 0,
                        "retry_after_seconds": 0,
                    },
                ),
                "capabilities": {
                    "tools": profile.capabilities.tools,
                    "streaming": profile.capabilities.streaming,
                    "json_mode": profile.capabilities.json_mode,
                    "vision": profile.capabilities.vision,
                },
            }
            for profile in catalog.profiles
        ],
    }


def _admin_model_profile(catalog: Any, requested: str | None) -> Any:
    normalized = str(requested or "").strip()
    if not normalized:
        return None
    if catalog is None:
        raise HTTPException(status_code=503, detail="模型目录不可用")
    try:
        profile = catalog.resolve(normalized)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="没有这个模型配置") from None
    if not profile.configured:
        raise HTTPException(status_code=409, detail="这个模型还没有配置可用的密钥")
    return profile


def _preserve_admin_model_selections(
    catalog: Any,
    preferences: Any,
    settings: Any,
    group_id: int,
) -> None:
    """Keep the admin lane independent when the shared member model changes."""
    admin_user_ids = set(
        getattr(settings, "admin_user_ids", set()) or set()
    )
    if not admin_user_ids or catalog is None:
        return

    dynamic_default = (
        preferences.get_group_default(group_id)
        if hasattr(preferences, "get_group_default")
        else None
    )
    deployed_default = (
        (getattr(settings, "group_model_profiles", {}) or {}).get(group_id)
        if settings is not None
        else None
    )
    effective_default = catalog.resolve_preference(
        dynamic_default or deployed_default
    )
    stored_keys = {
        str(conversation_id)
        for conversation_id, _stored in preferences.items()
    }
    for user_id in admin_user_ids:
        conversation_id = f"group:{group_id}:user:{int(user_id)}"
        if conversation_id not in stored_keys:
            preferences.set(conversation_id, effective_default.name)


_GROUP_CONVERSATION_PATTERN = re.compile(r"^group:(\d+):user:(\d+)$")
_GROUP_SETTING_PATTERN = re.compile(
    r"^group:(\d+):(?:default|enabled|vision-auto-describe)$"
)


def _group_model_overview(
    catalog: Any,
    preferences: Any,
    settings: Any,
    message_ledger: Any,
    user_profiles: Any = None,
) -> dict[str, object]:
    if catalog is None:
        return {"default": {}, "items": [], "configured": False}

    default_profile = catalog.default
    group_ids: set[int] = set()
    if settings is not None:
        group_ids.update(getattr(settings, "enabled_groups", set()) or set())
        group_ids.update(getattr(settings, "disabled_groups", set()) or set())
        group_ids.update(
            (getattr(settings, "group_model_profiles", {}) or {}).keys()
        )

    if message_ledger is not None:
        try:
            for scope in message_ledger.list_scopes():
                if scope.kind != "group" or scope.platform != "onebot-v11":
                    continue
                try:
                    group_ids.add(int(scope.native_conversation_id))
                except (TypeError, ValueError):
                    continue
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    if user_profiles is not None:
        try:
            group_ids.update(user_profiles.group_ids())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass

    overrides_by_group: dict[int, dict[int, dict[str, object]]] = {}
    preference_items = preferences.items() if preferences is not None else []
    for conversation_id, stored_preference in preference_items:
        match = _GROUP_CONVERSATION_PATTERN.fullmatch(str(conversation_id))
        if match is None:
            setting_match = _GROUP_SETTING_PATTERN.fullmatch(str(conversation_id))
            if setting_match is not None:
                group_ids.add(int(setting_match.group(1)))
            continue
        group_id = int(match.group(1))
        user_id = int(match.group(2))
        group_ids.add(group_id)
        resolved = catalog.resolve_preference(str(stored_preference))
        direct = catalog.try_resolve(str(stored_preference))
        recognized = direct is not None or any(
            profile.model == str(stored_preference)
            for profile in catalog.profiles
        )
        overrides_by_group.setdefault(group_id, {})[user_id] = {
            "user_id": user_id,
            "stored_preference": str(stored_preference),
            "profile": resolved.name,
            "provider": resolved.provider,
            "model": resolved.model,
            "recognized": recognized,
        }

    rows: list[dict[str, object]] = []
    admin_user_ids = set(
        getattr(settings, "admin_user_ids", set()) or set()
    )
    for group_id in sorted(group_ids):
        overrides = sorted(
            overrides_by_group.get(group_id, {}).values(),
            key=lambda item: int(item["user_id"]),
        )
        deployed_enabled = (
            bool(settings.is_group_enabled(group_id))
            if settings is not None
            else True
        )
        enabled_override = None
        if preferences is not None and hasattr(
            preferences,
            "get_group_enabled_override",
        ):
            enabled_override = preferences.get_group_enabled_override(group_id)
        enabled = (
            enabled_override
            if enabled_override is not None
            else deployed_enabled
        )
        vision_override = None
        if preferences is not None and hasattr(
            preferences,
            "get_group_vision_auto_describe_override",
        ):
            vision_override = preferences.get_group_vision_auto_describe_override(
                group_id
            )
        vision_deployed = bool(
            getattr(settings, "vision_auto_describe", False)
            if settings is not None
            else False
        )
        vision_auto_describe = (
            vision_override if vision_override is not None else vision_deployed
        )
        deployed_group_default = (
            (getattr(settings, "group_model_profiles", {}) or {}).get(group_id)
            if settings is not None
            else None
        )
        dynamic_group_default = None
        if preferences is not None and hasattr(preferences, "get_group_default"):
            dynamic_group_default = preferences.get_group_default(group_id)
        stored_group_default = dynamic_group_default or deployed_group_default
        group_default = catalog.resolve_preference(stored_group_default)

        observed_members: list[dict[str, object]] = []
        if user_profiles is not None:
            try:
                observed_members = user_profiles.members(group_id)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                observed_members = []
        members_by_id = {
            int(item["user_id"]): dict(item)
            for item in observed_members
            if int(item.get("user_id", 0)) > 0
        }
        for user_id in {*admin_user_ids, *overrides_by_group.get(group_id, {})}:
            members_by_id.setdefault(
                int(user_id),
                {
                    "user_id": int(user_id),
                    "nickname": "",
                    "card": "",
                    "display_name": f"QQ {int(user_id)}",
                    "last_seen": 0,
                },
            )

        classified: list[dict[str, object]] = []
        for user_id, member in members_by_id.items():
            explicit = overrides_by_group.get(group_id, {}).get(user_id)
            effective = (
                catalog.resolve_preference(str(explicit["stored_preference"]))
                if explicit is not None
                else group_default
            )
            classified.append(
                {
                    **member,
                    "is_admin": user_id in admin_user_ids,
                    "explicit_profile": (
                        str(explicit["profile"]) if explicit is not None else None
                    ),
                    "effective_profile": effective.name,
                    "effective_provider": effective.provider,
                    "effective_model": effective.model,
                }
            )
        classified.sort(
            key=lambda item: (
                not bool(item["is_admin"]),
                -int(item.get("last_seen", 0)),
                int(item["user_id"]),
            )
        )
        rows.append(
            {
                "group_id": group_id,
                "enabled": enabled,
                "enabled_override": enabled_override,
                "enabled_source": (
                    "dashboard" if enabled_override is not None else "deployment"
                ),
                "vision_auto_describe": vision_auto_describe,
                "vision_auto_describe_override": vision_override,
                "vision_auto_describe_source": (
                    "dashboard" if vision_override is not None else "deployment"
                ),
                "default_profile": group_default.name,
                "default_provider": group_default.provider,
                "default_model": group_default.model,
                "group_override": stored_group_default is not None,
                "dynamic_group_profile": dynamic_group_default,
                "deployed_group_profile": deployed_group_default,
                "group_default_source": (
                    "dashboard"
                    if dynamic_group_default is not None
                    else "deployment"
                    if deployed_group_default is not None
                    else "global"
                ),
                "overrides": overrides,
                "admins": [item for item in classified if item["is_admin"]],
                "members": [item for item in classified if not item["is_admin"]],
            }
        )

    return {
        "default": {
            "profile": default_profile.name,
            "provider": default_profile.provider,
            "model": default_profile.model,
        },
        "profiles": _model_overview(catalog)["profiles"],
        "items": rows,
        "configured": True,
    }
