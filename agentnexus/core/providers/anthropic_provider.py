"""Anthropic Messages API direct provider (httpx SSE → StreamResult).

Replaces the former LiteLLM fallback for Claude. Handles the Messages wire
shape: top-level system, content-block tool_use/tool_result, thinking deltas.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

from agentnexus.core.providers.base import BaseLLMProvider, StreamResult

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE = "https://api.anthropic.com"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw}
    return {}


def to_anthropic_payload(
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float = 0,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Map OpenAI-shaped messages[] to an Anthropic Messages request body."""
    system_parts: list[str] = []
    out_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            system_parts.append(_content_to_text(content))
            continue
        if role == "tool":
            out_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": str(msg.get("tool_call_id") or ""),
                    "content": _content_to_text(content),
                }],
            })
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = _content_to_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": _parse_tool_arguments(fn.get("arguments")),
                })
            reasoning = msg.get("reasoning_content") or msg.get("thinking")
            if reasoning:
                # Preserve thinking text when replaying history (no signature here).
                blocks.insert(0, {"type": "thinking", "thinking": str(reasoning), "signature": ""})
            out_messages.append({
                "role": "assistant",
                "content": blocks if blocks else _content_to_text(content),
            })
            continue
        out_messages.append({"role": "user", "content": _content_to_text(content)})

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens or 8192,
        "messages": out_messages,
        "temperature": temperature,
        "stream": True,
    }
    if system_parts:
        body["system"] = "\n\n".join(p for p in system_parts if p)

    if tools:
        anth_tools = []
        for tool in tools:
            fn = tool.get("function") if isinstance(tool, dict) else None
            if not fn:
                continue
            anth_tools.append({
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        if anth_tools:
            body["tools"] = anth_tools
            body["tool_choice"] = {"type": "auto"}

    if reasoning_effort and reasoning_effort != "none":
        # Map the shared effort knob to Anthropic's manual thinking budget.
        # (budget_tokens is deprecated on Claude 4.6+ in favor of adaptive+effort;
        # this path still works for 4.5-and-earlier and remains accepted on 4.6.)
        # "none" omits the thinking block entirely — explicit off.
        budget = 1024 if reasoning_effort == "low" else 4096 if reasoning_effort == "medium" else 8192
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}

    return body


class AnthropicMessagesProvider(BaseLLMProvider):
    """Direct provider for Anthropic's /v1/messages API."""

    def __init__(self) -> None:
        self._active_response: Any = None

    def abort_active_stream(self) -> None:
        stream = self._active_response
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        timeout: int = 60,
        parallel_tool_calls: bool | None = None,
        stream_options: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        on_token: Callable[..., None] | None = None,
    ) -> StreamResult:
        if not api_key:
            raise RuntimeError("Anthropic API key is required")
        root = (base_url or _DEFAULT_BASE).rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        url = f"{root}/v1/messages"
        body = to_anthropic_payload(
            messages,
            model=model,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        result = StreamResult()
        tool_bufs: dict[str, dict[str, Any]] = {}
        block_types: dict[int, str] = {}
        stop_reason = ""

        with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout * 10)) as client:
            with client.stream("POST", url, headers=headers, json=body) as response:
                self._active_response = response
                if response.status_code >= 400:
                    raw = response.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"Anthropic HTTP {response.status_code}: {raw[:400]}")
                try:
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type") or ""
                        if etype == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            result.usage["input_tokens"] = int(usage.get("input_tokens") or 0)
                            result.usage["cache_hit_tokens"] = int(
                                usage.get("cache_read_input_tokens") or 0
                            )
                            result.usage["cache_miss_tokens"] = int(
                                usage.get("cache_creation_input_tokens") or 0
                            )
                        elif etype == "content_block_start":
                            index = int(event.get("index") or 0)
                            block = event.get("content_block") or {}
                            block_types[index] = block.get("type") or ""
                            if block.get("type") == "tool_use":
                                tid = str(block.get("id") or f"toolu_{index}")
                                tool_bufs[tid] = {
                                    "id": tid,
                                    "name": str(block.get("name") or ""),
                                    "arguments": "",
                                    "_index": index,
                                }
                            if block.get("type") == "thinking" and block.get("thinking"):
                                result.reasoning_content += str(block["thinking"])
                                if on_token:
                                    on_token(str(block["thinking"]), is_reasoning=True)
                            if block.get("type") == "text" and block.get("text"):
                                result.text += str(block["text"])
                                if on_token:
                                    on_token(str(block["text"]))
                        elif etype == "content_block_delta":
                            index = int(event.get("index") or 0)
                            delta = event.get("delta") or {}
                            dtype = delta.get("type") or ""
                            if dtype == "text_delta":
                                text = str(delta.get("text") or "")
                                result.text += text
                                if on_token and text:
                                    on_token(text)
                            elif dtype == "thinking_delta":
                                think = str(delta.get("thinking") or "")
                                result.reasoning_content += think
                                if on_token and think:
                                    on_token(think, is_reasoning=True)
                            elif dtype == "input_json_delta":
                                partial = str(delta.get("partial_json") or "")
                                for buf in tool_bufs.values():
                                    if buf.get("_index") == index:
                                        buf["arguments"] += partial
                                        break
                        elif etype == "message_delta":
                            delta = event.get("delta") or {}
                            if delta.get("stop_reason"):
                                stop_reason = str(delta["stop_reason"])
                            usage = event.get("usage") or {}
                            if usage.get("output_tokens") is not None:
                                result.usage["output_tokens"] = int(usage.get("output_tokens") or 0)
                        elif etype == "error":
                            err = event.get("error") or {}
                            raise RuntimeError(f"Anthropic stream error: {err}")
                finally:
                    self._active_response = None

        for buf in tool_bufs.values():
            if not buf.get("name"):
                continue
            result.tool_calls.append({
                "id": buf.get("id") or "",
                "name": buf["name"],
                "arguments": _parse_tool_arguments(buf.get("arguments") or "{}"),
            })

        # Normalize stop_reason to Chat-like vocabulary used by StreamResult.truncated
        mapped = {
            "end_turn": "stop",
            "max_tokens": "max_tokens",
            "tool_use": "tool_calls",
            "stop_sequence": "stop",
            "refusal": "content_filter",
        }
        result.finish_reason = mapped.get(stop_reason, stop_reason or ("tool_calls" if result.tool_calls else "stop"))
        if "input_tokens" in result.usage or "output_tokens" in result.usage:
            result.usage.setdefault("input_tokens", 0)
            result.usage.setdefault("output_tokens", 0)
            result.usage["total_tokens"] = (
                result.usage.get("input_tokens", 0) + result.usage.get("output_tokens", 0)
            )
        return result
