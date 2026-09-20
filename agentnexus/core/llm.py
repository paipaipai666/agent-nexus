import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from agentnexus.core.providers.base import BaseLLMProvider, StreamResult

from rich.console import Console
from rich.live import Live
from rich.text import Text

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.core.capabilities import (
    ModelCapabilities,
    SessionCapabilityTracker,
    detect_capabilities,
)
from agentnexus.core.config import get_settings
from agentnexus.core.providers.router import select_provider
from agentnexus.observability.tracer import trace_manager

logger = logging.getLogger(__name__)
console = Console()

LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 2.0

# Provider health tracking for circuit breaker
_provider_health: dict[str, tuple[int, float]] = {}  # key -> (failure_count, last_failure_time)
_provider_health_lock = threading.Lock()
_PROVIDER_FAILURE_THRESHOLD = 3
_PROVIDER_COOLDOWN_SECONDS = 60


def _is_transient_error(exc: Exception) -> bool:
    """网络层瞬态错误判定——与 _call 的重试分类保持一致。"""
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if any(k in name or k in msg for k in (
        "connection", "ssl", "timeout", "server",
        "unexpected_eof", "incomplete", "peer closed",
    )):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        for attr in ("response", "status", "http_status"):
            inner = getattr(exc, attr, None)
            if inner is not None:
                status_code = getattr(inner, "status_code", None)
                if status_code is not None:
                    break
    return status_code in (429, 503)

# Live capability probe — one tiny call per (model, base_url), cached
# process-wide so concurrent agents don't each pay the round trip.
_probe_lock = threading.Lock()
_probe_cache: dict[tuple[str, str], dict[str, bool]] = {}

_PROBE_NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "Do nothing. Call this tool when the user asks you to.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

_default_llm: "AgentLLM | None" = None


def get_default_llm() -> "AgentLLM":
    global _default_llm
    if _default_llm is None:
        _default_llm = AgentLLM()
    return _default_llm


