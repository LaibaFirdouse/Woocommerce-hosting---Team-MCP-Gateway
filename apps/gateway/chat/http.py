"""HTTP route for POST /chat (JWT session only)."""

from __future__ import annotations

import logging
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from auth.exceptions import ValidationError
from auth.http import _authenticated_user, _read_json_body
from auth.principal import AuthMethod, GatewayPrincipal
from chat.agent import ChatTimeoutError, run_chat_agent
from chat.llm import (
    ChatLLMError,
    LLMClient,
    LLMConfigurationError,
    load_llm_config,
)

logger = logging.getLogger(__name__)

MAX_MESSAGES = 40
MAX_CONTENT_CHARS = 8_000
ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant"})

# Test hooks (module-level so fixtures can replace without rebinding routes).
_llm_client_factory: Callable[[], LLMClient] | None = None
_ensure_runtime_ready: Callable[[], Any] | None = None


def _get_llm_client() -> LLMClient:
    if _llm_client_factory is not None:
        return _llm_client_factory()
    return LLMClient(load_llm_config())


def _validate_messages(data: dict[str, Any]) -> list[dict[str, str]]:
    """
    Validate client messages. Ignores/forbids client-supplied identity and
    routing fields — they are never used for authorization or tool routing.
    """
    if "messages" not in data:
        raise ValidationError("messages is required.")
    raw = data.get("messages")
    if not isinstance(raw, list):
        raise ValidationError("messages must be an array.")
    if len(raw) == 0:
        raise ValidationError("messages must contain at least one user message.")
    if len(raw) > MAX_MESSAGES:
        raise ValidationError(f"messages must contain at most {MAX_MESSAGES} items.")

    messages: list[dict[str, str]] = []
    has_user = False
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError(f"messages[{index}] must be an object.")
        role = item.get("role")
        content = item.get("content")
        if role not in ALLOWED_MESSAGE_ROLES:
            raise ValidationError(
                f"messages[{index}].role must be 'user' or 'assistant'."
            )
        if not isinstance(content, str):
            raise ValidationError(f"messages[{index}].content must be a string.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValidationError(
                f"messages[{index}].content exceeds {MAX_CONTENT_CHARS} characters."
            )
        if role == "user":
            has_user = True
        messages.append({"role": str(role), "content": content})

    if not has_user:
        raise ValidationError("messages must contain at least one user message.")
    return messages


def register_chat_routes(
    mcp,
    *,
    ensure_runtime_ready: Callable[[], Any] | None = None,
) -> None:
    global _ensure_runtime_ready
    if ensure_runtime_ready is not None:
        _ensure_runtime_ready = ensure_runtime_ready

    @mcp.custom_route("/chat", methods=["POST"])
    async def post_chat(request: Request) -> Response:
        auth = _authenticated_user(request)
        if isinstance(auth, JSONResponse):
            return auth

        # Principal from DB-backed user only — body user_id/role are ignored.
        principal = GatewayPrincipal.from_user(auth, auth_method=AuthMethod.jwt)

        data = await _read_json_body(request)
        try:
            messages = _validate_messages(data)
        except ValidationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

        llm: LLMClient | None = None
        try:
            try:
                llm = _get_llm_client()
            except LLMConfigurationError:
                return JSONResponse(
                    {"detail": "Chat is not configured on this server."},
                    status_code=503,
                )

            # Dashboard /chat never enters the MCP lifespan; ensure proxies exist.
            if _ensure_runtime_ready is not None:
                await _ensure_runtime_ready()

            result = await run_chat_agent(
                messages=messages,
                principal=principal,
                llm=llm,
                mcp=mcp,
            )
        except ChatTimeoutError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=504)
        except ChatLLMError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502)
        except Exception:
            logger.exception("Chat request failed")
            return JSONResponse(
                {"detail": "Unable to complete the chat request."},
                status_code=503,
            )
        finally:
            if llm is not None:
                try:
                    await llm.aclose()
                except Exception:
                    logger.warning("Failed to close LLM client", exc_info=True)

        return JSONResponse(
            {
                "message": result["message"],
                "tool_activity": result["tool_activity"],
                "rounds": result["rounds"],
            }
        )
