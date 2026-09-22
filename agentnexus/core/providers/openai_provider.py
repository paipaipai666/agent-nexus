"""OpenAI-compatible direct provider using the openai SDK."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from openai import OpenAI

from agentnexus.core.providers.base import BaseLLMProvider, StreamResult

logger = logging.getLogger(__name__)

# Watchdog for hung streams. Free-tier gateways are observed to accept the
# request, return response headers, then hold the SSE connection open forever
# (keep-alive traffic defeats httpx's read timeout, which only fires on *idle*
# sockets). Without a guard, a single hung stream pins its worker thread
# indefinitely and the whole eval batch deadlocks (observed twice in the
# wild). We therefore bound both the gap between chunks and the total stream
# duration; on violation the underlying connection is closed so the pump
# thread unblocks and exits, and the caller gets a normal exception it can
# retry.
_CHUNK_GAP_S = 60
_TOTAL_BUDGET_MULT = 10  # total cap = per-call timeout × this

_SENTINEL = object()

# LiteLLM-style provider prefixes that must be stripped before calling an
# OpenAI-compatible endpoint (the endpoint expects the bare model name).
# Namespaced provider models like SiliconFlow's "deepseek-ai/DeepSeek-V4-Flash"
# or Groq's "llama-3/8b" keep the full string — there the whole name IS the
# endpoint's model id.
_KNOWN_PROVIDER_PREFIXES = {
    "openai", "azure", "deepseek", "anthropic", "zhipu", "glm", "zai",
    "moonshot", "gemini", "google", "bedrock", "vertex_ai", "azure_ai",
    "cohere", "mistral", "huggingface", "replicate", "anyscale", "ollama",
    "groq", "together_ai", "openrouter", "perplexity",
}


def _strip_known_prefix(model: str) -> str:
    if "/" not in model:
        return model
    prefix, _, rest = model.partition("/")
    if prefix.lower() in _KNOWN_PROVIDER_PREFIXES and rest:
        return rest
    return model


class OpenAIProvider(BaseLLMProvider):
    """Direct provider for any OpenAI-compatible API endpoint."""

    def __init__(self) -> None:
        # The in-flight openai Stream; closed by abort_active_stream() when
        # the run's cancel checker fires (产品决策: 取消 = 立刻停止).
        self._active_stream: Any = None

    def abort_active_stream(self) -> None:
        """Close the in-flight stream's HTTP connection, unblocking readers."""
        stream = self._active_stream
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _guarded_iter(self, response: Any, *, timeout: int) -> Iterator[Any]:
        """Yield stream chunks with hung-connection watchdogs.

        A daemon pump thread owns the blocking ``next()``; the consumer side
        enforces (a) no gap between chunks longer than _CHUNK_GAP_S and
        (b) a total budget of timeout × _TOTAL_BUDGET_MULT. On either
        violation the connection is closed (unblocking the pump, which then
        exits) and TimeoutError propagates to the caller for retry.
        """
        q: queue.Queue[Any] = queue.Queue()

        def _pump() -> None:
            try:
                for item in response:
                    q.put(item)
                q.put(_SENTINEL)
            except BaseException as exc:  # noqa: BLE001 — re-raised on consumer side
                q.put(exc)

        threading.Thread(target=_pump, daemon=True).start()
        total_budget = float(timeout) * _TOTAL_BUDGET_MULT
        deadline = time.monotonic() + total_budget

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.abort_active_stream()
                raise TimeoutError(
                    f"stream exceeded total budget {total_budget:.0f}s — treating as hung"
                )
            try:
                item = q.get(timeout=min(_CHUNK_GAP_S, remaining))
            except queue.Empty:
                self.abort_active_stream()
                if deadline - time.monotonic() <= 0:
                    raise TimeoutError(
                        f"stream exceeded total budget {total_budget:.0f}s — treating as hung"
                    )
                raise TimeoutError(f"no stream chunk for {_CHUNK_GAP_S}s — treating as hung")
            if item is _SENTINEL:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

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
        on_token: Callable[[str], None] | None = None,
    ) -> StreamResult:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

        kwargs: dict[str, Any] = {
            "model": _strip_known_prefix(model),
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

        if response_format:
            kwargs["response_format"] = response_format

        if stream_options:
            kwargs["stream_options"] = stream_options

        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**kwargs)
        self._active_stream = response

        result = StreamResult()
        tool_call_bufs: dict[int, dict[str, Any]] = {}

        try:
            for chunk in self._guarded_iter(response, timeout=timeout):
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
                # Some deployments (SiliconFlow DeepSeek-V4) leave thinking-tag
                # residue in delta.content even while reasoning_content is
                # streamed separately — strip it before accumulation.
                content = (delta.content or "").replace("</think>", "").replace("<think>", "")
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
        finally:
            self._active_stream = None

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
