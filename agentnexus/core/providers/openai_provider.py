"""OpenAI-compatible direct provider using the openai SDK."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from agentnexus.core.providers.base import BaseLLMProvider, StreamResult

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """Direct provider for any OpenAI-compatible API endpoint."""

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        timeout: int = 60,
        parallel_tool_calls: bool | None = None,
        stream_options: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> StreamResult:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

        kwargs: dict[str, Any] = {
            "model": model.split("/", 1)[1] if "/" in model else model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            if parallel_tool_calls is not None:
                kwargs["parallel_tool_calls"] = parallel_tool_calls

        if stream_options:
            kwargs["stream_options"] = stream_options

        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**kwargs)

        result = StreamResult()
        tool_call_bufs: dict[int, dict[str, Any]] = {}

        for chunk in response:
            # Capture usage from any chunk (may appear with empty choices)
            if hasattr(chunk, "usage") and chunk.usage:
                result.usage = {
                    "input_tokens": chunk.usage.prompt_tokens or 0,
                    "output_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }
                # DeepSeek prompt cache hit/miss tokens
                if hasattr(chunk.usage, "prompt_cache_hit_tokens"):
                    result.usage["cache_hit_tokens"] = chunk.usage.prompt_cache_hit_tokens or 0
                    result.usage["cache_miss_tokens"] = chunk.usage.prompt_cache_miss_tokens or 0
                # OpenAI cached_tokens (prompt_tokens_details.cached_tokens)
                elif hasattr(chunk.usage, "prompt_tokens_details") and chunk.usage.prompt_tokens_details:
                    result.usage["cache_hit_tokens"] = getattr(
                        chunk.usage.prompt_tokens_details, "cached_tokens", 0
                    ) or 0

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            content = delta.content or ""
            result.text += content

            if on_token and content:
                on_token(content)

            # Reasoning / thinking content (DeepSeek, o-series)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                result.reasoning_content += rc
                if on_token:
                    on_token(rc, is_reasoning=True)

            # Tool calls
            tc_list = getattr(delta, "tool_calls", None) or []
            for tc in tc_list:
                idx = tc.get("index", 0) if isinstance(tc, dict) else getattr(tc, "index", 0)
                if idx not in tool_call_bufs:
                    tool_call_bufs[idx] = {
                        "id": "",
                        "function": {"name": "", "arguments": ""},
                    }
                buf = tool_call_bufs[idx]
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    buf["id"] = tc_id
                fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                if fn:
                    name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
                    args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)
                    if name:
                        buf["function"]["name"] += name
                    if args:
                        buf["function"]["arguments"] += args

            fr = getattr(chunk.choices[0], "finish_reason", "")
            if fr:
                result.finish_reason = fr

        # Parse accumulated tool calls
        for buf in tool_call_bufs.values():
            if buf["function"]["name"]:
                try:
                    args = json.loads(buf["function"]["arguments"]) if buf["function"]["arguments"] else {}
                except (json.JSONDecodeError, ValueError):
                    args = {}
                result.tool_calls.append({
                    "id": buf["id"],
                    "name": buf["function"]["name"],
                    "arguments": args,
                })

        return result