class AgentLLM:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
        *,
        # Backward-compatible camelCase aliases (deprecated)
        apiKey: str | None = None,
        baseUrl: str | None = None,
    ):
        from agentnexus.core.capabilities import _normalize_model_id
        settings = get_settings()
        profile = settings.get_active_llm_profile()
        self.base_url = base_url or baseUrl or profile[1]
        raw_model = (model or profile[0]).strip()
        self.model = _normalize_model_id(raw_model, self.base_url) if "/" not in raw_model else raw_model
        self.api_key = api_key or apiKey or profile[2].get_secret_value()
        self.timeout = timeout or profile[3]
        # Per-call results live in thread-local storage: concurrent runs
        # (multi-session) share this client, and plain instance attributes
        # would clobber each other between interleaved streams.
        self._call_state = threading.local()
        self.total_usage: dict = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
        self._capabilities: ModelCapabilities | None = None
        self._session_tracker: SessionCapabilityTracker | None = None
        # Cooperative cancellation: a watcher thread closes the in-flight
        # provider stream the moment the checker fires (产品决策: 取消=
        # 立刻停止). Set per run by the agent; None on legacy paths.
        self._cancel_checker: Callable[[], bool] | None = None
        self._active_provider: Any = None

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        """Install a cooperative cancellation callback for the current run."""
        self._cancel_checker = checker

    def _cancel_watcher(self, stop: threading.Event) -> None:
        """Poll the cancel checker; on fire, close the active provider stream.

        Without this, a stalled network read hides the cancel signal until
        the next token arrives (token-boundary polling only).
        """
        checker = self._cancel_checker
        while not stop.wait(0.05):
            if checker is not None and checker():
                provider = self._active_provider
                if provider is not None:
                    try:
                        abort = getattr(provider, "abort_active_stream", None)
                        if abort is not None:
                            abort()
                    except Exception:
                        pass
                return

    @staticmethod
    def _litellm_can_route(model: str) -> bool:
        """litellm 是否能路由该模型（不认识的模型走了必然 BadRequest）。"""
        try:
            import litellm
            litellm.get_llm_provider(model)
            return True
        except Exception:
            return False

    def _cs(self):
        """Per-thread scratch space for the in-flight call's side-channel results."""
        s = self._call_state
        if not hasattr(s, "initialized"):
            s.initialized = True
            s.tool_calls = []
            s.tool_call_mode = False
            s.non_transient = False
            s.error = ""
            s.truncated = False
            s.usage = {}
            s.reasoning_content = ""
            s.reasoning_buf = ""
        return s

    @property
    def last_tool_calls(self) -> list[dict]:
        return self._cs().tool_calls

    @last_tool_calls.setter
    def last_tool_calls(self, value: list[dict]) -> None:
        self._cs().tool_calls = value

    @property
    def last_error(self) -> str:
        return self._cs().error

    @last_error.setter
    def last_error(self, value: str) -> None:
        self._cs().error = value

    @property
    def last_truncated(self) -> bool:
        return self._cs().truncated

    @last_truncated.setter
    def last_truncated(self, value: bool) -> None:
        self._cs().truncated = value

    @property
    def last_usage(self) -> dict:
        return self._cs().usage

    @last_usage.setter
    def last_usage(self, value: dict) -> None:
        self._cs().usage = value

    @property
    def last_reasoning_content(self) -> str:
        return self._cs().reasoning_content

    @last_reasoning_content.setter
    def last_reasoning_content(self, value: str) -> None:
        self._cs().reasoning_content = value

    def configure(self, *, model: str, base_url: str, api_key: str, timeout: int | None = None) -> None:
        """Hot-switch the underlying model/provider (shared instance — every
        agent holding this client picks the change up on its next call)."""
        from agentnexus.core.capabilities import _normalize_model_id

        self.base_url = base_url
        raw = model.strip()
        self.model = _normalize_model_id(raw, base_url) if "/" not in raw else raw
        self.api_key = api_key
        if timeout:
            self.timeout = timeout
        # Cached capability state is model-specific — force re-detection.
        self._capabilities = None
        self._session_tracker = None
        self.last_error = ""

    @property
    def capabilities(self) -> ModelCapabilities:
        if self._capabilities is None:
            caps = detect_capabilities(self.model, self.base_url)
            if caps.from_default_fallback:
                caps = self._merge_probed_capabilities(caps)
            self._capabilities = caps
        return self._capabilities

    def _merge_probed_capabilities(self, caps: ModelCapabilities) -> ModelCapabilities:
        """Fill in capabilities for models the static registry doesn't know.

        Probes the endpoint once per (model, base_url) with minimal real
        calls — a tool-calling call, then (only when tools are unavailable)
        a JSON-mode call. Explicit config overrides (model_tool_calling /
        model_json_mode) always win over probe results. Probe failures never
        raise and never disable a capability the registry granted.
        """
        settings = get_settings()
        if settings.model_tool_calling is not None and settings.model_json_mode is not None:
            return caps
        key = (self.model.lower(), (self.base_url or "").rstrip("/"))
        with _probe_lock:
            probed = _probe_cache.get(key)
            if probed is None:
                probed = self._probe_capabilities()
                if probed is not None:
                    _probe_cache[key] = probed
        if probed is None:
            return caps
        if settings.model_tool_calling is None:
            caps.supports_tool_calling = probed["tool_calling"]
        if settings.model_json_mode is None:
            caps.supports_json_mode = probed["json_mode"]
        return caps

    def _probe_capabilities(self) -> dict[str, bool] | None:
        """Probe tool-calling / JSON-mode support with minimal real calls.

        Returns {"tool_calling": bool, "json_mode": bool} on a definitive
        outcome, or None when the endpoint couldn't be reached (transient —
        left uncached so a later client retries).
        """
        provider = select_provider(self.model, self.base_url)
        if provider is None:
            return None
        try:
            tool_result = provider.stream_chat(
                messages=[{"role": "user", "content": "Call the noop tool."}],
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=0,
                tools=[_PROBE_NOOP_TOOL],
                max_tokens=64,
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.debug("tool-calling probe failed for %s: %s", self.model, exc)
            return None
        tool_calling = bool(tool_result.tool_calls)
        json_mode = False
        if not tool_calling:
            try:
                json_result = provider.stream_chat(
                    messages=[{"role": "user", "content": 'Reply with exactly: {"ok": true}'}],
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=0,
                    response_format={"type": "json_object"},
                    max_tokens=64,
                    timeout=self.timeout,
                )
                json.loads(json_result.text)
                json_mode = True
            except Exception as exc:
                logger.debug("json-mode probe failed for %s: %s", self.model, exc)
        return {"tool_calling": tool_calling, "json_mode": json_mode}

    @property
    def session_tracker(self) -> SessionCapabilityTracker:
        if self._session_tracker is None:
            self._session_tracker = SessionCapabilityTracker()
        return self._session_tracker

    def reset_session_capabilities(self) -> None:
        self._session_tracker = SessionCapabilityTracker()

    def think(self, messages: List[Dict[str, str]], temperature: float = 0, silent: bool = False,
              tools: list[dict] | None = None,
              response_format: dict | None = None,
              projection_fn: Callable | None = None,
              thinking: bool | None = None,
              max_attempts: int | None = None,
              on_token: Callable[[str], None] | None = None) -> str:
        from agentnexus.core.hooks import HookType, get_hook_manager

        if not self.api_key or not self.base_url:
            return ""

        self.last_tool_calls = []
        self._cs().tool_call_mode = tools is not None and len(tools) > 0
        self._cs().non_transient = False

        # ── before llm hook ────────────────────────────────────
        hook_mgr = get_hook_manager()
        hook_ctx = hook_mgr.fire(HookType.BEFORE_LLM_CALL, {
            "messages": messages,
            "model": self.model,
            "tools": tools,
            "temperature": temperature,
        })
        if hook_ctx.aborted:
            return hook_ctx.payload.get("response_text", "")

        effective_messages = projection_fn(messages) if projection_fn else messages

        # Cancel watcher: while a run is active, poll the checker and close
        # the in-flight provider stream on fire. Without it a stalled read
        # hides cancel until the next token (old token-boundary behavior).
        stop_watcher = threading.Event()
        watcher = None
        if self._cancel_checker is not None:
            watcher = threading.Thread(
                target=self._cancel_watcher, args=(stop_watcher,),
                name="llm-cancel-watcher", daemon=True,
            )
            watcher.start()

        attempts = max(1, min(max_attempts or LLM_MAX_RETRIES, LLM_MAX_RETRIES))
        result = ""
        try:
            for attempt in range(attempts):
                result = self._call(
                    effective_messages, temperature, silent, attempt,
                    tools, response_format, thinking, on_token=on_token,
                ) or ""
                if result:
                    break
                if self._cs().non_transient:
                    break
                if attempt < attempts - 1:
                    import random
                    delay = LLM_RETRY_BASE_DELAY * (2 ** attempt) * (0.5 + random.random())
                    time.sleep(delay)
        finally:
            stop_watcher.set()

        # ── after llm hook ─────────────────────────────────────
        hook_mgr.fire(HookType.AFTER_LLM_CALL, {
            "messages": messages,
            "model": self.model,
            "response_text": result,
            "tool_calls": self.last_tool_calls,
        })
        return result

    def _call(self, messages, temperature, silent, attempt, tools=None, response_format=None, thinking=None,
              on_token=None) -> str:
        model = self.model

        ctx = trace_manager.active
        span = None
        if ctx:
            # 提取上下文来源引用（context_refs）
            context_refs = []
            for msg in messages:
                role = msg.get("role", "")
                if role == "system":
                    context_refs.append("system_prompt")
                elif role == "tool":
                    context_refs.append(f"tool_result:{msg.get('name', 'unknown')}")
                elif role == "assistant" and msg.get("tool_calls"):
                    context_refs.append("assistant_tool_calls")
            span = ctx.start_span("llm", {
                "model": model,
                "messages_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "input_preview": _preview(messages[-1]["content"]) if messages else "",
                "context_refs": context_refs,
            })

        self._cs().reasoning_buf = ""
        self.last_reasoning_content = ""

        try:
            # ── Step 1: Try direct provider ─────────────────────
            provider = select_provider(model, self.base_url)
            if provider is not None:
                provider_key = f"{type(provider).__name__}/{self.model}"
                with _provider_health_lock:
                    health = _provider_health.get(provider_key)
                    if health and health[0] >= _PROVIDER_FAILURE_THRESHOLD:
                        elapsed = time.time() - health[1]
                        if elapsed < _PROVIDER_COOLDOWN_SECONDS:
                            provider = None  # Skip to fallback
                        else:
                            _provider_health.pop(provider_key, None)
            provider_err: Exception | None = None
            if provider is not None:
                try:
                    result = self._call_via_provider(
                        provider, messages, temperature, tools,
                        response_format, thinking, on_token,
                    )
                    # Sync state from provider result
                    self.last_tool_calls = result.tool_calls
                    self.last_truncated = result.truncated
                    self.last_reasoning_content = result.reasoning_content
                    self.last_usage = result.usage or self._estimate_usage(model, messages, result.text)
                    self.total_usage["input_tokens"] += self.last_usage.get("input_tokens", 0)
                    self.total_usage["output_tokens"] += self.last_usage.get("output_tokens", 0)
                    self.total_usage["cache_hit_tokens"] += self.last_usage.get("cache_hit_tokens", 0)

                    if ctx and span:
                        self._end_trace_span(ctx, span, model, result.text)

                    if not silent and result.text:
                        text = Text(result.text)
                        console.print(text)

                    # Reset provider health on success
                    with _provider_health_lock:
                        _provider_health.pop(provider_key, None)

                    return result.text
                except Exception as exc:
                    provider_err = exc
                    # Track provider failure for circuit breaker
                    with _provider_health_lock:
                        fail_count, _ = _provider_health.get(provider_key, (0, 0))
                        _provider_health[provider_key] = (fail_count + 1, time.time())
                    # 瞬态错误（对端断流/超时/429）必须让外层重试直接走
                    # provider——litellm 往往不认识该模型，只会把瞬态失败
                    # 变成 "Provider NOT provided" 的确定性致命错误。
                    if _is_transient_error(exc):
                        logger.warning("Direct provider failed (transient, will retry): %s", exc)
                        raise
                    logger.warning("Direct provider failed, falling back to LiteLLM: %s", exc)

            # ── Step 2: LiteLLM fallback ────────────────────────
            # litellm 路由不了的模型（OpenRouter 的 *:free / stealth / 第三方
            # 命名）走 litellm 必然失败并掩盖原始 provider 错误——保留原错误
            # 交给外层重试分类。
            if provider_err is not None and not self._litellm_can_route(model):
                raise provider_err
            result = self._call_via_litellm(
                messages, temperature, silent, tools,
                response_format, thinking, on_token, model,
            )

            if ctx and span:
                self._end_trace_span(ctx, span, model, result)

            return result

        except Exception as e:
            # 取消信号必须直接传播，不能被重试逻辑吞掉
            if isinstance(e, AgentCancelled):
                raise
            # 流被 cancel-watcher 关闭后底层会抛 httpx/连接错误——统一
            # 还原为 AgentCancelled，避免被重试循环当作瞬态错误重放。
            if self._cancel_checker is not None and self._cancel_checker():
                raise AgentCancelled("cancelled") from e

            error_msg = str(e)
            self.last_error = error_msg

            # ── Capability degradation on "unsupported" errors ──
            error_lower = error_msg.lower()
            if any(kw in error_lower for kw in ("tool", "function_call", "function calling")) and \
               any(kw in error_lower for kw in ("not support", "unsupported", "invalid", "unknown parameter")):
                self.session_tracker.mark_failed("tool_calling")
            if "response_format" in error_lower and \
               any(kw in error_lower for kw in ("not support", "unsupported", "invalid", "unknown parameter")):
                self.session_tracker.mark_failed("json_mode")
            if "reasoning_effort" in error_lower or "thinking" in error_lower:
                self.session_tracker.mark_failed("thinking")

            is_transient = _is_transient_error(e)

            retry_tag = f"[retry {attempt + 1}/{LLM_MAX_RETRIES}]" if attempt < LLM_MAX_RETRIES - 1 else "[exhausted]"
            logger.error("LLM 错误%s: %s", retry_tag, error_msg)

            if ctx and span:
                ctx.end_span(span, metadata={
                    "model": model, "status": "error", "error": error_msg,
                    "retry_attempt": attempt + 1, "transient": is_transient,
                })

            if not is_transient:
                self._cs().non_transient = True
                return ""

            # transient error — outer retry loop continues

        # All retries exhausted with transient errors
        return ""

    def _call_via_provider(
        self,
        provider: "BaseLLMProvider",
        messages: list[dict],
        temperature: float,
        tools: list[dict] | None,
        response_format: dict | None,
        thinking: bool | None,
        on_token: Callable[[str], None] | None = None,
    ) -> "StreamResult":
        """Call LLM via a direct provider (OpenAI SDK)."""
        caps = self.capabilities
        tracker = self.session_tracker

        provider_tools = None
        parallel = None
        if tools and tracker.is_available("tool_calling", caps.supports_tool_calling):
            provider_tools = tools
            if caps.supports_parallel_tool_calls:
                parallel = True

        reasoning_effort = None
        should_think = thinking if thinking is not None else caps.supports_thinking
        if should_think and tracker.is_available("thinking", caps.supports_thinking):
            if caps.thinking_effort != "none":
                reasoning_effort = caps.thinking_effort

        provider_response_format = None
        if response_format:
            if tracker.is_available("json_mode", caps.supports_json_mode):
                provider_response_format = response_format
            elif isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                if tracker.is_available("json_schema", caps.supports_json_schema):
                    provider_response_format = response_format

        stream_opts = None
        if "openai.com" in (self.base_url or ""):
            stream_opts = {"include_usage": True}

        self._active_provider = provider
        try:
            return provider.stream_chat(
                messages=messages,
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=temperature,
                tools=provider_tools,
                response_format=provider_response_format,
                max_tokens=caps.max_output_tokens,
                timeout=self.timeout,
                parallel_tool_calls=parallel,
                stream_options=stream_opts,
                reasoning_effort=reasoning_effort,
                on_token=on_token,
            )
        finally:
            self._active_provider = None

    def _call_via_litellm(self, messages, temperature, silent, tools, response_format, thinking,
                          on_token, model) -> str:
        """Call LLM via LiteLLM (fallback path)."""
        import litellm

        caps = self.capabilities
        tracker = self.session_tracker

        completion_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "api_key": self.api_key,
            "api_base": self.base_url,
            "timeout": self.timeout,
            "max_tokens": caps.max_output_tokens,
        }

        # ── Tool calling ──
        if tools and tracker.is_available("tool_calling", caps.supports_tool_calling):
            completion_kwargs["tools"] = tools
            completion_kwargs["tool_choice"] = "auto"
            if caps.supports_parallel_tool_calls:
                completion_kwargs["parallel_tool_calls"] = True
        else:
            completion_kwargs["drop_params"] = True

        # ── JSON mode ──
        if response_format:
            if tracker.is_available("json_mode", caps.supports_json_mode):
                completion_kwargs["response_format"] = response_format
            elif isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                if tracker.is_available("json_schema", caps.supports_json_schema):
                    completion_kwargs["response_format"] = response_format

        # ── Thinking / reasoning ──
        should_think = thinking if thinking is not None else caps.supports_thinking
        if should_think and tracker.is_available("thinking", caps.supports_thinking):
            if caps.thinking_effort != "none":
                completion_kwargs["reasoning_effort"] = caps.thinking_effort

        if "openai.com" in (self.base_url or ""):
            completion_kwargs["stream_options"] = {"include_usage": True}
        response = litellm.completion(**completion_kwargs)

        collected = []
        usage = {}
        finish_reason = ""
        tool_call_bufs: dict[int, dict] = {}
        text = Text()
        live = None
        if not silent:
            live = Live(text, console=console, refresh_per_second=15, transient=True)
            live.__enter__()
        try:
            for chunk in response:
                delta = chunk.choices[0].delta
                content = delta.content or ""
                collected.append(content)
                if on_token and content:
                    on_token(content)
                text.append(content)
                if live:
                    live.update(text)

                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    self._cs().reasoning_buf += rc
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

                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens or 0,
                        "output_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }
                    # DeepSeek prompt cache hit/miss tokens
                    if hasattr(chunk.usage, "prompt_cache_hit_tokens"):
                        usage["cache_hit_tokens"] = chunk.usage.prompt_cache_hit_tokens or 0
                        usage["cache_miss_tokens"] = chunk.usage.prompt_cache_miss_tokens or 0
                    # OpenAI cached_tokens (prompt_tokens_details.cached_tokens)
                    elif hasattr(chunk.usage, "prompt_tokens_details") and chunk.usage.prompt_tokens_details:
                        usage["cache_hit_tokens"] = getattr(
                            chunk.usage.prompt_tokens_details, "cached_tokens", 0
                        ) or 0
                fr = getattr(chunk.choices[0], "finish_reason", "")
                if fr:
                    finish_reason = fr
        finally:
            if live:
                live.__exit__(None, None, None)

        result = "".join(collected)
        self.last_truncated = finish_reason in ("length", "max_tokens")
        self.last_reasoning_content = self._cs().reasoning_buf

        self.last_tool_calls = []
        for buf in tool_call_bufs.values():
            if buf["function"]["name"]:
                try:
                    args = json.loads(buf["function"]["arguments"]) if buf["function"]["arguments"] else {}
                except (json.JSONDecodeError, ValueError):
                    args = {}
                self.last_tool_calls.append({
                    "id": buf["id"],
                    "name": buf["function"]["name"],
                    "arguments": args,
                })

        if not usage:
            usage = self._estimate_usage(model, messages, result)

        self.last_usage = usage
        self.total_usage["input_tokens"] += usage.get("input_tokens", 0)
        self.total_usage["output_tokens"] += usage.get("output_tokens", 0)
        self.total_usage["cache_hit_tokens"] += usage.get("cache_hit_tokens", 0)

        return result

    def _estimate_usage(self, model, messages, result) -> dict:
        """Estimate token usage via tiktoken when not reported by the API."""
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            input_tokens = sum(len(enc.encode(json.dumps(m, ensure_ascii=False))) for m in messages)
            output_tokens = len(enc.encode(result or ""))
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
        except Exception:
            logger.debug("Token usage estimation failed", exc_info=True)
            return {}

    def _end_trace_span(self, ctx, span, model: str, result_text: str) -> None:
        """End a trace span with standard metadata (cache hit rate, tool calls, etc.)."""
        meta = {"model": model, "status": "ok", "truncated": self.last_truncated, **self.last_usage}
        if self.last_tool_calls:
            meta["tool_calls"] = [tc["name"] for tc in self.last_tool_calls]
        cache_hit = self.last_usage.get("cache_hit_tokens", 0)
        cache_miss = self.last_usage.get("cache_miss_tokens", 0)
        if cache_hit or cache_miss:
            meta["cache_hit_tokens"] = cache_hit
            meta["cache_miss_tokens"] = cache_miss
            total_cache = cache_hit + cache_miss
            meta["cache_hit_rate"] = cache_hit / total_cache if total_cache > 0 else 0.0
        ctx.end_span(
            span,
            output_data={"output_preview": _preview(result_text), "output_length": len(result_text)},
            metadata=meta,
        )


def _preview(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
