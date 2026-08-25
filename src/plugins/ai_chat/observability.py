from __future__ import annotations

import functools
import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
TURN_BUCKETS = (0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120, 300, 900, 1800)
STAGE_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120, 300)
_logger = logging.getLogger("ai_chat.observability")


class BotTelemetry:
    """Low-cardinality metrics and optional OTLP traces for the bot runtime."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.turns = Counter(
            "kennethbot_ai_turns_total",
            "AI turns handled by the bot.",
            ("platform", "kind", "status"),
            registry=self.registry,
        )
        self.turn_duration = Histogram(
            "kennethbot_ai_turn_duration_seconds",
            "End-to-end AI turn duration before final delivery.",
            ("platform", "kind", "status"),
            buckets=TURN_BUCKETS,
            registry=self.registry,
        )
        self.stage_duration = Histogram(
            "kennethbot_stage_duration_seconds",
            "Duration of bounded processing stages.",
            ("stage", "status"),
            buckets=STAGE_BUCKETS,
            registry=self.registry,
        )
        self.model_requests = Counter(
            "kennethbot_model_requests_total",
            "Requests attempted against model profiles.",
            ("profile", "provider", "status"),
            registry=self.registry,
        )
        self.model_duration = Histogram(
            "kennethbot_model_request_duration_seconds",
            "Model request latency, including provider transport.",
            ("profile", "provider", "status"),
            buckets=TURN_BUCKETS,
            registry=self.registry,
        )
        self.model_fallbacks = Counter(
            "kennethbot_model_fallbacks_total",
            "Successful model requests routed to a fallback profile.",
            ("requested_profile", "actual_profile"),
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "kennethbot_tool_calls_total",
            "Tool calls completed by the Agent loop.",
            ("tool", "status"),
            registry=self.registry,
        )
        self.tool_duration = Histogram(
            "kennethbot_tool_call_duration_seconds",
            "Tool execution latency.",
            ("tool", "status"),
            buckets=TURN_BUCKETS,
            registry=self.registry,
        )
        self.deliveries = Counter(
            "kennethbot_deliveries_total",
            "Outbound delivery attempts.",
            ("platform", "status"),
            registry=self.registry,
        )
        self.delivery_duration = Histogram(
            "kennethbot_delivery_duration_seconds",
            "Outbound platform delivery latency.",
            ("platform", "status"),
            buckets=STAGE_BUCKETS,
            registry=self.registry,
        )
        self.tokens = Counter(
            "kennethbot_model_tokens_total",
            "Tokens reported by model providers.",
            ("profile", "direction"),
            registry=self.registry,
        )
        self.runtime_tasks = Gauge(
            "kennethbot_runtime_tasks",
            "Currently active in-process AI tasks.",
            registry=self.registry,
        )
        self.outbox = Gauge(
            "kennethbot_outbox_deliveries",
            "Current durable outbox deliveries by status.",
            ("status",),
            registry=self.registry,
        )
        self._configured = False
        self._tracer = trace.get_tracer("kennethbot")

    def configure(
        self,
        service_name: str,
        *,
        service_version: str = "unknown",
        otlp_endpoint: str = "",
    ) -> None:
        if self._configured:
            return
        resource = Resource.create(
            {
                "service.name": service_name or "kennethbot",
                "service.version": service_version,
                "deployment.environment": os.getenv("ENVIRONMENT", "prod"),
            }
        )
        provider = TracerProvider(resource=resource)
        endpoint = otlp_endpoint.strip()
        if endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
        try:
            trace.set_tracer_provider(provider)
        except Exception:
            # Another host integration may already own the global provider.
            pass
        self._tracer = trace.get_tracer("kennethbot")
        self._configured = True

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: dict[str, str | int | float | bool] | None = None,
    ) -> Iterator[str]:
        with self._tracer.start_as_current_span(
            name,
            attributes=attributes or {},
        ) as span:
            context = span.get_span_context()
            trace_id = (
                f"{context.trace_id:032x}"
                if context.is_valid
                else _new_local_trace_id()
            )
            token = _trace_id.set(trace_id)
            try:
                yield trace_id
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                _trace_id.reset(token)

    @contextmanager
    def stage(self, stage: str) -> Iterator[None]:
        started = time.monotonic()
        status = "succeeded"
        try:
            with self._tracer.start_as_current_span(f"kennethbot.{stage}"):
                yield
        except BaseException:
            status = "failed"
            raise
        finally:
            self.stage_duration.labels(stage=stage, status=status).observe(
                time.monotonic() - started
            )

    @asynccontextmanager
    async def tool(self, name: str) -> AsyncIterator[None]:
        started = time.monotonic()
        status = "succeeded"
        try:
            with self._tracer.start_as_current_span(
                "kennethbot.tool",
                attributes={"tool.name": name},
            ):
                yield
        except BaseException:
            status = "failed"
            raise
        finally:
            self.tool_calls.labels(tool=name, status=status).inc()
            self.tool_duration.labels(tool=name, status=status).observe(
                time.monotonic() - started
            )

    def observe_model(
        self,
        *,
        requested_profile: str,
        actual_profile: str,
        provider: str,
        status: str,
        duration: float,
    ) -> None:
        self.model_requests.labels(
            profile=actual_profile,
            provider=provider,
            status=status,
        ).inc()
        self.model_duration.labels(
            profile=actual_profile,
            provider=provider,
            status=status,
        ).observe(max(duration, 0.0))
        if status == "succeeded" and actual_profile != requested_profile:
            self.model_fallbacks.labels(
                requested_profile=requested_profile,
                actual_profile=actual_profile,
            ).inc()

    def observe_tokens(self, profile: str, input_tokens: int, output_tokens: int) -> None:
        if input_tokens > 0:
            self.tokens.labels(profile=profile, direction="input").inc(input_tokens)
        if output_tokens > 0:
            self.tokens.labels(profile=profile, direction="output").inc(output_tokens)

    @contextmanager
    def delivery(self, platform: str) -> Iterator[None]:
        started = time.monotonic()
        status = "committed"
        try:
            with self._tracer.start_as_current_span(
                "kennethbot.delivery",
                attributes={"messaging.system": platform},
            ):
                yield
        except BaseException:
            status = "failed"
            raise
        finally:
            self.deliveries.labels(platform=platform, status=status).inc()
            self.delivery_duration.labels(platform=platform, status=status).observe(
                time.monotonic() - started
            )

    def update_runtime_gauges(self, running_tasks: Any, delivery_store: Any) -> None:
        if running_tasks is not None:
            self.runtime_tasks.set(len(running_tasks.list_all()))
        if delivery_store is not None:
            stats = delivery_store.stats()
            for status in (
                "pending",
                "sending",
                "ambiguous",
                "committed",
                "failed",
                "cancelled",
            ):
                self.outbox.labels(status=status).set(int(stats.get(status, 0)))

    def render(self) -> bytes:
        return generate_latest(self.registry)


_trace_id: ContextVar[str] = ContextVar("kennethbot_trace_id", default="")
telemetry = BotTelemetry()


def current_trace_id() -> str:
    return _trace_id.get()


def register_metrics_endpoint(
    app: Any,
    *,
    path: str,
    service_name: str,
    service_version: str,
    otlp_endpoint: str = "",
    running_tasks: Any = None,
    delivery_store: Any = None,
) -> None:
    normalized_path = "/" + path.strip("/")
    telemetry.configure(
        service_name,
        service_version=service_version,
        otlp_endpoint=otlp_endpoint,
    )

    @app.get(normalized_path, include_in_schema=False)
    async def prometheus_metrics() -> Response:
        telemetry.update_runtime_gauges(running_tasks, delivery_store)
        return Response(
            content=telemetry.render(),
            media_type=PROMETHEUS_CONTENT_TYPE,
            headers={"Cache-Control": "no-store"},
        )


def observed_ai_turn(
    function: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        event = args[1] if len(args) > 1 else kwargs.get("event")
        platform = "onebot-v11"
        kind = "group" if hasattr(event, "group_id") else "private"
        started = time.monotonic()
        status = "crashed"
        with telemetry.span(
            "kennethbot.ai_turn",
            attributes={
                "messaging.system": platform,
                "messaging.conversation.type": kind,
            },
        ) as trace_id:
            _logger.info(
                "AI turn started trace_id=%s platform=%s kind=%s",
                trace_id,
                platform,
                kind,
            )
            try:
                result = await function(*args, **kwargs)
                status = str(getattr(result, "status", "aborted"))
                return result
            except BaseException:
                status = "crashed"
                raise
            finally:
                telemetry.turns.labels(
                    platform=platform,
                    kind=kind,
                    status=status,
                ).inc()
                telemetry.turn_duration.labels(
                    platform=platform,
                    kind=kind,
                    status=status,
                ).observe(time.monotonic() - started)
                _logger.info(
                    "AI turn finished trace_id=%s platform=%s kind=%s status=%s duration_seconds=%.3f",
                    trace_id,
                    platform,
                    kind,
                    status,
                    time.monotonic() - started,
                )

    return wrapped


def _new_local_trace_id() -> str:
    return os.urandom(16).hex()
