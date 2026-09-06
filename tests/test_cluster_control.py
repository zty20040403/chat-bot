from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import nonebot

nonebot.init()

from src.cluster_control.api import create_app
from src.cluster_control.capabilities import capability_manifest
from src.cluster_control.config import _inventory
from src.cluster_control.adapters.maxops import (
    MaxOpsClient,
    MaxOpsError,
    MaxOpsOperation,
    MaxOpsResponse,
)
from src.cluster_control.service import FleetControlService
from src.plugins.ai_chat.fleet_client import FleetControlClient


class FakeStore:
    def __init__(self) -> None:
        self.backends: list[dict[str, object]] = []
        self.observations: list[dict[str, object]] = []
        self.closed = False

    def record_backend(self, **payload: object) -> None:
        self.backends.append(payload)

    def record_observation(self, **payload: object) -> int:
        self.observations.append(payload)
        return len(self.observations)

    def backend_snapshot(self) -> dict[str, object] | None:
        return self.backends[-1] if self.backends else None

    def recent(self, *, limit: int) -> list[dict[str, object]]:
        return self.observations[-limit:]

    def latest(
        self, *, operation: str, params: dict[str, object]
    ) -> dict[str, object] | None:
        for item in reversed(self.observations):
            if (
                item["operation"] == operation
                and item["params"] == params
                and not item["sensitive"]
                and item["payload"] is not None
                and item["status"] in {"fresh", "stale"}
            ):
                return {
                    "operation": operation,
                    "status": item["status"],
                    "data": item["payload"],
                    "sensitive": False,
                    "observed_at": item["observed_at"],
                    "received_at": item["received_at"],
                    "expires_at": item["expires_at"],
                    "duration_ms": item["duration_ms"],
                    "error_code": item["error_code"],
                }
        return None

    def close(self) -> None:
        self.closed = True


class FakeMaxOps:
    def __init__(self) -> None:
        self.fail = False
        self.execute_calls = 0
        self.closed = False
        self.failure_code = "timeout"

    async def operations(self) -> tuple[MaxOpsOperation, ...]:
        if self.fail:
            raise MaxOpsError(
                self.failure_code,
                "upstream request failed",
                retryable=self.failure_code == "timeout",
            )
        schemas = {
            "fleet.overview": {},
            "units.failed": {},
            "alerts.active": {},
            "host.facts": {"host": "string"},
            "units.status": {"host": "string", "unit": "string"},
            "units.logs": {
                "host": "string",
                "unit": "string",
                "lines": "integer",
                "since_seconds": "integer",
            },
        }
        return tuple(
            MaxOpsOperation(
                name=name,
                params_schema={
                    "type": "object",
                    "properties": {
                        key: {"type": value} for key, value in properties.items()
                    },
                    "required": [
                        key for key in ("host", "unit") if key in properties
                    ],
                },
            )
            for name, properties in schemas.items()
        )

    async def execute(
        self,
        operation: str,
        params: dict[str, object],
    ) -> MaxOpsResponse:
        self.execute_calls += 1
        if self.fail:
            raise MaxOpsError(
                self.failure_code,
                "upstream request failed",
                retryable=self.failure_code == "timeout",
            )
        if operation in {"fleet.overview", "units.failed"}:
            data: dict[str, object] = {"hosts": [], "observed_at": 123}
        elif operation == "alerts.active":
            data = {"alerts": [], "observed_at": 123}
        elif operation == "host.facts":
            data = {"host": params["host"], "facts": {}, "observed_at": 123}
        elif operation == "units.status":
            data = {
                "host": params["host"],
                "unit": {"unit": params["unit"]},
                "observed_at": 123,
            }
        else:
            data = {
                "host": params["host"],
                "unit": params["unit"],
                "entries": [],
                "observed_at": 123,
            }
        return MaxOpsResponse(data=data, elapsed_ms=1)

    async def close(self) -> None:
        self.closed = True


class MaxOpsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_filters_mutations_and_execute_checks_catalog(self) -> None:
        with TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("x" * 40, encoding="ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.headers["authorization"], "Bearer " + "x" * 40)
                if request.url.path == "/v1/operations":
                    return httpx.Response(
                        200,
                        json={
                            "version": 1,
                            "operations": [
                                {
                                    "name": "fleet.overview",
                                    "read_only": True,
                                    "params_schema": {},
                                },
                                {
                                    "name": "units.restart",
                                    "read_only": False,
                                    "params_schema": {},
                                },
                            ],
                        },
                    )
                self.assertEqual(
                    json.loads(request.content),
                    {"op": "fleet.overview", "params": {}},
                )
                return httpx.Response(200, json={"hosts": []})

            client = MaxOpsClient(
                "http://maxops.test",
                token_file,
                transport=httpx.MockTransport(handler),
            )
            try:
                operations = await client.operations()
                self.assertEqual([item.name for item in operations], ["fleet.overview"])
                response = await client.execute("fleet.overview", {})
                self.assertEqual(response.data, {"hosts": []})
                with self.assertRaises(MaxOpsError) as caught:
                    await client.execute("units.restart", {})
                self.assertEqual(caught.exception.code, "unsupported")
            finally:
                await client.close()


class FleetControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_authenticated_log_query(self) -> None:
        with TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("c" * 40, encoding="ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.headers["authorization"], "Bearer " + "c" * 40)
                self.assertEqual(
                    request.url.path,
                    "/v1/hosts/h610/units/nginx.service/logs",
                )
                self.assertEqual(request.url.params["lines"], "200")
                self.assertEqual(request.url.params["since_seconds"], "86400")
                return httpx.Response(200, json={"ok": True, "data": {"entries": []}})

            client = FleetControlClient(
                "http://control.test",
                token_file,
                transport=httpx.MockTransport(handler),
            )
            try:
                payload = await client.logs(
                    "h610",
                    "nginx.service",
                    lines=999,
                    since_seconds=999999,
                )
                self.assertTrue(payload["ok"])
            finally:
                await client.close()


class ClusterControlConfigTests(unittest.TestCase):
    def test_inventory_is_normalized_and_requires_explicit_observation(self) -> None:
        inventory = _inventory(json.dumps([
            {
                "host_id": "h610",
                "roles": ["bot"],
                "readable_units": ["nginx.service", "nginx.service"],
            }
        ]))
        self.assertFalse(inventory[0]["observe"])
        self.assertEqual(inventory[0]["readable_units"], ["nginx.service"])
        self.assertEqual(inventory[0]["permission_source"], "unconfirmed")

    def test_inventory_rejects_implicit_boolean_and_non_service_unit(self) -> None:
        with self.assertRaises(ValueError):
            _inventory(json.dumps([{"host_id": "h610", "observe": "true"}]))
        with self.assertRaises(ValueError):
            _inventory(json.dumps([
                {"host_id": "h610", "readable_units": ["/bin/sh"]}
            ]))


class MaxOpsCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_incompatible_upstream_schema_is_not_advertised(self) -> None:
        operation = MaxOpsOperation(
            name="host.facts",
            params_schema={
                "type": "object",
                "properties": {"host": {"type": "integer"}},
                "required": ["host"],
            },
        )
        manifest = capability_manifest({operation.name: operation})
        host_capability = next(
            item for item in manifest if item["name"] == "host.facts.read"
        )
        self.assertFalse(host_capability["available"])
        self.assertEqual(
            host_capability["reason"], "upstream_schema_incompatible"
        )

    async def test_rejects_invalid_url_and_credential(self) -> None:
        with TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("short", encoding="ascii")
            with self.assertRaises(ValueError):
                MaxOpsClient("file:///tmp/socket", token_file)
            client = MaxOpsClient(
                "http://maxops.test",
                token_file,
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500)
                ),
            )
            try:
                with self.assertRaises(MaxOpsError) as caught:
                    await client.operations()
                self.assertEqual(caught.exception.code, "credential_invalid")
            finally:
                await client.close()


