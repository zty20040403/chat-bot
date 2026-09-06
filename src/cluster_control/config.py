from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _validate_url(value: str, name: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query, or fragment")
    return normalized


def _inventory(raw: str) -> tuple[dict[str, object], ...]:
    if not raw.strip():
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("KC_INVENTORY_JSON must be valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError("KC_INVENTORY_JSON must be a JSON array")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Every inventory item must be an object")
        host_id = str(item.get("host_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host_id):
            raise ValueError(f"Invalid inventory host_id: {host_id!r}")
        if host_id in seen:
            raise ValueError(f"Duplicate inventory host_id: {host_id}")
        roles = item.get("roles", [])
        readable_units = item.get("readable_units", [])
        for flag in ("observe", "operate", "compute"):
            if flag in item and not isinstance(item[flag], bool):
                raise ValueError(f"Invalid {flag} flag for inventory host {host_id}")
        if not isinstance(roles, list) or not all(
            isinstance(role, str) and role.strip() for role in roles
        ):
            raise ValueError(f"Invalid roles for inventory host {host_id}")
        if not isinstance(readable_units, list) or not all(
            isinstance(unit, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service", unit)
            for unit in readable_units
        ):
            raise ValueError(f"Invalid readable_units for inventory host {host_id}")
        seen.add(host_id)
        result.append(
            {
                "host_id": host_id,
                "label": str(item.get("label") or host_id).strip()[:80],
                "architecture": str(item.get("architecture") or "unknown").strip()[:40],
                "site": str(item.get("site") or "unknown").strip()[:80],
                "maintainer": str(item.get("maintainer") or "unknown").strip()[:80],
                "permission_source": str(
                    item.get("permission_source") or "unconfirmed"
                ).strip()[:120],
                "roles": [str(role).strip()[:80] for role in roles],
                "observe": bool(item.get("observe", False)),
                "operate": bool(item.get("operate", False)),
                "compute": bool(item.get("compute", False)),
                "readable_units": list(dict.fromkeys(readable_units)),
            }
        )
    return tuple(result)


@dataclass(frozen=True)
class ClusterControlSettings:
    host: str
    port: int
    api_token_file: str
    maxops_enabled: bool
    maxops_base_url: str
    maxops_token_file: str
    maxops_timeout_seconds: int
    cache_seconds: int
    inventory: tuple[dict[str, object], ...]
    postgres_dsn: str
    postgres_schema: str
    postgres_pool_min_size: int
    postgres_pool_max_size: int
    postgres_pool_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "ClusterControlSettings":
        schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("AI_POSTGRES_SCHEMA is not a valid identifier")
        min_size = _int("AI_POSTGRES_POOL_MIN_SIZE", 1, 1, 20)
        max_size = _int("AI_POSTGRES_POOL_MAX_SIZE", 5, min_size, 100)
        return cls(
            host=os.getenv("KC_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int("KC_PORT", 8091, 1, 65535),
            api_token_file=os.getenv("KC_API_TOKEN_FILE", "").strip(),
            maxops_enabled=_bool("KC_MAXOPS_ENABLED", False),
            maxops_base_url=_validate_url(
                os.getenv("KC_MAXOPS_BASE_URL", ""), "KC_MAXOPS_BASE_URL"
            ),
            maxops_token_file=os.getenv("KC_MAXOPS_TOKEN_FILE", "").strip(),
            maxops_timeout_seconds=_int("KC_MAXOPS_TIMEOUT_SECONDS", 15, 1, 30),
            cache_seconds=_int("KC_CACHE_SECONDS", 20, 1, 300),
            inventory=_inventory(os.getenv("KC_INVENTORY_JSON", "")),
            postgres_dsn=os.getenv("AI_POSTGRES_DSN", "").strip(),
            postgres_schema=schema,
            postgres_pool_min_size=min_size,
            postgres_pool_max_size=max_size,
            postgres_pool_timeout_seconds=_int(
                "AI_POSTGRES_POOL_TIMEOUT_SECONDS", 10, 1, 60
            ),
        )

    def validate(self) -> None:
        if not self.api_token_file:
            raise ValueError("KC_API_TOKEN_FILE is required")
        if not self.postgres_dsn:
            raise ValueError("AI_POSTGRES_DSN is required")
        if self.maxops_enabled and (
            not self.maxops_base_url or not self.maxops_token_file
        ):
            raise ValueError(
                "KC_MAXOPS_BASE_URL and KC_MAXOPS_TOKEN_FILE are required "
                "when MaxOps is enabled"
            )
