"""
Server-side LLM tool-calling loop for POST /chat.

Tool definitions and execution are entirely server-controlled.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from audit.service import sanitize_error_message
from auth.principal import GatewayPrincipal
from chat.grounding import (
    compact_tool_result,
    grounded_answer_from_tool_activity,
)
from chat.invoke import (
    ChatToolInvocationError,
    chat_tools_for_principal,
    invoke_chat_tool,
)
from chat.llm import ChatLLMError, LLMClient, LLMConfig
from servers.registry import is_tool_callable

logger = logging.getLogger(__name__)

MAX_RESULT_PREVIEW_CHARS = 1_200


def _monotonic() -> float:
    """Isolated for tests — do not patch stdlib ``time.monotonic`` globally."""
    return time.monotonic()


# Shared with the post-tool recovery nudge so small local models see the same rules
# when they finally write prose. No tool names or sample business values.
TOOL_GROUNDING_RULES = (
    "After tools run, tool JSON is the only source of truth. "
    "Copy every numeric and string field exactly — never change values. "
    "If a field is absent from tool results, say it was not provided; "
    "do not invent it or treat missing as zero unless the tool returned 0. "
    "Do not infer facts that tools did not return. "
    "Prefer a short structured answer that repeats fields present in the "
    "tool JSON. "
    "Never invent entities, identifiers, quantities, locations, prices, "
    "or credentials."
)

SYSTEM_PROMPT = (
    "You are the Urumi assistant for store operations. "
    "Use the provided tools to answer questions. "
    "When the user asks for data tools can retrieve, call the relevant "
    "tools before answering. For multi-part questions, call every needed "
    "tool from those available. "
    "Only call tools that are available to you. "
    f"{TOOL_GROUNDING_RULES}"
)

ANSWER_FROM_TOOLS_NUDGE = (
    "Using only the tool results above, answer the user concisely. "
    f"{TOOL_GROUNDING_RULES}"
)


class ChatAgentError(RuntimeError):
    """Safe failure during a chat turn (timeout, provider, validation)."""


class ChatTimeoutError(ChatAgentError):
    """Overall chat turn exceeded CHAT_TIMEOUT_SECONDS."""


def build_chat_tool_definitions(
    mcp: Any | None,
    *,
    principal: GatewayPrincipal,
) -> list[dict[str, Any]]:
    """OpenAI tool schemas for role-allowlisted tools that are currently callable."""
    definitions: list[dict[str, Any]] = []
    tool_manager = getattr(mcp, "_tool_manager", None) if mcp is not None else None
    registered = getattr(tool_manager, "_tools", {}) if tool_manager is not None else {}

    for name in sorted(chat_tools_for_principal(principal)):
        if not is_tool_callable(name):
            continue
        tool = registered.get(name)
        description = ""
        parameters: dict[str, Any] = {"type": "object", "properties": {}}
        if tool is not None:
            description = (getattr(tool, "description", None) or "").strip()
            raw_params = getattr(tool, "parameters", None)
            if isinstance(raw_params, dict) and raw_params:
                parameters = _openai_parameters(raw_params)
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or f"Call {name}",
                    "parameters": parameters,
                },
            }
        )
    return definitions


def _openai_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Copy a JSON Schema into an OpenAI-compatible function parameters object.

    Keeps type/properties/required; drops noisy vendor keys that confuse small
    local models without changing the callable argument surface.
    """
    out: dict[str, Any] = {"type": schema.get("type") or "object"}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        out["properties"] = properties
    else:
        out["properties"] = {}
    required = schema.get("required")
    if isinstance(required, list) and required:
        out["required"] = required
    return out


def _preview_result(value: Any) -> str:
    compact = compact_tool_result(value)
    try:
        text = compact if isinstance(compact, str) else json.dumps(compact, default=str)
    except TypeError:
        text = str(compact)
    cleaned = sanitize_error_message(text) or ""
    if len(cleaned) > MAX_RESULT_PREVIEW_CHARS:
        cleaned = cleaned[:MAX_RESULT_PREVIEW_CHARS] + "…"
    return cleaned


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChatToolInvocationError("Malformed tool arguments JSON.") from exc
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            raise ChatToolInvocationError("Tool arguments must be a JSON object.")
        return parsed
    raise ChatToolInvocationError("Tool arguments must be a JSON object.")


def _normalize_tool_calls_for_provider(tool_calls: Any) -> list[dict[str, Any]]:
    """
    Normalize provider tool_calls for the next /chat/completions round.

    Ollama's native API may use object ``arguments``; the OpenAI-compatible
    endpoint expects a JSON string. Echoing the wrong shape breaks follow-ups.
    """
    if not isinstance(tool_calls, list):
        return []
    normalized: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        item = dict(call)
        item.setdefault("type", "function")
        fn = item.get("function")
        if isinstance(fn, dict):
            fn_out = dict(fn)
            args = fn_out.get("arguments")
            if isinstance(args, dict):
                fn_out["arguments"] = json.dumps(args)
            elif args is None:
                fn_out["arguments"] = "{}"
            elif not isinstance(args, str):
                fn_out["arguments"] = json.dumps(args, default=str)
            item["function"] = fn_out
        normalized.append(item)
    return normalized


