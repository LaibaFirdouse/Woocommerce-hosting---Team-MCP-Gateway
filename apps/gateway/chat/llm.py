"""
Minimal OpenAI-compatible chat completions client (httpx).

Configured entirely via environment variables — never hardcode API keys.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOOL_ROUNDS = 6
DEFAULT_CHAT_TIMEOUT_SECONDS = 60.0


class LLMConfigurationError(RuntimeError):
    """Missing or invalid LLM environment configuration."""


class ChatLLMError(RuntimeError):
    """Safe, non-secret failure talking to the LLM provider."""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str
    max_tool_rounds: int
    timeout_seconds: float


def load_llm_config(
    *,
    environ: dict[str, str] | None = None,
) -> LLMConfig:
    env = environ if environ is not None else os.environ
    api_key = (env.get("LLM_API_KEY") or "").strip()
    model = (env.get("LLM_MODEL") or "").strip()
    if not api_key:
        raise LLMConfigurationError("LLM_API_KEY is not configured.")
    if not model:
        raise LLMConfigurationError("LLM_MODEL is not configured.")

    base_url = (env.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")

    try:
        max_rounds = int(env.get("CHAT_MAX_TOOL_ROUNDS") or DEFAULT_MAX_TOOL_ROUNDS)
    except ValueError as exc:
        raise LLMConfigurationError(
            "CHAT_MAX_TOOL_ROUNDS must be a positive integer."
        ) from exc
    if max_rounds < 1:
        raise LLMConfigurationError(
            "CHAT_MAX_TOOL_ROUNDS must be a positive integer."
        )

    try:
        timeout = float(
            env.get("CHAT_TIMEOUT_SECONDS") or DEFAULT_CHAT_TIMEOUT_SECONDS
        )
    except ValueError as exc:
        raise LLMConfigurationError(
            "CHAT_TIMEOUT_SECONDS must be a positive number."
        ) from exc
    if timeout <= 0:
        raise LLMConfigurationError(
            "CHAT_TIMEOUT_SECONDS must be a positive number."
        )

    return LLMConfig(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_tool_rounds=max_rounds,
        timeout_seconds=timeout,
    )


class LLMClient:
    """
    Thin OpenAI-compatible Chat Completions client.

    ``http_client`` may be injected for tests (mock transport / AsyncClient).
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._owns_client = http_client is None

    @property
    def config(self) -> LLMConfig:
        return self._config

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout_seconds),
            )
            self._owns_client = True
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def chat_completions(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        POST /chat/completions and return the parsed JSON body.

        Never logs the API key or Authorization header.
        """
        url = f"{self._config.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client().post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("LLM request timed out model=%s", self._config.model)
            raise ChatLLMError("The language model request timed out.") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "LLM transport error model=%s err=%s",
                self._config.model,
                type(exc).__name__,
            )
            raise ChatLLMError("Unable to reach the language model provider.") from exc

        if response.status_code >= 400:
            # Do not echo provider bodies (may contain request fragments).
            logger.warning(
                "LLM provider returned HTTP %s model=%s",
                response.status_code,
                self._config.model,
            )
            raise ChatLLMError(
                f"Language model provider returned HTTP {response.status_code}."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ChatLLMError(
                "Language model provider returned an invalid response."
            ) from exc

        if not isinstance(data, dict):
            raise ChatLLMError(
                "Language model provider returned an invalid response."
            )
        return data
