"""Unit tests for the chat agent tool-calling loop."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.principal import AuthMethod, GatewayPrincipal
from chat.agent import (
    ANSWER_FROM_TOOLS_NUDGE,
    PRIOR_ASSISTANT_PLACEHOLDER,
    SYSTEM_PROMPT,
    TOOL_GROUNDING_RULES,
    build_chat_tool_definitions,
    run_chat_agent,
    _normalize_tool_calls_for_provider,
    _parse_tool_arguments,
    _preview_result,
    _sanitize_client_history,
)
from chat.grounding import compact_tool_result, grounded_answer_from_tool_activity
from chat.invoke import ChatToolInvocationError
from chat.llm import ChatLLMError, LLMClient, LLMConfig
from models.user import User, UserRole


def _principal(*, role: UserRole = UserRole.member) -> GatewayPrincipal:
    user = User(
        id=3,
        email="agent@example.com",
        password_hash="x",
        role=role,
        is_active=True,
    )
    return GatewayPrincipal.from_user(user, auth_method=AuthMethod.jwt)


def _llm_config(**overrides: Any) -> LLMConfig:
    base = {
        "api_key": "sk-test",
        "model": "qwen-test",
        "base_url": "http://127.0.0.1:11434/v1",
        "max_tool_rounds": 6,
        "timeout_seconds": 60.0,
    }
    base.update(overrides)
    return LLMConfig(**base)


def _mock_llm(responses: list[dict[str, Any]], *, config: LLMConfig | None = None) -> LLMClient:
    client = MagicMock(spec=LLMClient)
    client.config = config or _llm_config()
    client.chat_completions = AsyncMock(side_effect=list(responses))
    return client


def _mcp_with_tools(schemas: dict[str, dict[str, Any]]) -> MagicMock:
    tools = {}
    for name, schema in schemas.items():
        tool = MagicMock()
        tool.description = schema.get("description", f"Tool {name}")
        tool.parameters = schema.get("parameters", {"type": "object", "properties": {}})
        tools[name] = tool
    mcp = MagicMock()
    mcp._tool_manager._tools = tools
    return mcp


def test_build_chat_tool_definitions_only_callable_allowlisted() -> None:
    mcp = _mcp_with_tools(
        {
            "get_inventory": {
                "description": "Inventory lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"product_id": {"type": "integer"}},
                    "required": ["product_id"],
                    "additionalProperties": False,
                    "title": "GetInventoryArgs",
                },
            },
            "create_product": {
                "description": "Admin-only mutating tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    )

    def callable_only(name: str) -> bool:
        # create_product is callable in registry but not offered to members.
        return name in {"get_inventory", "create_product"}

    with patch("chat.agent.is_tool_callable", side_effect=callable_only):
        defs = build_chat_tool_definitions(mcp, principal=_principal())

    names = [d["function"]["name"] for d in defs]
    assert names == ["get_inventory"]
    params = defs[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert "product_id" in params["properties"]
    assert params["required"] == ["product_id"]
    assert "additionalProperties" not in params
    assert "title" not in params


def test_build_chat_tool_definitions_offers_create_product_to_admin() -> None:
    mcp = _mcp_with_tools(
        {
            "get_inventory": {"description": "inv", "parameters": {"type": "object"}},
            "create_product": {
                "description": "Create product",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "regular_price": {"type": "string"},
                    },
                    "required": ["name", "regular_price"],
                },
            },
        }
    )

    with patch("chat.agent.is_tool_callable", return_value=True):
        defs = build_chat_tool_definitions(
            mcp, principal=_principal(role=UserRole.admin)
        )

    names = [d["function"]["name"] for d in defs]
    assert "create_product" in names
    assert "get_inventory" in names


def test_build_chat_tool_definitions_empty_when_none_callable() -> None:
    mcp = _mcp_with_tools({"get_inventory": {"description": "x"}})
    with patch("chat.agent.is_tool_callable", return_value=False):
        assert build_chat_tool_definitions(mcp, principal=_principal()) == []


def test_normalize_tool_calls_stringifies_object_arguments() -> None:
    normalized = _normalize_tool_calls_for_provider(
        [
            {
                "id": "call_1",
                "function": {
                    "name": "get_inventory",
                    "arguments": {"product_id": 22},
                },
            }
        ]
    )
    assert normalized[0]["type"] == "function"
    assert normalized[0]["function"]["arguments"] == '{"product_id": 22}'


def test_parse_tool_arguments_accepts_string_and_object() -> None:
    assert _parse_tool_arguments('{"product_id": 22}') == {"product_id": 22}
    assert _parse_tool_arguments({"product_id": 22}) == {"product_id": 22}
    with pytest.raises(ChatToolInvocationError):
        _parse_tool_arguments("[1]")
    with pytest.raises(ChatToolInvocationError):
        _parse_tool_arguments("{")


def test_compact_and_preview_keeps_scalar_ids_visible() -> None:
    """Fixture payload: scalars survive compaction; nested blobs are dropped."""
    bulky = {
        "result": [
            {
                "id": 7,
                "name": "Fixture Alpha",
                "slug": "fixture-alpha",
                "permalink": "https://example.test/item/fixture-alpha/",
                "description": "x" * 2000,
                "status": "publish",
                "sku": "",
            }
        ]
    }
    compact = compact_tool_result(bulky)
    assert compact["count"] == 1
    assert compact["result"][0]["id"] == 7
    assert compact["result"][0]["name"] == "Fixture Alpha"
    # Oversized string scalars are dropped; short scalars kept.
    assert "description" not in compact["result"][0]
    preview = _preview_result(bulky)
    assert "7" in preview
    assert "Fixture Alpha" in preview
    assert "x" * 200 not in preview


def test_run_chat_agent_passes_tools_and_uses_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mcp = _mcp_with_tools(
        {
            "list_products": {
                "description": "List products",
                "parameters": {"type": "object", "properties": {}},
            },
            "get_inventory": {
                "description": "Get inventory",
                "parameters": {
                    "type": "object",
                    "properties": {"product_id": {"type": "integer"}},
                },
            },
        }
    )
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {
                                        "name": "list_products",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": {"product_id": 22},
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Products include MCP Test Product. "
                                "Product 22 has 12 units in Mumbai Central."
                            ),
                        }
                    }
                ]
            },
        ]
    )

    invoked: list[dict[str, Any]] = []

    async def fake_invoke(*, tool_name, arguments, principal, session_factory=None):
        invoked.append({"name": tool_name, "arguments": arguments})
        if tool_name == "list_products":
            return {"products": [{"id": 22, "name": "MCP Test Product"}]}
        return {
            "inventory": [
                {
                    "product_id": 22,
                    "available_quantity": 12,
                    "warehouse": "Mumbai Central",
                }
            ]
        }

    with (
        patch("chat.agent.is_tool_callable", return_value=True),
        caplog.at_level(logging.INFO),
    ):
        result = asyncio.run(
            run_chat_agent(
                messages=[
                    {
                        "role": "user",
                        "content": "List products and check inventory for product ID 22.",
                    }
                ],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    # Server-grounded answer from tool previews (not raw model prose).
    assert "available_quantity=12" in result["message"]["content"]
    assert "MCP Test Product" in result["message"]["content"]
    assert result["rounds"] == 2
    assert [row["name"] for row in result["tool_activity"]] == [
        "list_products",
        "get_inventory",
    ]
    assert all(row["outcome"] == "success" for row in result["tool_activity"])
    assert invoked[1]["arguments"] == {"product_id": 22}

    first_call = llm.chat_completions.await_args_list[0]
    assert first_call.kwargs["tool_choice"] == "auto"
    tool_names = [t["function"]["name"] for t in first_call.kwargs["tools"]]
    assert "list_products" in tool_names
    assert "get_inventory" in tool_names

    # Follow-up messages include normalized tool_calls + tool results.
    second_messages = llm.chat_completions.await_args_list[1].kwargs["messages"]
    assistant_with_tools = next(
        m for m in second_messages if m.get("tool_calls")
    )
    assert isinstance(
        assistant_with_tools["tool_calls"][1]["function"]["arguments"], str
    )
    assert json.loads(
        assistant_with_tools["tool_calls"][1]["function"]["arguments"]
    ) == {"product_id": 22}
    tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert "Mumbai Central" in tool_msgs[1]["content"]

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "offering tools" in log_text
    assert "get_inventory" in log_text


def test_run_chat_agent_warns_when_no_tools_offered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I can explain inventory in general.",
                        }
                    }
                ]
            }
        ]
    )
    with (
        patch("chat.agent.is_tool_callable", return_value=False),
        caplog.at_level(logging.WARNING),
    ):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "stock for 22?"}],
                principal=_principal(),
                llm=llm,
                mcp=_mcp_with_tools({}),
            )
        )

    assert result["tool_activity"] == []
    assert llm.chat_completions.await_args.kwargs["tools"] is None
    assert llm.chat_completions.await_args.kwargs["tool_choice"] is None
    assert any("offering no tools" in r.getMessage() for r in caplog.records)


def test_run_chat_agent_malformed_arguments_surface_as_tool_error() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": "{not-json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I could not read that tool call.",
                        }
                    }
                ]
            },
        ]
    )

    async def must_not_run(**kwargs):
        raise AssertionError("invoke must not run for malformed args")

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "stock"}],
                principal=_principal(),
                llm=llm,
                mcp=_mcp_with_tools({"get_inventory": {}}),
                invoke_tool=must_not_run,
            )
        )

    assert result["tool_activity"][0]["outcome"] == "error"
    assert "Malformed" in result["tool_activity"][0]["error"]


def test_run_chat_agent_blocks_create_product_for_member() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "create_product",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I cannot create products.",
                        }
                    }
                ]
            },
        ]
    )

    async def real_style_invoke(*, tool_name, arguments, principal, session_factory=None):
        raise ChatToolInvocationError(
            f"Tool {tool_name!r} is not allowed for chat."
        )

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "create"}],
                principal=_principal(role=UserRole.member),
                llm=llm,
                mcp=_mcp_with_tools({"create_product": {}}),
                invoke_tool=real_style_invoke,
            )
        )

    assert result["tool_activity"][0]["outcome"] == "error"
    assert "not allowed" in result["tool_activity"][0]["error"]
    assert "result_preview" not in result["tool_activity"][0]


def test_run_chat_agent_allows_create_product_for_admin() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "create_product",
                                        "arguments": (
                                            '{"name":"Admin Demo Cup",'
                                            '"regular_price":"12.50"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Created the product.",
                        }
                    }
                ]
            },
        ]
    )

    async def admin_invoke(*, tool_name, arguments, principal, session_factory=None):
        assert principal.role is UserRole.admin
        assert tool_name == "create_product"
        assert arguments["name"] == "Admin Demo Cup"
        return {"id": 55, "name": arguments["name"]}

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "create a product"}],
                principal=_principal(role=UserRole.admin),
                llm=llm,
                mcp=_mcp_with_tools({"create_product": {}}),
                invoke_tool=admin_invoke,
            )
        )

    assert result["tool_activity"][0]["outcome"] == "success"
    assert result["tool_activity"][0]["name"] == "create_product"


def test_run_chat_agent_provider_error_propagates() -> None:
    llm = _mock_llm([])
    llm.chat_completions = AsyncMock(
        side_effect=ChatLLMError("Language model provider returned HTTP 500.")
    )
    with pytest.raises(ChatLLMError, match="HTTP 500"):
        asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "hi"}],
                principal=_principal(),
                llm=llm,
                mcp=None,
            )
        )


def test_run_chat_agent_retries_empty_stop_with_tool_choice_required() -> None:
    """
    Regression: qwen2.5:1.5b via Ollama sometimes returns finish_reason=stop
    with empty content and no tool_calls. The agent must retry with
    tool_choice=required and never return blank text to the dashboard.
    """
    mcp = _mcp_with_tools(
        {
            "get_inventory": {
                "description": "Get inventory",
                "parameters": {
                    "type": "object",
                    "properties": {"product_id": {"type": "integer"}},
                },
            }
        }
    )
    llm = _mock_llm(
        [
            # First response: empty stop (reproduces dashboard "(No response text.)")
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": ""},
                    }
                ]
            },
            # Retry with tool_choice=required
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": '{"product_id":22}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Product 22 has 12 units in Mumbai Central."
                            ),
                        }
                    }
                ]
            },
        ]
    )

    async def fake_invoke(*, tool_name, arguments, principal, session_factory=None):
        assert tool_name == "get_inventory"
        assert arguments == {"product_id": 22}
        return {
            "inventory": [
                {
                    "product_id": 22,
                    "available_quantity": 12,
                    "warehouse": "Mumbai Central",
                }
            ]
        }

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "List the WooCommerce products and check the "
                            "inventory for product ID 22."
                        ),
                    }
                ],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    assert result["message"]["content"].strip()
    assert "12" in result["message"]["content"]
    assert result["tool_activity"][0]["name"] == "get_inventory"
    assert result["tool_activity"][0]["outcome"] == "success"

    choices = [c.kwargs.get("tool_choice") for c in llm.chat_completions.await_args_list]
    assert choices[0] == "auto"
    assert choices[1] == "required"


def test_run_chat_agent_empty_after_tools_uses_fallback_not_blank() -> None:
    mcp = _mcp_with_tools(
        {
            "get_inventory": {
                "description": "Get inventory",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    )
    empty_stop = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": ""},
            }
        ]
    }
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": '{"product_id":22}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            # Post-tool round: empty stop → one required retry
            empty_stop,
            empty_stop,
            # Text-only recovery also empty → fallback from tool activity
            empty_stop,
        ]
    )

    async def fake_invoke(**kwargs):
        return {
            "inventory": [
                {
                    "product_id": 22,
                    "available_quantity": 12,
                    "warehouse": "Mumbai Central",
                }
            ]
        }

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "stock for 22?"}],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    content = result["message"]["content"]
    assert content.strip()
    assert content != "(No response text.)"
    assert "available_quantity=12" in content
    assert "Mumbai Central" in content
    assert result["tool_activity"][0]["outcome"] == "success"


def test_run_chat_agent_double_empty_without_tools_returns_safe_message() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": ""},
                    }
                ]
            },
            # required retry also empty / no tools path when none callable
        ]
    )
    with patch("chat.agent.is_tool_callable", return_value=False):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "hello"}],
                principal=_principal(),
                llm=llm,
                mcp=_mcp_with_tools({}),
            )
        )

    assert result["message"]["content"].strip()
    assert "could not generate" in result["message"]["content"].lower()
    assert result["tool_activity"] == []


def test_tool_grounding_rules_forbid_contradictory_field_values() -> None:
    """Prompt must require exact field copy from tool JSON — no sample values."""
    rules = TOOL_GROUNDING_RULES.casefold()
    assert "source of truth" in rules
    assert "exactly" in rules or "never change values" in rules
    assert "not provided" in rules
    assert "do not invent" in rules or "never invent" in rules
    assert "quantities" in rules or "identifiers" in rules
    # No product-/SKU-/qty-specific examples in production prompts.
    assert "available_quantity=12" not in rules
    assert "p-049" not in rules
    assert "mumbai" not in rules
    assert SYSTEM_PROMPT.startswith("You are the Urumi assistant")
    assert TOOL_GROUNDING_RULES in SYSTEM_PROMPT
    assert TOOL_GROUNDING_RULES in ANSWER_FROM_TOOLS_NUDGE


def test_tool_grounding_rules_forbid_unsupported_inferences() -> None:
    rules = TOOL_GROUNDING_RULES.casefold()
    assert "do not infer" in rules
    assert "missing as zero" in rules or "treat missing as zero" in rules
    assert "tool json" in rules or "tool results" in rules


def test_run_chat_agent_sends_grounding_system_prompt() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        }
                    }
                ]
            }
        ]
    )
    with patch("chat.agent.is_tool_callable", return_value=False):
        asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "hello?"}],
                principal=_principal(),
                llm=llm,
                mcp=_mcp_with_tools({}),
            )
        )

    messages = llm.chat_completions.await_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert TOOL_GROUNDING_RULES in messages[0]["content"]
    assert "never invent" in messages[0]["content"].casefold()


def test_run_chat_agent_recovery_nudge_includes_grounding_rules() -> None:
    """When the model returns empty text after tools, recovery must restate grounding."""
    mcp = _mcp_with_tools(
        {
            "get_inventory": {
                "description": "Get inventory",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    )
    empty_stop = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": ""},
            }
        ]
    }
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": '{"product_id":22}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            empty_stop,
            empty_stop,
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "MCP Test Product (id 22): available_quantity 12 "
                                "at Mumbai Central."
                            ),
                        }
                    }
                ]
            },
        ]
    )

    async def fake_invoke(**kwargs):
        return {
            "inventory": [
                {
                    "product_id": 22,
                    "product_name": "MCP Test Product",
                    "available_quantity": 12,
                    "warehouse": "Mumbai Central",
                }
            ]
        }

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "stock for 22?"}],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    # Last LLM call is the text-only recovery with grounding nudge.
    last_messages = llm.chat_completions.await_args_list[-1].kwargs["messages"]
    assert last_messages[-1]["role"] == "user"
    assert last_messages[-1]["content"] == ANSWER_FROM_TOOLS_NUDGE
    assert "source of truth" in last_messages[-1]["content"].casefold()
    assert "never invent" in last_messages[-1]["content"].casefold()
    # Client answer is schema-rendered from tool JSON, not model prose.
    assert "available_quantity=12" in result["message"]["content"]
    assert "get_inventory:" in result["message"]["content"]
    assert "P-049" not in result["message"]["content"]


def test_grounded_answer_reports_tool_json_not_conflicting_values() -> None:
    """Mock fixtures only — production path must not embed these values."""
    activity = [
        {
            "name": "list_items",
            "outcome": "success",
            "result_preview": json.dumps(
                {
                    "count": 2,
                    "result": [
                        {"id": 7, "name": "Fixture Alpha", "sku": "FIX-A"},
                        {"id": 9, "name": "Fixture Beta", "sku": ""},
                    ],
                }
            ),
        },
        {
            "name": "check_stock",
            "outcome": "success",
            "result_preview": json.dumps(
                {
                    "inventory": [
                        {
                            "product_id": 7,
                            "product_name": "Fixture Alpha",
                            "available_quantity": 12,
                            "warehouse": "Zone North",
                        }
                    ]
                }
            ),
        },
    ]
    answer = grounded_answer_from_tool_activity(activity)
    assert answer is not None
    assert answer.startswith("list_items:")
    assert "check_stock:" in answer
    assert "available_quantity=12" in answer
    assert "FIX-A" in answer
    assert "Zone North" in answer
    assert "id=7" in answer
    assert "id=9" in answer
    # Must not invent values absent from tool JSON.
    assert "Test Prod1" not in answer
    assert "P-049" not in answer
    assert "available_quantity=-5" not in answer


def test_run_chat_agent_rejects_model_hallucinated_values() -> None:
    """Model prose inventing values absent from tool JSON must not reach the client."""
    mcp = _mcp_with_tools(
        {
            "list_products": {
                "description": "List",
                "parameters": {"type": "object", "properties": {}},
            },
            "get_inventory": {
                "description": "Inv",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    )
    hallucinated_sku = "FAKE-SKU-999"
    hallucinated_qty = -5
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "a",
                                    "function": {
                                        "name": "list_products",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "b",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": '{"product_id":7}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                f"Items: Ghost One, Ghost Two. "
                                f"Item 7 has SKU {hallucinated_sku} "
                                f"and quantity {hallucinated_qty}."
                            ),
                        }
                    }
                ]
            },
        ]
    )

    async def fake_invoke(*, tool_name, arguments, principal, session_factory=None):
        if tool_name == "list_products":
            return {
                "result": [
                    {"id": 7, "name": "Fixture Alpha", "sku": "FIX-A"},
                    {"id": 9, "name": "Fixture Beta", "sku": ""},
                ]
            }
        return {
            "inventory": [
                {
                    "product_id": 7,
                    "product_name": "Fixture Alpha",
                    "available_quantity": 12,
                    "warehouse": "Zone North",
                }
            ]
        }

    with patch("chat.agent.is_tool_callable", return_value=True):
        result = asyncio.run(
            run_chat_agent(
                messages=[
                    {
                        "role": "user",
                        "content": "List items and check inventory for id 7.",
                    }
                ],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    content = result["message"]["content"]
    assert "available_quantity=12" in content
    assert "FIX-A" in content
    assert "Ghost One" not in content
    assert hallucinated_sku not in content
    assert str(hallucinated_qty) not in content


def test_sanitize_client_history_omits_prior_assistant_prose() -> None:
    sanitized = _sanitize_client_history(
        [
            {"role": "user", "content": "list items"},
            {
                "role": "assistant",
                "content": "Ghost One SKU FAKE-999 quantity -5",
            },
            {"role": "user", "content": "inventory for 7"},
        ]
    )
    assert sanitized[0]["content"] == "list items"
    assert sanitized[1]["content"] == PRIOR_ASSISTANT_PLACEHOLDER
    assert "Ghost One" not in sanitized[1]["content"]
    assert sanitized[2]["content"] == "inventory for 7"


def test_run_chat_agent_does_not_feed_prior_hallucinations_to_model() -> None:
    llm = _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        }
                    }
                ]
            }
        ]
    )
    with patch("chat.agent.is_tool_callable", return_value=False):
        asyncio.run(
            run_chat_agent(
                messages=[
                    {"role": "user", "content": "earlier"},
                    {
                        "role": "assistant",
                        "content": "Ghost One has SKU FAKE-999 and quantity -5",
                    },
                    {"role": "user", "content": "again"},
                ],
                principal=_principal(),
                llm=llm,
                mcp=_mcp_with_tools({}),
            )
        )

    sent = llm.chat_completions.await_args.kwargs["messages"]
    joined = json.dumps(sent)
    assert "Ghost One" not in joined
    assert "FAKE-999" not in joined
    assert PRIOR_ASSISTANT_PLACEHOLDER in joined
