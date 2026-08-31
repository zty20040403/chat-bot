from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import nonebot

nonebot.init()

from src.plugins.ai_chat.llm_gateway import (
    AnthropicMessagesProvider,
    LLMConfigError,
    LLMConnectionError,
    LLMGateway,
    LLMProviderError,
    LLMRateLimitError,
    OpenAIChatProvider,
    _anthropic_request,
    _anthropic_response,
    _request_for_profile,
)
from src.plugins.ai_chat.model_catalog import (
    ModelCatalog,
    ModelCapabilities,
    ModelProfile,
)


def profile(
    name: str,
    *,
    protocol: str = "openai-chat",
    provider: str = "test",
    api_key: str = "key",
    base_url: str = "https://example.test/v1",
    thinking: str = "auto",
    capabilities: ModelCapabilities | None = None,
) -> ModelProfile:
    return ModelProfile(
        name=name,
        provider=provider,
        protocol=protocol,
        model=f"model-{name}",
        base_url=base_url,
        api_key=api_key,
        thinking=thinking,
        max_output_tokens=1024 if protocol == "anthropic-messages" else 0,
        capabilities=capabilities or ModelCapabilities.defaults_for(protocol),
    )


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[ModelProfile, dict[str, object]]] = []
        self.closed = False

    async def create_completion(self, selected, **kwargs):
        self.calls.append((selected, kwargs))
        return "ok"

    async def list_models(self, selected):
        self.calls.append((selected, {"operation": "list"}))
        return [selected.model]

    async def close(self):
        self.closed = True


class LLMGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_profile_forwards_reasoning_effort(self) -> None:
        selected = profile("reasoning", provider="cliproxy")
        selected = selected.with_reasoning_effort("high")

        request = _request_for_profile(
            selected,
            {"messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(request["reasoning_effort"], "high")

    async def test_retryable_failure_uses_fallback_and_opens_circuit(self) -> None:
        primary = profile("primary", provider="same-provider")
        backup = profile("backup", provider="same-provider")
        primary = ModelProfile(
            **{
                **primary.__dict__,
                "fallback_profiles": ("backup",),
            }
        )
        catalog = ModelCatalog(
            {primary.name: primary, backup.name: backup},
            default_profile=primary.name,
        )

        class SelectiveProvider(RecordingProvider):
            async def create_completion(self, selected, **kwargs):
                self.calls.append((selected, kwargs))
                if selected.name == "primary":
                    raise LLMConnectionError("temporary network failure")
                return selected.name

        provider = SelectiveProvider()
        gateway = LLMGateway(
            {"openai-chat": provider},
            catalog=catalog,
            failure_threshold=1,
        )

        messages = [
            {"role": "tool", "tool_call_id": "call-1", "content": "done"}
        ]
        first = await gateway.create_completion_with_profile(
            primary,
            messages=messages,
        )
        second = await gateway.create_completion_with_profile(
            primary,
            messages=[],
        )

        self.assertEqual(first.response, "backup")
        self.assertEqual(first.profile.name, "backup")
        self.assertEqual(provider.calls[1][1]["messages"], messages)
        self.assertEqual(second.profile.name, "backup")
        self.assertEqual(
            [item[0].name for item in provider.calls],
            ["primary", "backup", "backup"],
        )
        health = gateway.health_snapshot()
        self.assertEqual(health["primary"]["status"], "open")
        self.assertEqual(health["backup"]["fallback_uses"], 2)

    async def test_content_policy_failure_does_not_try_another_model(self) -> None:
        primary = profile("primary")
        backup = profile("backup")
        catalog = ModelCatalog(
            {primary.name: primary, backup.name: backup},
            default_profile=primary.name,
        )

        class RefusingProvider(RecordingProvider):
            async def create_completion(self, selected, **kwargs):
                self.calls.append((selected, kwargs))
                raise LLMProviderError(
                    selected.provider,
                    400,
                    "content_filter: safety policy",
                )

        provider = RefusingProvider()
        gateway = LLMGateway(
            {"openai-chat": provider},
            catalog=catalog,
        )

        with self.assertRaises(LLMProviderError):
            await gateway.create_completion(primary, messages=[])
        self.assertEqual(
            [item[0].name for item in provider.calls],
            ["primary"],
        )

    async def test_concurrent_requests_keep_their_own_profiles(self) -> None:
        first = profile("first")
        second = profile(
            "second",
            protocol="anthropic-messages",
            provider="anthropic",
        )
        catalog = ModelCatalog(
            {first.name: first, second.name: second},
            default_profile=first.name,
        )

        class ConcurrentGateway:
            def __init__(self):
                self.seen = []

            async def create_completion(self, selected, **kwargs):
                await asyncio.sleep(0)
                self.seen.append((selected.name, kwargs["model"]))
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=selected.name)
                        )
                    ],
                    usage=None,
                )

        gateway = ConcurrentGateway()
        agent = importlib.import_module("src.plugins.ai_chat.deepseek")
        with (
            patch.object(agent, "_model_catalog", catalog),
            patch.object(agent, "_llm_gateway", gateway),
        ):
            answers = await asyncio.gather(
                agent.ask_deepseek("one", [], profile=first),
                agent.ask_deepseek("two", [], profile=second),
            )

        self.assertEqual(answers, ["first", "second"])
        self.assertCountEqual(
            gateway.seen,
            [("first", "model-first"), ("second", "model-second")],
        )

    async def test_gateway_routes_by_protocol_and_closes_providers(self) -> None:
        openai_provider = RecordingProvider()
        anthropic_provider = RecordingProvider()
        gateway = LLMGateway(
            {
                "openai-chat": openai_provider,
                "anthropic-messages": anthropic_provider,
            }
        )

        await gateway.create_completion(profile("one"), messages=[])
        await gateway.create_completion(
            profile("two", protocol="anthropic-messages"),
            messages=[],
        )
        await gateway.close()

        self.assertEqual(len(openai_provider.calls), 1)
        self.assertEqual(len(anthropic_provider.calls), 1)
        self.assertTrue(openai_provider.closed)
        self.assertTrue(anthropic_provider.closed)

    async def test_gateway_enforces_declared_capabilities(self) -> None:
        provider = RecordingProvider()
        gateway = LLMGateway({"openai-chat": provider})
        text_only = profile(
            "text",
            capabilities=ModelCapabilities(
                tools=False,
                streaming=False,
                json_mode=False,
                model_listing=False,
            ),
        )

        with self.assertRaisesRegex(LLMConfigError, "tool calls"):
            await gateway.create_completion(
                text_only,
                messages=[],
                tools=[{"type": "function"}],
            )
        with self.assertRaisesRegex(LLMConfigError, "streaming"):
            await gateway.create_completion(text_only, messages=[], stream=True)
        with self.assertRaisesRegex(LLMConfigError, "JSON mode"):
            await gateway.create_completion(
                text_only,
                messages=[],
                response_format={"type": "json_object"},
            )
        await gateway.close()

    async def test_openai_clients_are_separated_by_endpoint_and_secret(self) -> None:
        first_client = AsyncMock()
        second_client = AsyncMock()
        provider = OpenAIChatProvider()
        first = profile("first", api_key="key-one")
        same_connection = profile("second", api_key="key-one")
        other_secret = profile("third", api_key="key-two")

        with patch(
            "src.plugins.ai_chat.llm_gateway.AsyncOpenAI",
            side_effect=[first_client, second_client],
        ) as constructor:
            self.assertIs(provider._client(first), first_client)
            self.assertIs(provider._client(same_connection), first_client)
            self.assertIs(provider._client(other_secret), second_client)

        self.assertEqual(constructor.call_count, 2)
        await provider.close()

    async def test_anthropic_provider_translates_tool_round_trip(self) -> None:
        selected = profile(
            "claude",
            protocol="anthropic-messages",
            provider="anthropic",
            base_url="https://api.anthropic.test",
        )
        payload = {
            "id": "msg_1",
            "model": selected.model,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "content": [
                {"type": "text", "text": "我先查一下。"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {"query": "hello"},
                },
            ],
        }
        response = httpx.Response(200, json=payload)
        client = SimpleNamespace(
            post=AsyncMock(return_value=response),
            aclose=AsyncMock(),
        )
        provider = AnthropicMessagesProvider()
        provider._clients[selected.client_cache_key] = client
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look something up",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]

        result = await provider.create_completion(
            selected,
            model=selected.model,
            messages=[
                {"role": "system", "content": "system rule"},
                {"role": "user", "content": "hello"},
            ],
            tools=tools,
            tool_choice="auto",
        )

        request = client.post.await_args.kwargs
        self.assertEqual(request["json"]["system"], "system rule")
        self.assertEqual(request["json"]["tools"][0]["input_schema"]["type"], "object")
        self.assertEqual(request["headers"]["x-api-key"], "key")
        self.assertEqual(result.choices[0].message.content, "我先查一下。")
        call = result.choices[0].message.tool_calls[0]
        self.assertEqual(call.function.name, "lookup")
        self.assertEqual(json.loads(call.function.arguments), {"query": "hello"})
        self.assertEqual(result.usage.total_tokens, 18)
        await provider.close()

    async def test_anthropic_rate_limit_is_normalized(self) -> None:
        selected = profile(
            "claude",
            protocol="anthropic-messages",
            provider="anthropic",
        )
        client = SimpleNamespace(
            post=AsyncMock(
                return_value=httpx.Response(
                    429,
                    json={"error": {"type": "rate_limit_error"}},
                )
            ),
            aclose=AsyncMock(),
        )
        provider = AnthropicMessagesProvider()
        provider._clients[selected.client_cache_key] = client

        with self.assertRaises(LLMRateLimitError):
            await provider.create_completion(
                selected,
                messages=[{"role": "user", "content": "hello"}],
            )
        await provider.close()


class AnthropicConversionTests(unittest.TestCase):
    def test_tool_results_and_forced_choice_use_anthropic_shapes(self) -> None:
        selected = profile(
            "claude",
            protocol="anthropic-messages",
            provider="anthropic",
            thinking="enabled",
        )
        request = _anthropic_request(
            selected,
            {
                "messages": [
                    {"role": "system", "content": "rules"},
                    {"role": "user", "content": "question"},
                    {
                        "role": "assistant",
                        "content": "checking",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"query":"x"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": '{"ok":true}',
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "lookup"},
                },
            },
        )

        self.assertEqual(request["tool_choice"], {"type": "tool", "name": "lookup"})
        self.assertNotIn("thinking", request)
        assistant = request["messages"][1]
        self.assertEqual(assistant["content"][1]["type"], "tool_use")
        tool_result = request["messages"][2]
        self.assertEqual(tool_result["content"][0]["type"], "tool_result")

    def test_response_adapter_emits_openai_style_message(self) -> None:
        response = _anthropic_response(
            {
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "stop_reason": "end_turn",
            }
        )

        self.assertEqual(response.choices[0].message.content, "done")
        self.assertEqual(response.choices[0].message.tool_calls, [])
        self.assertEqual(response.usage.total_tokens, 5)


if __name__ == "__main__":
    unittest.main()
