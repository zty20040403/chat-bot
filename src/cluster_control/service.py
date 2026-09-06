from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram

from .capabilities import (
    BINDING_BY_CAPABILITY,
    capability_manifest,
    operation_compatible,
)
from .contracts import FleetError, FleetQueryResult, FleetStatus
from .adapters.maxops import MaxOpsClient, MaxOpsError, MaxOpsOperation
from .storage import FleetProjectionStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CacheEntry:
    result: FleetQueryResult
    stored_at: float


class FleetControlService:
    def __init__(
        self,
        maxops: MaxOpsClient | None,
        *,
        store: FleetProjectionStore | None = None,
        inventory: tuple[dict[str, object], ...] = (),
        cache_seconds: int = 20,
    ) -> None:
        self.maxops = maxops
        self.store = store
        self.inventory = inventory
        self._inventory_by_host = {
            str(item["host_id"]): item
            for item in inventory
            if item.get("host_id")
        }
        self.cache_seconds = min(max(int(cache_seconds), 1), 300)
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._last_success_at: int | None = None
        self.metrics_registry = CollectorRegistry(auto_describe=True)
        self._query_counter = Counter(
            "kennethbot_cluster_queries_total",
            "Read-only cluster queries handled by the control service.",
            ("capability", "status", "source"),
            registry=self.metrics_registry,
        )
        self._query_duration = Histogram(
            "kennethbot_cluster_query_duration_seconds",
            "End-to-end latency of read-only cluster queries.",
            ("capability",),
            registry=self.metrics_registry,
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30),
        )

    async def close(self) -> None:
        if self.maxops is not None:
            await self.maxops.close()
        if self.store is not None:
            await asyncio.to_thread(self.store.close)

    @staticmethod
    def _cache_key(operation: str, params: dict[str, Any]) -> tuple[str, str]:
        import json

        return operation, json.dumps(params, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _observed_at(data: Any) -> int | None:
        if not isinstance(data, dict):
            return None
        for key in ("observed_at", "sample_time", "timestamp"):
            value = data.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                return int(parsed.timestamp())
        return None

    @staticmethod
    def _target_key(params: dict[str, Any]) -> str:
        host = str(params.get("host") or "").strip()
        unit = str(params.get("unit") or "").strip()
        return ":".join(item for item in (host, unit) if item) or "fleet"

    async def _record_backend(
        self,
        *,
        state: str,
        operations: list[str],
        checked_at: int,
        error_code: str = "",
    ) -> None:
        if self.store is None:
            return
        try:
            await asyncio.to_thread(
                self.store.record_backend,
                state=state,
                operations=operations,
                checked_at=checked_at,
                last_success_at=self._last_success_at,
                error_code=error_code,
            )
        except Exception as exc:
            logger.warning("Could not persist MaxOps backend state: %s", exc)

    async def _record_observation(
        self,
        result: FleetQueryResult,
        params: dict[str, Any],
        *,
        sensitive: bool,
    ) -> None:
        if self.store is None:
            return
        try:
            await asyncio.to_thread(
                self.store.record_observation,
                operation=result.operation,
                params=params,
                target_key=self._target_key(params),
                status=result.status.value,
                payload=result.data,
                observed_at=result.observed_at,
                received_at=result.received_at,
                expires_at=result.expires_at,
                duration_ms=result.duration_ms,
                error_code=result.error.code if result.error else "",
                sensitive=sensitive,
            )
        except Exception as exc:
            logger.warning("Could not persist fleet observation: %s", exc)

    async def _load_stale_projection(
        self,
        operation: str,
        params: dict[str, Any],
    ) -> _CacheEntry | None:
        if self.store is None:
            return None
        try:
            stored = await asyncio.to_thread(
                self.store.latest,
                operation=operation,
                params=params,
            )
        except Exception as exc:
            logger.warning("Could not load fleet projection: %s", exc)
            return None
        if stored is None:
            return None
        result = FleetQueryResult(
            operation=operation,
            status=FleetStatus.FRESH,
            source_backend="maxops",
            received_at=int(stored["received_at"]),
            observed_at=(
                int(stored["observed_at"])
                if stored.get("observed_at") is not None
                else None
            ),
            expires_at=(
                int(stored["expires_at"])
                if stored.get("expires_at") is not None
                else None
            ),
            duration_ms=(
                int(stored["duration_ms"])
                if stored.get("duration_ms") is not None
                else None
            ),
            data=self._scope_payload(operation, stored.get("data")),
        )
        return _CacheEntry(result=result, stored_at=0.0)

    async def operations(self) -> dict[str, MaxOpsOperation]:
        checked_at = int(time.time())
        if self.maxops is None:
            await self._record_backend(
                state="disabled", operations=[], checked_at=checked_at
            )
            return {}
        try:
            catalog = await self.maxops.operations()
        except MaxOpsError as exc:
            await self._record_backend(
                state="unavailable",
                operations=[],
                checked_at=checked_at,
                error_code=exc.code,
            )
            raise
        operations = {item.name: item for item in catalog}
        names = set(operations)
        self._last_success_at = checked_at
        await self._record_backend(
            state="online", operations=sorted(names), checked_at=checked_at
        )
        return operations

    async def capabilities(self) -> dict[str, Any]:
        try:
            operations = await self.operations()
            error = None
        except MaxOpsError as exc:
            operations = {}
            error = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            }
        return {
            "backend": "maxops",
            "catalog_version": 1,
            "checked_at": int(time.time()),
            "capabilities": capability_manifest(operations),
            "error": error,
        }

    def _observable_hosts(self) -> set[str]:
        return {
            host_id
            for host_id, policy in self._inventory_by_host.items()
            if policy.get("observe", False) is True
        }

    def _authorize_target(
        self,
        operation: str,
        params: dict[str, Any],
    ) -> FleetError | None:
        observable_hosts = self._observable_hosts()
        if operation in {"fleet.overview", "units.failed", "alerts.active"}:
            if observable_hosts:
                return None
            return FleetError(
                "target_not_allowed",
                "No fleet hosts are enabled for observation",
            )

        host = str(params.get("host") or "").strip()
        policy = self._inventory_by_host.get(host)
        if policy is None or policy.get("observe", False) is not True:
            return FleetError(
                "target_not_allowed",
                "This host is not enabled for observation",
            )

        if operation in {"units.status", "units.logs"}:
            unit = str(params.get("unit") or "").strip()
            readable_units = policy.get("readable_units", [])
            if not isinstance(readable_units, list) or unit not in readable_units:
                return FleetError(
                    "target_not_allowed",
                    "This service is not enabled for observation",
                )
        return None

    def _scope_payload(self, operation: str, payload: Any) -> Any:
        allowed_hosts = self._observable_hosts()
        if not isinstance(payload, dict):
            return payload
        scoped = dict(payload)
        if operation in {"fleet.overview", "units.failed"}:
            hosts = payload.get("hosts")
            if isinstance(hosts, list):
                scoped["hosts"] = [
                    item
                    for item in hosts
                    if isinstance(item, dict)
                    and str(item.get("host") or item.get("host_id") or "")
                    in allowed_hosts
                ]
        elif operation == "alerts.active":
            alerts = payload.get("alerts")
            if isinstance(alerts, list):
                scoped["alerts"] = [
                    item
                    for item in alerts
                    if isinstance(item, dict)
                    and isinstance(item.get("labels"), dict)
                    and str(item["labels"].get("instance") or "") in allowed_hosts
                ]
        return scoped

    def _validated_payload(
        self,
        operation: str,
        params: dict[str, Any],
        payload: Any,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise MaxOpsError(
                "invalid_response",
                "MaxOps returned an invalid operation result",
            )
        if operation in {"fleet.overview", "units.failed"}:
            if not isinstance(payload.get("hosts"), list):
                raise MaxOpsError(
                    "invalid_response",
                    "MaxOps returned an invalid host collection",
                )
        elif operation == "alerts.active":
            if not isinstance(payload.get("alerts"), list):
                raise MaxOpsError(
                    "invalid_response",
                    "MaxOps returned an invalid alert collection",
                )
        else:
            expected_host = str(params.get("host") or "")
            if payload.get("host") != expected_host:
                raise MaxOpsError(
                    "invalid_response",
                    "MaxOps returned data for a different host",
                )
            if operation == "units.status":
                unit = payload.get("unit")
                if not isinstance(unit, dict) or unit.get("unit") != params.get("unit"):
                    raise MaxOpsError(
                        "invalid_response",
                        "MaxOps returned data for a different service",
                    )
            elif operation == "units.logs":
                if (
                    payload.get("unit") != params.get("unit")
                    or not isinstance(payload.get("entries"), list)
                ):
                    raise MaxOpsError(
                        "invalid_response",
                        "MaxOps returned invalid service logs",
                    )
        return self._scope_payload(operation, payload)

    async def query_capability(
        self,
        capability: str,
        params: dict[str, Any],
    ) -> FleetQueryResult:
        binding = BINDING_BY_CAPABILITY.get(capability)
        now = int(time.time())
        if binding is None:
            result = FleetQueryResult(
                operation=capability,
                status=FleetStatus.UNSUPPORTED,
                source_backend="kennethbot",
                received_at=now,
                error=FleetError("unsupported", "Fleet capability is unavailable"),
            )
            self._record_metrics(capability, result, source="local")
            return result
        target_error = self._authorize_target(binding.operation, params)
        if target_error is not None:
            result = FleetQueryResult(
                operation=binding.operation,
                status=FleetStatus.FORBIDDEN,
                source_backend="kennethbot",
                received_at=now,
                error=target_error,
            )
            self._record_metrics(capability, result, source="local")
            return result
        if self.maxops is None:
            result = FleetQueryResult(
                operation=binding.operation,
                status=FleetStatus.UNAVAILABLE,
                source_backend="maxops",
                received_at=now,
                error=FleetError("not_configured", "MaxOps is not configured"),
            )
            self._record_metrics(capability, result, source="local")
            return result

        key = self._cache_key(binding.operation, params)
        cached = self._cache.get(key)
        if (
            binding.cacheable
            and cached is not None
            and time.monotonic() - cached.stored_at < self.cache_seconds
        ):
            result = replace(cached.result, cached=True, duration_ms=0)
            self._record_metrics(capability, result, source="cache")
            return result

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if (
                binding.cacheable
                and cached is not None
                and time.monotonic() - cached.stored_at < self.cache_seconds
            ):
                result = replace(cached.result, cached=True, duration_ms=0)
                self._record_metrics(capability, result, source="cache")
                return result
            stale_candidate = cached or await self._load_stale_projection(
                binding.operation,
                params,
            )
            started = time.monotonic()
            result = await self._execute(binding.operation, params, stale_candidate)
            duration_ms = max(int((time.monotonic() - started) * 1000), 0)
            result = replace(result, duration_ms=duration_ms)
            self._record_metrics(capability, result, source="upstream")
            await self._record_observation(
                result, params, sensitive=binding.sensitive
            )
            if result.status in {
                FleetStatus.FORBIDDEN,
                FleetStatus.UNSUPPORTED,
                FleetStatus.INVALID_REQUEST,
            }:
                self._cache.pop(key, None)
            if result.status == FleetStatus.FRESH and binding.cacheable:
                self._cache[key] = _CacheEntry(result=result, stored_at=time.monotonic())
            return result

    def _record_metrics(
        self,
        capability: str,
        result: FleetQueryResult,
        *,
        source: str,
    ) -> None:
        self._query_counter.labels(
            capability=capability,
            status=result.status.value,
            source=source,
        ).inc()
        if result.duration_ms is not None:
            self._query_duration.labels(capability=capability).observe(
                result.duration_ms / 1000
            )

    async def _execute(
        self,
        operation: str,
        params: dict[str, Any],
        cached: _CacheEntry | None,
    ) -> FleetQueryResult:
        assert self.maxops is not None
        received_at = int(time.time())
        try:
            catalog = await self.operations()
            upstream_operation = catalog.get(operation)
            if upstream_operation is None:
                raise MaxOpsError(
                    "unsupported",
                    "MaxOps operation is unavailable or not read-only",
                )
            if not operation_compatible(upstream_operation):
                raise MaxOpsError(
                    "incompatible_catalog",
                    "MaxOps operation schema is incompatible",
                )
            response = await self.maxops.execute(operation, params)
            response_data = self._validated_payload(operation, params, response.data)
        except MaxOpsError as exc:
            if cached is not None and exc.retryable:
                return FleetQueryResult(
                    operation=operation,
                    status=FleetStatus.STALE,
                    source_backend="maxops",
                    received_at=received_at,
                    observed_at=cached.result.observed_at,
                    expires_at=cached.result.expires_at,
                    data=cached.result.data,
                    error=FleetError(exc.code, str(exc), exc.retryable),
                    cached=True,
                )
            status = {
                "forbidden": FleetStatus.FORBIDDEN,
                "unauthorized": FleetStatus.FORBIDDEN,
                "unsupported": FleetStatus.UNSUPPORTED,
                "incompatible_catalog": FleetStatus.UNSUPPORTED,
                "invalid_request": FleetStatus.INVALID_REQUEST,
                "request_too_large": FleetStatus.INVALID_REQUEST,
            }.get(exc.code, FleetStatus.UNAVAILABLE)
            return FleetQueryResult(
                operation=operation,
                status=status,
                source_backend="maxops",
                received_at=received_at,
                error=FleetError(exc.code, str(exc), exc.retryable),
            )
        self._last_success_at = received_at
        return FleetQueryResult(
            operation=operation,
            status=FleetStatus.FRESH,
            source_backend="maxops",
            received_at=received_at,
            observed_at=self._observed_at(response.data),
            expires_at=received_at + self.cache_seconds,
            data=response_data,
        )

    async def fleet_overview(self) -> dict[str, Any]:
        overview, failed, alerts = await asyncio.gather(
            self.query_capability("fleet.read", {}),
            self.query_capability("fleet.failed_units.read", {}),
            self.query_capability("fleet.alerts.read", {}),
        )
        payload = overview.as_dict()
        payload["inventory"] = list(self.inventory)
        payload["failed_units"] = failed.as_dict()
        payload["active_alerts"] = alerts.as_dict()
        return payload

    async def host_facts(self, host: str) -> dict[str, Any]:
        return (await self.query_capability("host.facts.read", {"host": host})).as_dict()

    async def unit_status(self, host: str, unit: str) -> dict[str, Any]:
        return (
            await self.query_capability(
                "unit.status.read", {"host": host, "unit": unit}
            )
        ).as_dict()

    async def unit_logs(
        self,
        host: str,
        unit: str,
        lines: int,
        since_seconds: int = 3600,
    ) -> dict[str, Any]:
        return (
            await self.query_capability(
                "unit.logs.read",
                {
                    "host": host,
                    "unit": unit,
                    "lines": min(max(lines, 1), 200),
                    "since_seconds": min(max(since_seconds, 1), 86400),
                },
            )
        ).as_dict()

    async def alerts(self) -> dict[str, Any]:
        return (
            await self.query_capability("fleet.alerts.read", {})
        ).as_dict()

    async def failed_units(self) -> dict[str, Any]:
        return (
            await self.query_capability("fleet.failed_units.read", {})
        ).as_dict()

    def backend_snapshot(self) -> dict[str, Any]:
        stored = self.store.backend_snapshot() if self.store is not None else None
        return stored or {
            "backend_name": "maxops",
            "state": "disabled" if self.maxops is None else "unknown",
            "catalog_version": 1,
            "operations": [],
            "error_code": "",
            "last_success_at": self._last_success_at,
            "checked_at": None,
        }

    def recent_observations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.recent(limit=limit) if self.store is not None else []
