from __future__ import annotations

from typing import Any, Protocol

from .admin import AdminServices, register_admin
from .bridges import register_bridge_routes
from .config import Settings
from .runtime import AppContext
from .stickers import sticker_inventory


class BootstrapLogger(Protocol):
    def error(self, message: object, *args: object, **kwargs: object) -> object: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> object: ...


def register_http_surfaces(
    app: Any,
    context: AppContext,
    *,
    settings: Settings,
    version: str,
    logger: BootstrapLogger,
) -> None:
    if context.bridge_manager is not None:
        try:
            register_bridge_routes(
                app,
                context.bridge_manager,
                matrix_appservice_token=settings.matrix_appservice_token,
                bluebubbles_webhook_token=settings.imessage_webhook_token,
                bluebubbles_chat_guid=settings.imessage_chat_guid,
                bluebubbles_bot_handle=settings.imessage_bot_handle,
                path=settings.bridge_path,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                f"Cross-platform bridge routes could not be registered: {exc}"
            )

    if not settings.admin_enabled:
        return
    try:
        register_admin(
            app,
            AdminServices(
                version=version,
                started_at=context.started_at,
                delivery_store=context.delivery_store,
                usage_store=context.usage_store,
                running_tasks=context.running_tasks,
                bridge_router=context.bridge_router,
                bridge_state=context.mirror_state,
                browser_manager=context.browser_manager,
                background_tasks=context.background_tasks,
                model_catalog=context.model_catalog,
                model_preferences=context.model_preferences,
                user_profiles=context.user_profiles,
                message_ledger=context.message_ledger,
                settings=context.settings,
                sandbox_manager=context.sandbox_manager,
                sticker_inventory=sticker_inventory,
                media_library=context.media_library,
                turn_journal=context.turn_journal,
            ),
            path=settings.admin_path,
            token=settings.admin_token,
        )
        if not settings.admin_token:
            logger.warning(
                "Admin dashboard is enabled without a token; keep HOST on loopback."
            )
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.error(f"Admin dashboard could not be registered: {exc}")
