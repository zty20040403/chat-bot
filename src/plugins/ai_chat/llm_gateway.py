from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from .model_catalog import ModelCatalog, ModelProfile
from .observability import telemetry


_logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMConfigError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMConnectionError(LLMError):
    pass


class LLMProviderError(LLMError):
    def __init__(self, provider: str, status_code: int, detail: str = "") -> None:
        self.provider = provider
        self.status_code = int(status_code)
        self.detail = detail
        message = f"{provider} API error: HTTP {self.status_code}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


class LLMUnavailableError(LLMError):
    pass


class LLMFailureKind(str, Enum):
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    BILLING = "billing"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER = "provider"
    INVALID_REQUEST = "invalid_request"
    REFUSAL = "refusal"
    CONFIG = "config"


@dataclass(frozen=True)
class CompletionResult:
    response: Any
    profile: ModelProfile
    fallback_count: int = 0


@dataclass(frozen=True)
class ClassifiedFailure:
    kind: LLMFailureKind
    retryable: bool
    open_immediately: bool = False
    long_cooldown: bool = False


@dataclass
class ModelCircuit:
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    fallback_uses: int = 0
    opened_until: float = 0.0
    probe_in_flight: bool = False
    last_error_kind: str = ""
    last_error: str = ""
    last_failure_at: int = 0
    last_success_at: int = 0