def _assistant_message_from_choice(choice: dict[str, Any]) -> dict[str, Any]:
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ChatLLMError("Language model provider returned an invalid response.")
    tool_calls = _normalize_tool_calls_for_provider(message.get("tool_calls"))
    content = message.get("content")
    # OpenAI-compatible providers (including Ollama /v1) prefer null content
    # when emitting tool_calls; keep non-empty text if the model sent both.
    if tool_calls and (content is None or content == ""):
        content = None
    out: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _ensure_time(deadline: float) -> None:
    if _monotonic() >= deadline:
        raise ChatTimeoutError("The chat request timed out.")


def _message_text(content: Any) -> str:
    """Normalize provider content to stripped plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content).strip()


def _fallback_from_tool_activity(tool_activity: list[dict[str, Any]]) -> str:
    """Safe user-visible summary when the model returns no final text."""
    grounded = grounded_answer_from_tool_activity(tool_activity)
    if grounded:
        return grounded
    if not tool_activity:
        return "I could not generate a response. Please try again."
    lines: list[str] = ["Here are the tool results:"]
    for row in tool_activity:
        name = str(row.get("name") or "tool")
        if row.get("outcome") == "success":
            preview = str(row.get("result_preview") or "").strip()
            lines.append(f"- {name}: {preview}" if preview else f"- {name}: success")
        else:
            err = str(row.get("error") or "failed").strip()
            lines.append(f"- {name}: {err}")
    return "\n".join(lines)


PRIOR_ASSISTANT_PLACEHOLDER = (
    "[Prior assistant reply omitted. Use tools for current facts.]"
)


def _sanitize_client_history(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Drop prior assistant prose from client history.

    The dashboard resends earlier answers; small local models often treat
    invented values from those turns as facts and ignore fresh tool JSON.
    """
    sanitized: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "assistant":
            sanitized.append(
                {"role": "assistant", "content": PRIOR_ASSISTANT_PLACEHOLDER}
            )
        elif role == "user":
            sanitized.append({"role": "user", "content": content})
    return sanitized


EMPTY_MODEL_RESPONSE = "I could not generate a response. Please try again."
TOOL_LIMIT_MESSAGE = (
    "I reached the tool-call limit before finishing. "
    "Please try a simpler question."
)


