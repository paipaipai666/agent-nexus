"""Anthropic Messages provider codec tests (no network)."""

import json
from unittest.mock import MagicMock, patch

from agentnexus.core.providers.anthropic_provider import (
    AnthropicMessagesProvider,
    to_anthropic_payload,
)
from agentnexus.core.providers.router import select_provider


def test_select_provider_routes_anthropic_com():
    p = select_provider("claude-sonnet-4-6", "https://api.anthropic.com")
    assert isinstance(p, AnthropicMessagesProvider)


def test_select_provider_routes_openai_compatible():
    p = select_provider("deepseek/deepseek-chat", "https://api.deepseek.com")
    assert not isinstance(p, AnthropicMessagesProvider)


def test_select_provider_anthropic_prefix_without_base_uses_openai_codec():
    # No URL sniffing on model id — protocol follows base_url only.
    p = select_provider("anthropic/claude-sonnet-4-6", "")
    assert not isinstance(p, AnthropicMessagesProvider)


def test_to_anthropic_payload_maps_system_and_tools():
    body = to_anthropic_payload(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "noop", "arguments": '{"x": 1}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
        model="anthropic/claude-x",
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}],
        max_tokens=128,
    )
    assert body["model"] == "anthropic/claude-x"  # sent exactly as configured
    assert body["system"] == "Be brief."
    assert body["max_tokens"] == 128
    assert body["tools"][0]["name"] == "noop"
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    types = []
    for m in body["messages"]:
        content = m["content"]
        if isinstance(content, list):
            types.extend(b.get("type") for b in content)
    assert "tool_use" in types
    assert "tool_result" in types


def _sse(*events: dict) -> list[str]:
    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}")
    return lines


def test_stream_chat_parses_text_and_tool_use():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "noop"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"a":'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '1}'}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    tokens = []
    provider = AnthropicMessagesProvider()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _sse(*events)
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = lambda s, *a: False
    mock_client = MagicMock()
    mock_client.stream.return_value = mock_response
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = lambda s, *a: False

    with patch("agentnexus.core.providers.anthropic_provider.httpx.Client", return_value=mock_client):
        result = provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-x",
            api_key="sk-test",
            base_url="https://api.anthropic.com",
            tools=[{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}],
            on_token=lambda *a, **k: tokens.append(a),
        )

    assert result.text == "Hello"
    assert result.tool_calls == [{"id": "toolu_1", "name": "noop", "arguments": {"a": 1}}]
    assert result.finish_reason == "tool_calls"
    assert result.usage["input_tokens"] == 11
    assert result.usage["output_tokens"] == 7
    assert result.truncated is False
    assert tokens


def test_stream_chat_maps_max_tokens_stop():
    events = [
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    provider = AnthropicMessagesProvider()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _sse(*events)
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = lambda s, *a: False
    mock_client = MagicMock()
    mock_client.stream.return_value = mock_response
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = lambda s, *a: False

    with patch("agentnexus.core.providers.anthropic_provider.httpx.Client", return_value=mock_client):
        result = provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-x",
            api_key="sk-test",
            base_url="https://api.anthropic.com",
            max_tokens=16,
        )
    assert result.finish_reason == "max_tokens"
    assert result.truncated is True