class ModelHealthRegistry:
    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 120.0,
        long_cooldown_seconds: float = 3600.0,
    ) -> None:
        self.failure_threshold = max(int(failure_threshold), 1)
        self.cooldown_seconds = max(float(cooldown_seconds), 1.0)
        self.long_cooldown_seconds = max(float(long_cooldown_seconds), 1.0)
        self._circuits: dict[str, ModelCircuit] = {}

    def acquire(self, profile_name: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        circuit = self._circuits.setdefault(profile_name, ModelCircuit())
        if circuit.opened_until <= 0:
            return True
        if circuit.opened_until > current:
            return False
        if circuit.probe_in_flight:
            return False
        circuit.probe_in_flight = True
        return True

    def record_success(
        self,
        profile_name: str,
        *,
        used_as_fallback: bool = False,
    ) -> None:
        circuit = self._circuits.setdefault(profile_name, ModelCircuit())
        circuit.consecutive_failures = 0
        circuit.opened_until = 0.0
        circuit.probe_in_flight = False
        circuit.last_error_kind = ""
        circuit.last_error = ""
        circuit.last_success_at = int(time.time())
        circuit.total_successes += 1
        circuit.fallback_uses += int(used_as_fallback)

    def record_failure(
        self,
        profile_name: str,
        failure: ClassifiedFailure,
        exc: BaseException,
    ) -> None:
        circuit = self._circuits.setdefault(profile_name, ModelCircuit())
        circuit.probe_in_flight = False
        circuit.total_failures += 1
        circuit.last_error_kind = failure.kind.value
        circuit.last_error = _safe_error_text(exc)
        circuit.last_failure_at = int(time.time())
        if not failure.retryable:
            return
        circuit.consecutive_failures += 1
        should_open = (
            failure.open_immediately
            or circuit.consecutive_failures >= self.failure_threshold
        )
        if should_open:
            duration = (
                self.long_cooldown_seconds
                if failure.long_cooldown
                else self.cooldown_seconds
            )
            circuit.opened_until = time.monotonic() + duration

    def snapshot(self, profile_names: list[str]) -> dict[str, dict[str, object]]:
        monotonic_now = time.monotonic()
        result: dict[str, dict[str, object]] = {}
        for name in profile_names:
            circuit = self._circuits.get(name)
            if circuit is None:
                result[name] = {
                    "status": "unknown",
                    "consecutive_failures": 0,
                    "retry_after_seconds": 0,
                }
                continue
            retry_after = max(int(circuit.opened_until - monotonic_now), 0)
            if circuit.opened_until > monotonic_now:
                status = "open"
            elif circuit.opened_until > 0:
                status = "half_open"
            elif circuit.consecutive_failures:
                status = "degraded"
            else:
                status = "healthy"
            result[name] = {
                "status": status,
                "consecutive_failures": circuit.consecutive_failures,
                "total_failures": circuit.total_failures,
                "total_successes": circuit.total_successes,
                "fallback_uses": circuit.fallback_uses,
                "retry_after_seconds": retry_after,
                "last_error_kind": circuit.last_error_kind,
                "last_error": circuit.last_error,
                "last_failure_at": circuit.last_failure_at,
                "last_success_at": circuit.last_success_at,
            }
        return result


class CompletionProvider(Protocol):
    async def create_completion(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> Any: ...

    async def list_models(self, profile: ModelProfile) -> list[str]: ...

    async def close(self) -> None: ...


class OpenAIChatProvider:
    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI] = {}

    def _client(self, profile: ModelProfile) -> AsyncOpenAI:
        _require_configured(profile)
        cache_key = profile.client_cache_key
        client = self._clients.get(cache_key)
        if client is None:
            client = AsyncOpenAI(
                api_key=profile.api_key or "local-no-key",
                base_url=profile.base_url,
                timeout=profile.timeout_seconds,
            )
            self._clients[cache_key] = client
        return client

    async def create_completion(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> Any:
        client = self._client(profile)
        request = {key: value for key, value in kwargs.items() if value is not None}
        if profile.max_output_tokens > 0 and "max_tokens" not in request:
            request["max_tokens"] = profile.max_output_tokens
        try:
            return await client.chat.completions.create(**request)
        except RateLimitError as exc:
            raise LLMRateLimitError(
                f"{profile.provider} rate limit reached"
            ) from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(
                f"could not connect to {profile.provider}"
            ) from exc
        except APIStatusError as exc:
            raise LLMProviderError(
                profile.provider,
                exc.status_code,
                _openai_error_detail(exc),
            ) from exc

    async def list_models(self, profile: ModelProfile) -> list[str]:
        client = self._client(profile)
        try:
            response = await client.models.list()
        except RateLimitError as exc:
            raise LLMRateLimitError(
                f"{profile.provider} rate limit reached"
            ) from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(
                f"could not connect to {profile.provider}"
            ) from exc
        except APIStatusError as exc:
            raise LLMProviderError(
                profile.provider,
                exc.status_code,
                _openai_error_detail(exc),
            ) from exc
        return sorted(
            item.id
            for item in response.data
            if isinstance(getattr(item, "id", None), str) and item.id.strip()
        )

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()


class AnthropicMessagesProvider:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _client(self, profile: ModelProfile) -> httpx.AsyncClient:
        _require_configured(profile)
        cache_key = profile.client_cache_key
        client = self._clients.get(cache_key)
        if client is None:
            client = httpx.AsyncClient(timeout=profile.timeout_seconds)
            self._clients[cache_key] = client
        return client

    async def create_completion(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> Any:
        if kwargs.get("stream"):
            raise LLMConfigError(
                f"model profile {profile.name!r} does not enable streaming"
            )
        body = _anthropic_request(profile, kwargs)
        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if profile.api_key:
            headers["x-api-key"] = profile.api_key
        try:
            response = await self._client(profile).post(
                _anthropic_messages_url(profile.base_url),
                headers=headers,
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise LLMConnectionError(
                f"{profile.provider} request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(
                f"could not connect to {profile.provider}"
            ) from exc
        _raise_for_anthropic_status(profile, response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMProviderError(
                profile.provider,
                response.status_code,
                "invalid JSON response",
            ) from exc
        if not isinstance(payload, dict):
            raise LLMProviderError(
                profile.provider,
                response.status_code,
                "invalid response object",
            )
        return _anthropic_response(payload)

    async def list_models(self, profile: ModelProfile) -> list[str]:
        raise LLMConfigError(
            f"model listing is not enabled for profile {profile.name!r}"
        )

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.aclose()


class LLMGateway:
    def __init__(
        self,
        providers: dict[str, CompletionProvider] | None = None,
        *,
        catalog: ModelCatalog | None = None,
        fallback_enabled: bool = True,
        failure_threshold: int = 2,
        cooldown_seconds: float = 120.0,
        long_cooldown_seconds: float = 3600.0,
    ) -> None:
        self._providers: dict[str, CompletionProvider] = (
            {
                "openai-chat": OpenAIChatProvider(),
                "anthropic-messages": AnthropicMessagesProvider(),
            }
            if providers is None
            else dict(providers)
        )
        self._catalog = catalog
        self._fallback_enabled = bool(fallback_enabled)
        self.health = ModelHealthRegistry(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
            long_cooldown_seconds=long_cooldown_seconds,
        )
        self._closed = False

    async def create_completion(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> Any:
        result = await self.create_completion_with_profile(profile, **kwargs)
        return result.response

    async def create_completion_with_profile(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> CompletionResult:
        candidates = self._completion_candidates(profile, kwargs)
        attempted = 0
        last_error: BaseException | None = None
        for candidate in candidates:
            if not self.health.acquire(candidate.name):
                telemetry.observe_model(
                    requested_profile=profile.name,
                    actual_profile=candidate.name,
                    provider=candidate.provider_identity,
                    status="circuit_open",
                    duration=0.0,
                )
                continue
            attempted += 1
            request = _request_for_profile(candidate, kwargs)
            request_started = time.monotonic()
            try:
                with telemetry.stage("model.request"):
                    response = await self._create_single(candidate, **request)
            except (LLMError, asyncio.TimeoutError, httpx.TimeoutException) as exc:
                failure = classify_llm_failure(exc)
                telemetry.observe_model(
                    requested_profile=profile.name,
                    actual_profile=candidate.name,
                    provider=candidate.provider_identity,
                    status=failure.kind.value,
                    duration=time.monotonic() - request_started,
                )
                self.health.record_failure(candidate.name, failure, exc)
                last_error = exc
                _logger.warning(
                    "Model profile %s failed (%s); fallback=%s",
                    candidate.name,
                    failure.kind.value,
                    failure.retryable and self._fallback_enabled,
                )
                if not failure.retryable or not self._fallback_enabled:
                    raise
                continue
            except BaseException:
                telemetry.observe_model(
                    requested_profile=profile.name,
                    actual_profile=candidate.name,
                    provider=candidate.provider_identity,
                    status="unexpected_error",
                    duration=time.monotonic() - request_started,
                )
                raise
            telemetry.observe_model(
                requested_profile=profile.name,
                actual_profile=candidate.name,
                provider=candidate.provider_identity,
                status="succeeded",
                duration=time.monotonic() - request_started,
            )
            self.health.record_success(
                candidate.name,
                used_as_fallback=candidate.name != profile.name,
            )
            if candidate.name != profile.name:
                _logger.warning(
                    "Model request routed from profile %s to fallback %s",
                    profile.name,
                    candidate.name,
                )
            return CompletionResult(
                response=response,
                profile=candidate,
                fallback_count=max(attempted - 1, 1)
                if candidate.name != profile.name
                else 0,
            )
        if last_error is not None:
            raise last_error
        raise LLMUnavailableError(
            f"all compatible fallback profiles for {profile.name!r} are cooling down"
        )

    async def _create_single(
        self,
        profile: ModelProfile,
        **kwargs: Any,
    ) -> Any:
        provider = self._provider(profile)
        if kwargs.get("tools") and not profile.capabilities.tools:
            raise LLMConfigError(
                f"model profile {profile.name!r} does not support tool calls"
            )
        if kwargs.get("stream") and not profile.capabilities.streaming:
            raise LLMConfigError(
                f"model profile {profile.name!r} does not support streaming"
            )
        if kwargs.get("response_format") and not profile.capabilities.json_mode:
            raise LLMConfigError(
                f"model profile {profile.name!r} does not support native JSON mode"
            )
        return await provider.create_completion(profile, **kwargs)

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        names = (
            [profile.name for profile in self._catalog.profiles]
            if self._catalog is not None
            else list(self.health._circuits)
        )
        return self.health.snapshot(names)

    def _completion_candidates(
        self,
        primary: ModelProfile,
        kwargs: dict[str, Any],
    ) -> list[ModelProfile]:
        candidates = [primary]
        if not self._fallback_enabled or self._catalog is None:
            return candidates

        preferred: list[ModelProfile] = []
        for name in primary.fallback_profiles:
            selected = self._catalog.try_resolve(name)
            if selected is not None:
                preferred.append(selected)
        automatic = [
            candidate
            for candidate in self._catalog.profiles
            if candidate.name != primary.name
        ]
        automatic.sort(key=lambda item: item.provider != primary.provider)
        seen = {primary.name}
        for candidate in (*preferred, *automatic):
            if candidate.name in seen or not candidate.configured:
                continue
            if not _profile_supports_request(candidate, kwargs):
                continue
            seen.add(candidate.name)
            candidates.append(candidate)
        return candidates

    async def list_models(self, profile: ModelProfile) -> list[str]:
        if not profile.capabilities.model_listing:
            raise LLMConfigError(
                f"model listing is not enabled for profile {profile.name!r}"
            )
        return await self._provider(profile).list_models(profile)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for provider in self._providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            await provider.close()

    def _provider(self, profile: ModelProfile) -> CompletionProvider:
        if self._closed:
            raise LLMConfigError("LLM gateway is closed")
        provider = self._providers.get(profile.protocol)
        if provider is None:
            raise LLMConfigError(
                f"unsupported model protocol: {profile.protocol}"
            )
        return provider


def classify_llm_failure(exc: BaseException) -> ClassifiedFailure:
    if isinstance(exc, LLMRateLimitError):
        return ClassifiedFailure(
            LLMFailureKind.RATE_LIMIT,
            retryable=True,
            open_immediately=True,
        )
    if isinstance(
        exc,
        (LLMConnectionError, asyncio.TimeoutError, httpx.TimeoutException),
    ):
        return ClassifiedFailure(LLMFailureKind.NETWORK, retryable=True)
    if isinstance(exc, LLMConfigError):
        if "missing its API key" in str(exc):
            return ClassifiedFailure(
                LLMFailureKind.CONFIG,
                retryable=True,
                open_immediately=True,
                long_cooldown=True,
            )
        return ClassifiedFailure(LLMFailureKind.CONFIG, retryable=False)
    if isinstance(exc, LLMProviderError):
        status = exc.status_code
        detail = exc.detail.lower()
        if status == 429:
            return ClassifiedFailure(
                LLMFailureKind.RATE_LIMIT,
                retryable=True,
                open_immediately=True,
            )
        if status in {401, 403}:
            return ClassifiedFailure(
                LLMFailureKind.AUTH,
                retryable=True,
                open_immediately=True,
                long_cooldown=True,
            )
        if status == 402:
            return ClassifiedFailure(
                LLMFailureKind.BILLING,
                retryable=True,
                open_immediately=True,
                long_cooldown=True,
            )
        if status == 404:
            return ClassifiedFailure(
                LLMFailureKind.MODEL_UNAVAILABLE,
                retryable=True,
                open_immediately=True,
                long_cooldown=True,
            )
        if status == 400 and any(
            marker in detail
            for marker in (
                "content_filter",
                "content policy",
                "safety policy",
                "moderation",
            )
        ):
            return ClassifiedFailure(LLMFailureKind.REFUSAL, retryable=False)
        if status in {400, 404, 413, 422}:
            return ClassifiedFailure(
                LLMFailureKind.INVALID_REQUEST,
                retryable=False,
            )
        if status in {408, 409, 425} or status >= 500:
            return ClassifiedFailure(LLMFailureKind.PROVIDER, retryable=True)
        return ClassifiedFailure(LLMFailureKind.PROVIDER, retryable=False)
    return ClassifiedFailure(LLMFailureKind.PROVIDER, retryable=False)


def _request_for_profile(
    profile: ModelProfile,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    request = dict(kwargs)
    request["model"] = profile.model
    if profile.temperature is None:
        request.pop("temperature", None)
    else:
        request["temperature"] = profile.temperature
    if profile.protocol == "openai-chat":
        if profile.thinking == "enabled":
            request["extra_body"] = {"thinking": {"type": "enabled"}}
        elif profile.thinking == "disabled":
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            request.pop("extra_body", None)
        if (
            profile.provider in {"openai", "cliproxy"}
            and profile.reasoning_effort
        ):
            request["reasoning_effort"] = profile.reasoning_effort
        else:
            request.pop("reasoning_effort", None)
    else:
        request.pop("extra_body", None)
        request.pop("reasoning_effort", None)
    return request


def _profile_supports_request(
    profile: ModelProfile,
    kwargs: dict[str, Any],
) -> bool:
    if kwargs.get("tools") and not profile.capabilities.tools:
        return False
    if kwargs.get("stream") and not profile.capabilities.streaming:
        return False
    if kwargs.get("response_format") and not profile.capabilities.json_mode:
        return False
    if _request_contains_images(kwargs.get("messages")):
        return profile.capabilities.vision
    return True


def _request_contains_images(messages: object) -> bool:
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in {
                "image",
                "image_url",
            }:
                return True
    return False


def _openai_error_detail(exc: APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    parts = [
        str(error.get(key) or "").strip().replace("\n", " ")
        for key in ("type", "code", "message")
    ]
    return ": ".join(part for part in parts if part)[:300]


def _safe_error_text(exc: BaseException) -> str:
    if isinstance(exc, LLMProviderError):
        text = f"HTTP {exc.status_code}"
        if exc.detail:
            text += f": {exc.detail}"
    else:
        text = str(exc)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", text)
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}",
        "Bearer [redacted]",
        text,
    )
    return " ".join(text.split())[:300]


def _require_configured(profile: ModelProfile) -> None:
    if profile.configured:
        return
    source = profile.api_key_env or "api_key"
    raise LLMConfigError(
        f"model profile {profile.name!r} is missing its API key ({source})"
    )


def _anthropic_request(
    profile: ModelProfile,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    messages = kwargs.get("messages")
    if not isinstance(messages, list):
        raise LLMConfigError("Anthropic Messages requires a messages list")
    system, converted_messages = _convert_anthropic_messages(messages)
    body: dict[str, Any] = {
        "model": str(kwargs.get("model") or profile.model),
        "max_tokens": profile.max_output_tokens or 4096,
        "messages": converted_messages,
    }
    if system:
        body["system"] = system
    temperature = kwargs.get("temperature", profile.temperature)
    if temperature is not None:
        body["temperature"] = temperature

    tools = kwargs.get("tools")
    if tools:
        body["tools"] = _convert_anthropic_tools(tools)
        tool_choice = _convert_anthropic_tool_choice(
            kwargs.get("tool_choice", "auto")
        )
        body["tool_choice"] = tool_choice
    else:
        tool_choice = {"type": "none"}
    if (
        profile.thinking == "enabled"
        and tool_choice["type"] in {"auto", "none"}
    ):
        body["thinking"] = {"type": "adaptive"}
    return body


def _convert_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        if role == "system":
            text = _content_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": _content_text(content),
                        }
                    ],
                }
            )
            continue
        if role not in {"user", "assistant"}:
            continue

        if role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            text = _content_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                raw_arguments = function.get("arguments")
                try:
                    arguments = json.loads(raw_arguments or "{}")
                except (TypeError, json.JSONDecodeError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "input": arguments,
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue

        converted.append({"role": role, "content": _content_text(content)})
    return "\n\n".join(system_parts), converted


def _convert_anthropic_tools(tools: object) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise LLMConfigError("tools must be a list")
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        converted.append(
            {
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters")
                if isinstance(function.get("parameters"), dict)
                else {"type": "object", "properties": {}},
            }
        )
    return converted


def _convert_anthropic_tool_choice(choice: object) -> dict[str, Any]:
    if isinstance(choice, str):
        normalized = choice.strip().lower()
        if normalized == "required":
            return {"type": "any"}
        if normalized in {"auto", "none"}:
            return {"type": normalized}
        return {"type": "auto"}
    if isinstance(choice, dict):
        function = choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return {"type": "tool", "name": str(function["name"])}
    return {"type": "auto"}


def _anthropic_response(payload: dict[str, Any]) -> Any:
    text_parts: list[str] = []
    tool_calls: list[Any] = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append(
                SimpleNamespace(
                    id=str(block.get("id") or ""),
                    type="function",
                    function=SimpleNamespace(
                        name=str(block.get("name") or ""),
                        arguments=json.dumps(
                            block.get("input")
                            if isinstance(block.get("input"), dict)
                            else {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
            )
    raw_usage = payload.get("usage")
    usage_data = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _safe_int(usage_data.get("input_tokens"))
    output_tokens = _safe_int(usage_data.get("output_tokens"))
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    message = SimpleNamespace(
        role="assistant",
        content="".join(text_parts),
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        id=payload.get("id"),
        model=payload.get("model"),
        choices=[
            SimpleNamespace(
                index=0,
                message=message,
                finish_reason=payload.get("stop_reason"),
            )
        ],
        usage=usage,
    )


def _anthropic_messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized + "/messages"
    if normalized.endswith("/v1/messages"):
        return normalized
    return normalized + "/v1/messages"


def _raise_for_anthropic_status(
    profile: ModelProfile,
    response: httpx.Response,
) -> None:
    if response.status_code < 400:
        return
    detail = _anthropic_error_detail(response)
    if response.status_code == 429:
        raise LLMRateLimitError(
            f"{profile.provider} rate limit reached"
        )
    raise LLMProviderError(profile.provider, response.status_code, detail)


def _anthropic_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    error_type = str(error.get("type") or "").strip()
    message = str(error.get("message") or "").strip().replace("\n", " ")
    combined = ": ".join(item for item in (error_type, message) if item)
    return combined[:300]


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