async def run_chat_agent(
    *,
    messages: list[dict[str, str]],
    principal: GatewayPrincipal,
    llm: LLMClient,
    mcp: Any | None = None,
    session_factory: Callable[[], Any] | None = None,
    invoke_tool: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """
    Run a bounded tool-calling loop and return assistant text + tool activity.

    ``invoke_tool`` defaults to ``invoke_chat_tool`` (injectable for tests).
    """
    config: LLMConfig = llm.config
    invoke = invoke_tool or invoke_chat_tool
    deadline = _monotonic() + config.timeout_seconds

    working: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_sanitize_client_history(messages),
    ]
    tool_activity: list[dict[str, Any]] = []
    rounds = 0
    final_content: str | None = None
    # qwen2.5:1.5b via Ollama occasionally returns finish_reason=stop with
    # empty content and no tool_calls despite tools being offered. Retry once
    # with tool_choice=required (supported by Ollama's OpenAI-compatible API).
    force_required_tools = False
    empty_response_retries = 0

    while rounds < config.max_tool_rounds:
        _ensure_time(deadline)
        rounds += 1
        tools = build_chat_tool_definitions(mcp, principal=principal)
        if rounds == 1:
            names = [t["function"]["name"] for t in tools]
            if names:
                logger.info(
                    "chat offering tools count=%s names=%s user_id=%s",
                    len(names),
                    names,
                    principal.user_id,
                )
            else:
                logger.warning(
                    "chat offering no tools (none callable/allowlisted) user_id=%s",
                    principal.user_id,
                )

        tool_choice: str | None = None
        if tools:
            tool_choice = "required" if force_required_tools else "auto"
            force_required_tools = False

        try:
            response = await llm.chat_completions(
                messages=working,
                tools=tools or None,
                tool_choice=tool_choice,
            )
        except ChatLLMError:
            raise
        except Exception as exc:
            logger.warning("Unexpected LLM failure: %s", type(exc).__name__)
            raise ChatLLMError("Unable to complete the chat request.") from exc

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ChatLLMError(
                "Language model provider returned an invalid response."
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ChatLLMError(
                "Language model provider returned an invalid response."
            )

        assistant_msg = _assistant_message_from_choice(choice)
        working.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []
        text = _message_text(assistant_msg.get("content"))
        requested_names = [
            str((c.get("function") or {}).get("name") or "").strip() or "?"
            for c in tool_calls
            if isinstance(c, dict)
        ]
        logger.info(
            "chat model round=%s content_len=%s tool_calls=%s names=%s "
            "finish=%s user_id=%s",
            rounds,
            len(text),
            len(tool_calls) if isinstance(tool_calls, list) else 0,
            requested_names,
            choice.get("finish_reason"),
            principal.user_id,
        )

        if not tool_calls:
            if text:
                final_content = text
                break

            # Empty stop: no text and no tool_calls.
            logger.warning(
                "chat empty model response rounds=%s tools_offered=%s "
                "prior_tool_results=%s user_id=%s",
                rounds,
                len(tools),
                len(tool_activity),
                principal.user_id,
            )
            working.pop()  # drop blank assistant turn before retry/recovery

            if tools and empty_response_retries < 1:
                empty_response_retries += 1
                force_required_tools = True
                logger.info(
                    "chat retrying with tool_choice=required user_id=%s",
                    principal.user_id,
                )
                continue

            if tool_activity:
                # Recover after the loop with a text-only completion / fallback.
                break

            final_content = EMPTY_MODEL_RESPONSE
            break

        if not isinstance(tool_calls, list):
            raise ChatLLMError(
                "Language model provider returned an invalid response."
            )

        for call in tool_calls:
            _ensure_time(deadline)
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            tool_name = str(fn.get("name") or "").strip()
            call_id = str(call.get("id") or f"call_{rounds}_{tool_name or 'unknown'}")
            started = _monotonic()
            outcome = "error"
            preview = ""
            try:
                args = _parse_tool_arguments(fn.get("arguments"))
                logger.info(
                    "chat executing tool=%s user_id=%s",
                    tool_name or "?",
                    principal.user_id,
                )
                result = await invoke(
                    tool_name=tool_name,
                    arguments=args,
                    principal=principal,
                    session_factory=session_factory,
                )
                outcome = "success"
                preview = _preview_result(result)
                tool_result_content = preview
            except ChatToolInvocationError as exc:
                preview = sanitize_error_message(str(exc)) or "Tool not available."
                tool_result_content = preview
                logger.info(
                    "chat skipped tool=%s user_id=%s reason=%s",
                    tool_name or "?",
                    principal.user_id,
                    preview,
                )
            except Exception as exc:
                preview = (
                    sanitize_error_message(str(exc)) or "Tool execution failed."
                )
                tool_result_content = preview
                logger.warning(
                    "chat tool failed tool=%s user_id=%s err=%s",
                    tool_name or "?",
                    principal.user_id,
                    type(exc).__name__,
                )

            duration_ms = int((_monotonic() - started) * 1000)
            activity: dict[str, Any] = {
                "name": tool_name or "unknown",
                "outcome": outcome,
                "duration_ms": duration_ms,
            }
            if outcome == "success":
                activity["result_preview"] = preview
            else:
                activity["error"] = preview
            tool_activity.append(activity)
            logger.info(
                "chat tool outcome tool=%s outcome=%s duration_ms=%s user_id=%s",
                tool_name or "?",
                outcome,
                duration_ms,
                principal.user_id,
            )

            working.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_result_content,
                }
            )

    if final_content is None:
        _ensure_time(deadline)
        rounds += 1
        try:
            # Nudge small local models that stop after tools without prose.
            recovery_messages = list(working)
            if tool_activity:
                recovery_messages.append(
                    {
                        "role": "user",
                        "content": ANSWER_FROM_TOOLS_NUDGE,
                    }
                )
            response = await llm.chat_completions(
                messages=recovery_messages, tools=None
            )
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                content = msg.get("content") if isinstance(msg, dict) else None
                final_content = _message_text(content)
        except ChatTimeoutError:
            raise
        except ChatLLMError:
            raise
        except Exception as exc:
            logger.warning("Final LLM round failed: %s", type(exc).__name__)
            raise ChatLLMError("Unable to complete the chat request.") from exc

        if not final_content:
            if tool_activity:
                final_content = _fallback_from_tool_activity(tool_activity)
            else:
                final_content = TOOL_LIMIT_MESSAGE

    if not _message_text(final_content):
        logger.warning(
            "chat final answer empty after recovery rounds=%s "
            "tool_results=%s user_id=%s",
            rounds,
            len(tool_activity),
            principal.user_id,
        )
        final_content = (
            _fallback_from_tool_activity(tool_activity)
            if tool_activity
            else EMPTY_MODEL_RESPONSE
        )

    # Prefer a schema-agnostic summary of successful tool previews over model
    # prose so small local models cannot invent conflicting field values.
    grounded = grounded_answer_from_tool_activity(tool_activity)
    if grounded:
        if _message_text(final_content) and _message_text(final_content) != grounded:
            logger.info(
                "chat replacing model prose with grounded tool summary "
                "tools=%s user_id=%s",
                [row.get("name") for row in tool_activity],
                principal.user_id,
            )
        final_content = grounded

    logger.info(
        "chat complete rounds=%s tool_results=%s content_len=%s user_id=%s",
        rounds,
        len(tool_activity),
        len(_message_text(final_content)),
        principal.user_id,
    )

    return {
        "message": {"role": "assistant", "content": final_content},
        "tool_activity": tool_activity,
        "rounds": rounds,
    }