class FleetControlServiceTests(unittest.IsolatedAsyncioTestCase):
    inventory = (
        {
            "host_id": "h610",
            "observe": True,
            "readable_units": ["nginx.service"],
        },
    )

    def test_observed_at_accepts_maxops_rfc3339_timestamp(self) -> None:
        self.assertEqual(
            FleetControlService._observed_at(
                {"observed_at": "2026-09-06T07:00:00Z"}
            ),
            1788678000,
        )
        self.assertIsNone(
            FleetControlService._observed_at({"observed_at": "not-a-time"})
        )

    async def test_deduplicates_fresh_queries_and_uses_stale_evidence(self) -> None:
        maxops = FakeMaxOps()
        store = FakeStore()
        service = FleetControlService(
            maxops,
            store=store,
            inventory=self.inventory,
            cache_seconds=20,
        )
        first = await service.query_capability("fleet.read", {})
        second = await service.query_capability("fleet.read", {})
        self.assertEqual(first.status.value, "fresh")
        self.assertTrue(second.cached)
        self.assertEqual(maxops.execute_calls, 1)

        key = service._cache_key("fleet.overview", {})
        service._cache[key] = service._cache[key].__class__(
            result=service._cache[key].result,
            stored_at=time.monotonic() - 30,
        )
        maxops.fail = True
        stale = await service.query_capability("fleet.read", {})
        self.assertEqual(stale.status.value, "stale")
        self.assertEqual(stale.data, first.data)
        self.assertEqual(stale.error.code, "timeout")
        self.assertEqual(len(store.observations), 2)

        await service.close()
        self.assertTrue(maxops.closed)
        self.assertTrue(store.closed)

    async def test_authorization_failure_never_returns_stale_data(self) -> None:
        maxops = FakeMaxOps()
        service = FleetControlService(
            maxops,
            store=FakeStore(),
            inventory=self.inventory,
            cache_seconds=20,
        )
        await service.query_capability("fleet.read", {})
        key = service._cache_key("fleet.overview", {})
        service._cache[key] = service._cache[key].__class__(
            result=service._cache[key].result,
            stored_at=time.monotonic() - 30,
        )
        maxops.fail = True
        maxops.failure_code = "forbidden"
        denied = await service.query_capability("fleet.read", {})
        self.assertEqual(denied.status.value, "forbidden")
        self.assertIsNone(denied.data)
        self.assertNotIn(key, service._cache)
        await service.close()

    async def test_sensitive_log_is_marked_before_persistence(self) -> None:
        maxops = FakeMaxOps()
        store = FakeStore()
        service = FleetControlService(maxops, store=store, inventory=self.inventory)
        result = await service.unit_logs("h610", "nginx.service", 20)
        self.assertTrue(result["ok"])
        self.assertTrue(store.observations[-1]["sensitive"])
        await service.close()

    async def test_local_inventory_denies_unregistered_hosts_and_units(self) -> None:
        maxops = FakeMaxOps()
        service = FleetControlService(maxops, inventory=self.inventory)
        host = await service.host_facts("tank")
        unit = await service.unit_status("h610", "postgresql.service")
        self.assertEqual(host["status"], "forbidden")
        self.assertEqual(unit["status"], "forbidden")
        self.assertEqual(maxops.execute_calls, 0)
        await service.close()

    async def test_aggregate_results_are_scoped_to_local_inventory(self) -> None:
        class AggregateMaxOps(FakeMaxOps):
            async def execute(
                self, operation: str, params: dict[str, object]
            ) -> MaxOpsResponse:
                self.execute_calls += 1
                if operation in {"fleet.overview", "units.failed"}:
                    return MaxOpsResponse(
                        data={
                            "hosts": [
                                {"host": "h610", "state": "available"},
                                {"host": "tank", "state": "available"},
                            ]
                        },
                        elapsed_ms=1,
                    )
                return MaxOpsResponse(
                    data={
                        "alerts": [
                            {"labels": {"instance": "h610"}},
                            {"labels": {"instance": "tank"}},
                        ]
                    },
                    elapsed_ms=1,
                )

        service = FleetControlService(AggregateMaxOps(), inventory=self.inventory)
        fleet = await service.fleet_overview()
        self.assertEqual(
            [item["host"] for item in fleet["data"]["hosts"]], ["h610"]
        )
        self.assertEqual(
            [item["host"] for item in fleet["failed_units"]["data"]["hosts"]],
            ["h610"],
        )
        self.assertEqual(
            len(fleet["active_alerts"]["data"]["alerts"]), 1
        )
        await service.close()

    async def test_incompatible_schema_is_blocked_before_execution(self) -> None:
        class IncompatibleMaxOps(FakeMaxOps):
            async def operations(self) -> tuple[MaxOpsOperation, ...]:
                operations = list(await super().operations())
                operations[0] = MaxOpsOperation(
                    name="fleet.overview",
                    params_schema={"type": "array"},
                )
                return tuple(operations)

        maxops = IncompatibleMaxOps()
        service = FleetControlService(maxops, inventory=self.inventory)
        result = await service.query_capability("fleet.read", {})
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(result.error.code, "incompatible_catalog")
        self.assertEqual(maxops.execute_calls, 0)
        await service.close()

    async def test_cross_host_response_is_rejected(self) -> None:
        class CrossHostMaxOps(FakeMaxOps):
            async def execute(
                self, operation: str, params: dict[str, object]
            ) -> MaxOpsResponse:
                self.execute_calls += 1
                return MaxOpsResponse(
                    data={"host": "tank", "facts": {}, "observed_at": 123},
                    elapsed_ms=1,
                )

        service = FleetControlService(CrossHostMaxOps(), inventory=self.inventory)
        result = await service.query_capability("host.facts.read", {"host": "h610"})
        self.assertEqual(result.status.value, "unavailable")
        self.assertEqual(result.error.code, "invalid_response")
        await service.close()

    async def test_restart_can_use_durable_non_sensitive_projection(self) -> None:
        store = FakeStore()
        first_service = FleetControlService(
            FakeMaxOps(), store=store, inventory=self.inventory
        )
        original = await first_service.query_capability("fleet.read", {})
        first_service.store = None
        await first_service.close()

        unavailable = FakeMaxOps()
        unavailable.fail = True
        restarted = FleetControlService(
            unavailable, store=store, inventory=self.inventory
        )
        result = await restarted.query_capability("fleet.read", {})
        self.assertEqual(result.status.value, "stale")
        self.assertEqual(result.data, original.data)
        await restarted.close()


class ClusterControlApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_requires_its_own_bearer_token(self) -> None:
        with TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("z" * 40, encoding="ascii")
            service = FleetControlService(
                None,
                inventory=({"host_id": "h610", "observe": True},),
            )
            app = create_app(service, api_token_file=token_file)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://control.test",
            ) as client:
                self.assertEqual((await client.get("/health")).status_code, 200)
                self.assertEqual((await client.get("/v1/fleet")).status_code, 401)
                response = await client.get(
                    "/v1/fleet",
                    headers={"Authorization": "Bearer " + "z" * 40},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "unavailable")
                self.assertEqual(
                    response.json()["inventory"],
                    [{"host_id": "h610", "observe": True}],
                )
                metrics = await client.get("/metrics/")
                self.assertEqual(metrics.status_code, 200)
                self.assertIn(
                    "kennethbot_cluster_queries_total",
                    metrics.text,
                )


if __name__ == "__main__":
    unittest.main()
