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

from agentnexus.core.providers.base import BaseLLMProvider, StreamResult, remaining_timeout, request_deadline

logger = logging.getLogger(__name__)

_CHUNK_GAP_S = 60
_TOTAL_BUDGET_MULT = 10

_SENTINEL = object()


class OpenAIProvider(BaseLLMProvider):
    """Direct provider for any OpenAI-compatible API endpoint."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: dict[tuple[str, str, int], OpenAI] = {}
        self._active_streams: dict[int, Any] = {}

    def _get_client(self, api_key: str, base_url: str, timeout: int) -> OpenAI:
        key = (base_url.rstrip("/"), api_key, timeout)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = OpenAI(api_key=api_key, base_url=key[0], timeout=timeout)
            return self._clients[key]

    @staticmethod
    def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                logger.debug("Failed to close OpenAI stream", exc_info=True)

    def abort_active_stream(self, thread_id: int | None = None) -> None:
        """Close the in-flight stream's HTTP connection, unblocking readers."""
        with self._lock:
            stream = self._active_streams.get(thread_id if thread_id is not None else threading.get_ident())
        if stream is not None:
            self._close_stream(stream)

    def close(self) -> None:
        """Release cached connections when the application shuts down."""
        with self._lock:
            streams = list(self._active_streams.values())
            clients = list(self._clients.values())
            self._active_streams.clear()
            self._clients.clear()
        for stream in streams:
            self._close_stream(stream)
        for client in clients:
            client.close()

    def _guarded_iter(self, response: Any, *, timeout: int, deadline: float | None = None) -> Iterator[Any]:
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

        threading.Thread(target=_pump, daemon=True, name="openai-stream-pump").start()
        total_budget = float(timeout) * _TOTAL_BUDGET_MULT
        deadline = deadline if deadline is not None else time.monotonic() + total_budget

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._close_stream(response)
                raise TimeoutError(
                    f"stream exceeded total budget {total_budget:.0f}s — treating as hung"
                )
            try:
                item = q.get(timeout=min(_CHUNK_GAP_S, remaining))
            except queue.Empty:
                self._close_stream(response)
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
        deadline: float | None = None,
    ) -> StreamResult:
        client = self._get_client(api_key, base_url, timeout) if deadline is None else OpenAI(
            api_key=api_key, base_url=base_url, timeout=remaining_timeout(deadline), max_retries=0,
        )

        try:
            with request_deadline(deadline, client.close):
                kwargs: dict[str, Any] = {
                    "model": model,
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

                if reasoning_effort is not None:
                    kwargs["reasoning_effort"] = reasoning_effort
                    if reasoning_effort == "none":
                        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                    else:
                        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

                if deadline is not None:
                    kwargs["timeout"] = remaining_timeout(deadline)

                try:
                    response = client.chat.completions.create(**kwargs)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "reasoning_effort" not in msg and "thinking" not in msg:
                        raise
                    kwargs.pop("reasoning_effort", None)
                    kwargs.pop("extra_body", None)
                    response = client.chat.completions.create(**kwargs)
                thread_id = threading.get_ident()
                with self._lock:
                    self._active_streams[thread_id] = response

                result = StreamResult()
                tool_call_bufs: dict[int, dict[str, Any]] = {}

                try:
                    for chunk in self._guarded_iter(response, timeout=timeout, deadline=deadline):
                        if hasattr(chunk, "usage") and chunk.usage:
                            result.usage = {
                                "input_tokens": chunk.usage.prompt_tokens or 0,
                                "output_tokens": chunk.usage.completion_tokens or 0,
                                "total_tokens": chunk.usage.total_tokens or 0,
                            }
                            if hasattr(chunk.usage, "prompt_cache_hit_tokens"):
                                result.usage["cache_hit_tokens"] = chunk.usage.prompt_cache_hit_tokens or 0
                                result.usage["cache_miss_tokens"] = chunk.usage.prompt_cache_miss_tokens or 0
                            elif hasattr(chunk.usage, "prompt_tokens_details") and chunk.usage.prompt_tokens_details:
                                result.usage["cache_hit_tokens"] = getattr(
                                    chunk.usage.prompt_tokens_details, "cached_tokens", 0
                                ) or 0

                        if not chunk.choices:
                            continue

                        delta = chunk.choices[0].delta
                        content = (delta.content or "").replace("</think>", "").replace("<think>", "")
                        result.text += content

                        if on_token and content:
                            on_token(content)

                        rc = getattr(delta, "reasoning_content", None)
                        if rc:
                            result.reasoning_content += rc
                            if on_token:
                                on_token(rc, is_reasoning=True)

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
                    with self._lock:
                        self._active_streams.pop(thread_id, None)
                    self._close_stream(response)

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
        finally:
            if deadline is not None:
                client.close()
