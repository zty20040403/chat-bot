from __future__ import annotations

import hmac
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .admin_dashboard import dashboard_html


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
    turn_journal: Any = None


class ModelSelectionRequest(BaseModel):
    profile: str | None = None


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

    @router.get("/api/overview", dependencies=[])
    async def overview(
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
    async def deliveries(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.delivery_store is None:
            return {"items": [], "configured": False}
        items = []
        for item in services.delivery_store.recent(limit=limit):
            payload = asdict(item)
            payload.pop("body", None)
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
        return {"ok": True, "delivery_id": delivery_id}

    @router.get("/api/usage")
    async def usage(
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
    async def stickers(
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
    async def group_models(
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
        if profile is None:
            services.model_preferences.clear_group_default(group_id)
        else:
            services.model_preferences.set_group_default(group_id, profile.name)
        return {
            "ok": True,
            "scope": "group",
            "group_id": group_id,
            "profile": profile.name if profile is not None else None,
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
        return {
            "ok": True,
            "scope": "group_user",
            "group_id": group_id,
            "user_id": user_id,
            "profile": profile.name if profile is not None else None,
        }

    @router.get("/api/media")
    async def media(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.media_library is None:
            return {
                "counts": {},
                "items": [],
                "jobs": [],
                "configured": False,
                "available": False,
            }
        try:
            snapshot = services.media_library.admin_snapshot(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "counts": {},
                "items": [],
                "jobs": [],
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {**snapshot, "configured": True, "available": True}

    @router.get("/api/context-plans")
    async def context_plans(
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


_GROUP_CONVERSATION_PATTERN = re.compile(r"^group:(\d+):user:(\d+)$")


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
        enabled = (
            bool(settings.is_group_enabled(group_id))
            if settings is not None
            else True
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
