"""Tests for OpenAI-compatible LLM client configuration and HTTP handling."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from chat.llm import (
    ChatLLMError,
    LLMClient,
    LLMConfigurationError,
    load_llm_config,
)


def test_load_llm_config_from_env() -> None:
    cfg = load_llm_config(
        environ={
            "LLM_API_KEY": "sk-test-key",
            "LLM_MODEL": "gpt-4o-mini",
            "LLM_BASE_URL": "https://example.test/v1/",
            "CHAT_MAX_TOOL_ROUNDS": "4",
            "CHAT_TIMEOUT_SECONDS": "30",
        }
    )
    assert cfg.api_key == "sk-test-key"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.base_url == "https://example.test/v1"
    assert cfg.max_tool_rounds == 4
    assert cfg.timeout_seconds == 30.0


def test_load_llm_config_defaults() -> None:
    cfg = load_llm_config(environ={"LLM_API_KEY": "sk-x", "LLM_MODEL": "m"})
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.max_tool_rounds == 6
    assert cfg.timeout_seconds == 60.0


def test_load_llm_config_missing_key() -> None:
    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
        load_llm_config(environ={"LLM_MODEL": "m"})


def test_load_llm_config_invalid_rounds() -> None:
    with pytest.raises(LLMConfigurationError, match="CHAT_MAX_TOOL_ROUNDS"):
        load_llm_config(
            environ={
                "LLM_API_KEY": "sk",
                "LLM_MODEL": "m",
                "CHAT_MAX_TOOL_ROUNDS": "0",
            }
        )


def test_chat_completions_success_and_no_secret_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        body = json.loads(request.content.decode())
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hello",
                        }
                    }
                ],
            },
        )

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        cfg = load_llm_config(
            environ={
                "LLM_API_KEY": "sk-super-secret",
                "LLM_MODEL": "gpt-test",
                "LLM_BASE_URL": "https://llm.test/v1",
            }
        )
        llm = LLMClient(cfg, http_client=client)

        with caplog.at_level(logging.DEBUG):
            data = await llm.chat_completions(
                messages=[{"role": "user", "content": "hi"}],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "get_inventory"},
                    }
                ],
            )

        assert data["choices"][0]["message"]["content"] == "hello"
        assert captured["url"] == "https://llm.test/v1/chat/completions"
        assert captured["authorization"] == "Bearer sk-super-secret"
        body = captured["body"]
        assert isinstance(body, dict)
        assert body["model"] == "gpt-test"
        assert body["tools"][0]["function"]["name"] == "get_inventory"

        log_text = " ".join(
            f"{record.getMessage()} {record.exc_text or ''}"
            for record in caplog.records
        )
        assert "sk-super-secret" not in log_text

        await llm.aclose()

    asyncio.run(_run())


def test_chat_completions_provider_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        cfg = load_llm_config(
            environ={
                "LLM_API_KEY": "sk",
                "LLM_MODEL": "m",
                "LLM_BASE_URL": "https://x/v1",
            }
        )
        llm = LLMClient(cfg, http_client=client)

        with pytest.raises(ChatLLMError, match="HTTP 500") as exc_info:
            await llm.chat_completions(
                messages=[{"role": "user", "content": "hi"}]
            )

        assert "boom" not in str(exc_info.value)
        await llm.aclose()

    asyncio.run(_run())


def test_chat_completions_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        cfg = load_llm_config(
            environ={
                "LLM_API_KEY": "sk",
                "LLM_MODEL": "m",
                "LLM_BASE_URL": "https://x/v1",
            }
        )
        llm = LLMClient(cfg, http_client=client)

        with pytest.raises(ChatLLMError, match="timed out"):
            await llm.chat_completions(
                messages=[{"role": "user", "content": "hi"}]
            )

        await llm.aclose()

    asyncio.run(_run())
