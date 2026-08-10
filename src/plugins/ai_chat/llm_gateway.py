from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from .model_catalog import ModelProfile


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
    ) -> None:
        self._providers: dict[str, CompletionProvider] = (
            {
                "openai-chat": OpenAIChatProvider(),
                "anthropic-messages": AnthropicMessagesProvider(),
            }
            if providers is None
            else dict(providers)
        )
        self._closed = False

    async def create_completion(
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
