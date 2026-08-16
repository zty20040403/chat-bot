from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from .admin_dashboard import ADMIN_FAVICON_SVG, dashboard_html


ADMIN_EVENT_STREAM_LEASE_SECONDS = 10


@dataclass(frozen=True)
class AdminServices:
    version: str
    started_at: int
    delivery_store: Any = None
    usage_store: Any = None
    running_tasks: Any = None
    bridge_router: Any = None
    bridge_state: Any = None
    browser_manager: Any = None
    background_tasks: Any = None
    model_catalog: Any = None
    model_preferences: Any = None
    user_profiles: Any = None
    message_ledger: Any = None
    settings: Any = None
    sandbox_manager: Any = None
    sticker_inventory: Any = None
    media_library: Any = None
    media_cleanup: Any = None
    vision_worker: Any = None
    turn_journal: Any = None
    database: Any = None


class ModelSelectionRequest(BaseModel):
    profile: str | None = None


class GroupEnabledRequest(BaseModel):
    enabled: bool


class CleanupConfirmationRequest(BaseModel):
    confirmation_token: str


class AdminEventBroker:
    def __init__(self) -> None:
        self._sequence = 0
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

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
    event_broker = AdminEventBroker()

    def authorize(authorization: Optional[str] = Header(default=None)) -> None:
        if not expected_token:
            return
        supplied = ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return dashboard_html(prefix, services.version, bool(expected_token))

    @router.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> Response:
        return Response(
            content=ADMIN_FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

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
        return {
            "version": services.version,
            "uptime_seconds": max(int(time.time()) - services.started_at, 0),
            "deliveries": deliveries,
            "running_tasks": tasks,
            "background_tasks": (
                {
                    "running": list(services.background_tasks.running()),
                    "failures": services.background_tasks.failures(),
                }
                if services.background_tasks is not None
                else {"running": [], "failures": {}}
            ),
            "models": _model_overview(services.model_catalog),
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
            deadline = time.monotonic() + ADMIN_EVENT_STREAM_LEASE_SECONDS
            ready = {
                "sequence": 0,
                "type": "ready",
                "resources": [],
                "timestamp": int(time.time()),
            }
            try:
                yield "retry: 2000\n"
                yield f"data: {json.dumps(ready, separators=(',', ':'))}\n\n"
                while (
                    time.monotonic() < deadline
                    and not await request.is_disconnected()
                ):
                    remaining = max(deadline - time.monotonic(), 0.1)
                    try:
                        payload = await asyncio.wait_for(
                            queue.get(),
                            timeout=min(2.0, remaining),
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
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.delivery_store is not None
            and services.delivery_store.requeue(delivery_id)
        )
        if not changed:
            raise HTTPException(
                status_code=409,
                detail="delivery cannot be retried from its current state",
            )
        event_broker.publish("deliveries", "overview")
        return {"ok": True, "delivery_id": delivery_id}

    @router.post("/api/deliveries/{delivery_id}/cancel")
    async def cancel_delivery(
        delivery_id: int,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.delivery_store is not None
            and services.delivery_store.cancel(delivery_id)
        )
        if not changed:
            raise HTTPException(
                status_code=409,
                detail="delivery cannot be cancelled from its current state",
            )
        event_broker.publish("deliveries", "overview")
        return {"ok": True, "delivery_id": delivery_id}

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
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.running_tasks is not None
            and services.running_tasks.cancel_any(task_id) is not None
        )
        if not changed:
            raise HTTPException(status_code=404, detail="task not found")
        event_broker.publish("tasks", "overview")
        return {"ok": True, "task_id": task_id}

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

    @router.get("/api/group-models")
    def group_models(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return _group_model_overview(
            services.model_catalog,
            services.model_preferences,
            services.settings,
            services.message_ledger,
            services.user_profiles,
        )

    @router.put("/api/group-models/{group_id}/default")
    async def set_group_model(
        group_id: int,
        selection: ModelSelectionRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="模型偏好存储不可用")
        profile = _admin_model_profile(services.model_catalog, selection.profile)
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
        event_broker.publish("groups", "overview")
        return {
            "ok": True,
            "scope": "group",
            "group_id": group_id,
            "profile": profile.name if profile is not None else None,
        }

    @router.put("/api/group-models/{group_id}/enabled")
    async def set_group_enabled(
        group_id: int,
        selection: GroupEnabledRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="群状态存储不可用")
        services.model_preferences.set_group_enabled(
            group_id,
            selection.enabled,
        )
        event_broker.publish("groups", "overview")
        return {
            "ok": True,
            "group_id": group_id,
            "enabled": selection.enabled,
        }

    @router.put("/api/group-models/{group_id}/vision-auto-describe")
    async def set_group_vision_auto_describe(
        group_id: int,
        selection: GroupEnabledRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="群号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="群状态存储不可用")
        services.model_preferences.set_group_vision_auto_describe(
            group_id,
            selection.enabled,
        )
        event_broker.publish("groups", "media", "overview")
        return {
            "ok": True,
            "group_id": group_id,
            "vision_auto_describe": selection.enabled,
        }

    @router.put("/api/group-models/{group_id}/users/{user_id}")
    async def set_group_user_model(
        group_id: int,
        user_id: int,
        selection: ModelSelectionRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if group_id <= 0 or user_id <= 0:
            raise HTTPException(status_code=422, detail="群号和 QQ 号必须是正整数")
        if services.model_preferences is None:
            raise HTTPException(status_code=503, detail="模型偏好存储不可用")
        profile = _admin_model_profile(services.model_catalog, selection.profile)
        conversation_id = f"group:{group_id}:user:{user_id}"
        if profile is None:
            services.model_preferences.clear(conversation_id)
        else:
            services.model_preferences.set(conversation_id, profile.name)
        event_broker.publish("groups", "overview")
        return {
            "ok": True,
            "scope": "group_user",
            "group_id": group_id,
            "user_id": user_id,
            "profile": profile.name if profile is not None else None,
        }

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
        }

    @router.delete("/api/media/legacy-images")
    async def cleanup_legacy_images(
        confirmation: CleanupConfirmationRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.media_cleanup is None:
            raise HTTPException(status_code=503, detail="旧图片清理服务不可用")
        try:
            report = services.media_cleanup.apply(
                confirmation.confirmation_token,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:500]) from None
        event_broker.publish("media", "overview")
        return {"ok": True, **report}

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

    app.include_router(router)


def _model_overview(catalog: Any) -> dict[str, object]:
    if catalog is None:
        return {"default": "", "profiles": []}
    return {
        "default": str(catalog.default_name),
        "profiles": [
            {
                "name": profile.name,
                "provider": profile.provider,
                "protocol": profile.protocol,
                "model": profile.model,
                "configured": profile.configured,
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
