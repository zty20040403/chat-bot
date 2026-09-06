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
        operable_units = item.get("operable_units", [])
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
        if not isinstance(operable_units, list) or not all(
            isinstance(unit, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service", unit)
            for unit in operable_units
        ):
            raise ValueError(f"Invalid operable_units for inventory host {host_id}")
        if any(unit not in readable_units for unit in operable_units):
            raise ValueError(
                f"operable_units must be a subset of readable_units for {host_id}"
            )
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
                "operable_units": list(dict.fromkeys(operable_units)),
            }
        )
    return tuple(result)


def _worker_identities(raw: str) -> tuple[dict[str, str], ...]:
    if not raw.strip():
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("KC_WORKER_IDENTITIES_JSON must be valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError("KC_WORKER_IDENTITIES_JSON must be a JSON array")
    result: list[dict[str, str]] = []
    worker_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Every worker identity must be an object")
        worker_id = str(item.get("worker_id") or "").strip()
        host_id = str(item.get("host_id") or "").strip()
        token_file = str(item.get("token_file") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", worker_id):
            raise ValueError("Invalid worker_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host_id):
            raise ValueError("Invalid worker host_id")
        if worker_id in worker_ids or not token_file:
            raise ValueError("Duplicate worker_id or missing worker token file")
        worker_ids.add(worker_id)
        result.append(
            {"worker_id": worker_id, "host_id": host_id, "token_file": token_file}
        )
    return tuple(result)


def _diagnostic_targets(raw: str) -> tuple[dict[str, str], ...]:
    if not raw.strip():
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("KC_DIAGNOSTIC_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError("KC_DIAGNOSTIC_TARGETS_JSON must be a JSON array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Every diagnostic target must be an object")
        target_id = str(item.get("target_id") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", target_id):
            raise ValueError(f"Invalid diagnostic target_id: {target_id!r}")
        if target_id in seen:
            raise ValueError(f"Duplicate diagnostic target_id: {target_id}")
        kind = str(item.get("kind") or "http").strip().lower()
        if kind not in {"model", "admin", "service"}:
            raise ValueError(f"Invalid diagnostic target kind: {kind!r}")
        url = _validate_url(str(item.get("url") or ""), "diagnostic target URL")
        if not url:
            raise ValueError(f"Diagnostic target {target_id!r} requires a URL")
        observer_host = str(item.get("observer_host") or "h610").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", observer_host):
            raise ValueError(
                f"Invalid diagnostic observer_host: {observer_host!r}"
            )
        seen.add(target_id)
        result.append(
            {
                "target_id": target_id,
                "label": str(item.get("label") or target_id).strip()[:80],
                "kind": kind,
                "url": url,
                "observer_host": observer_host,
            }
        )
    return tuple(result)


@dataclass(frozen=True)
class ClusterControlSettings:
    host: str
    port: int
    local_host_id: str
    api_token_file: str
    maxops_enabled: bool
    maxops_base_url: str
    maxops_token_file: str
    maxops_timeout_seconds: int
    cache_seconds: int
    inventory: tuple[dict[str, object], ...]
    diagnostic_targets: tuple[dict[str, str], ...]
    worker_identities: tuple[dict[str, str], ...]
    artifact_dir: str
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
        local_host_id = os.getenv("KC_LOCAL_HOST_ID", "h610").strip() or "h610"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", local_host_id):
            raise ValueError("KC_LOCAL_HOST_ID is not a valid host identifier")
        return cls(
            host=os.getenv("KC_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int("KC_PORT", 8091, 1, 65535),
            local_host_id=local_host_id,
            api_token_file=os.getenv("KC_API_TOKEN_FILE", "").strip(),
            maxops_enabled=_bool("KC_MAXOPS_ENABLED", False),
            maxops_base_url=_validate_url(
                os.getenv("KC_MAXOPS_BASE_URL", ""), "KC_MAXOPS_BASE_URL"
            ),
            maxops_token_file=os.getenv("KC_MAXOPS_TOKEN_FILE", "").strip(),
            maxops_timeout_seconds=_int("KC_MAXOPS_TIMEOUT_SECONDS", 15, 1, 30),
            cache_seconds=_int("KC_CACHE_SECONDS", 20, 1, 300),
            inventory=_inventory(os.getenv("KC_INVENTORY_JSON", "")),
            diagnostic_targets=_diagnostic_targets(
                os.getenv("KC_DIAGNOSTIC_TARGETS_JSON", "")
            ),
            worker_identities=_worker_identities(
                os.getenv("KC_WORKER_IDENTITIES_JSON", "")
            ),
            artifact_dir=os.getenv(
                "KC_ARTIFACT_DIR", "/var/lib/kennethbot-cluster-control/artifacts"
            ).strip(),
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
        if self.inventory and self.local_host_id not in {
            str(item.get("host_id") or "") for item in self.inventory
        }:
            raise ValueError("KC_LOCAL_HOST_ID must exist in KC_INVENTORY_JSON")
        inventory = {str(item.get("host_id") or ""): item for item in self.inventory}
        for worker in self.worker_identities:
            host = inventory.get(worker["host_id"])
            if host is None or not host.get("compute"):
                raise ValueError(
                    f"Worker {worker['worker_id']} requires a compute-enabled inventory host"
                )
        if not self.artifact_dir:
            raise ValueError("KC_ARTIFACT_DIR is required")
