import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from agentnexus.core.providers.base import BaseLLMProvider, StreamResult

from rich.console import Console
from rich.text import Text

from agentnexus.core.capabilities import (
    ModelCapabilities,
    ModelCapabilitySnapshot,
    SessionCapabilityTracker,
    detect_capabilities,
)
from agentnexus.core.capability_discovery import CapabilityDiscovery, probe_capabilities
from agentnexus.core.config import get_settings
from agentnexus.core.exceptions import AgentCancelled
from agentnexus.core.providers.router import select_provider
from agentnexus.core.telemetry import trace_manager

logger = logging.getLogger(__name__)
console = Console()

LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 2.0

_provider_health: dict[str, tuple[int, float]] = {}
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

_default_llm: "AgentLLM | None" = None


@dataclass
class _ActiveCall:
    thread_id: int
    checker: Callable[[], bool] | None
    provider: "BaseLLMProvider | None" = None


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
        apiKey: str | None = None,
        baseUrl: str | None = None,
    ):
        settings = get_settings()
        profile = settings.get_active_llm_profile()
        self.base_url = base_url or baseUrl or profile[1]
        self.model = (model or profile[0]).strip()
        self.api_key = api_key or apiKey or profile[2].get_secret_value()
        self.timeout = timeout or profile[3]
        self._call_state = threading.local()
        self._capability_lock = threading.RLock()
        self.total_usage: dict = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
        self._capabilities: ModelCapabilities | None = None
        self._discovery: CapabilityDiscovery | None = None
        self._session_tracker: SessionCapabilityTracker | None = None
        self._default_cancel_checker: Callable[[], bool] | None = None

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        """Install a callback for this thread's runs.

        A callback installed on the main thread is also the default for
        workers that have not installed their own callback.
        """
        self._cs().cancel_checker = checker
        if threading.current_thread() is threading.main_thread():
            self._default_cancel_checker = checker

    def _cancel_watcher(self, stop: threading.Event, call: _ActiveCall) -> None:
        """Poll the cancel checker; on fire, close the active provider stream.

        Without this, a stalled network read hides the cancel signal until
        the next token arrives (token-boundary polling only).
        """
        checker = call.checker
        while not stop.wait(0.05):
            if checker is not None and checker():
                provider = call.provider
                if provider is not None:
                    try:
                        provider.abort_active_stream(thread_id=call.thread_id)
                    except Exception:
                        pass

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
            s.active_call = None
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
        with self._capability_lock:
            self.base_url = base_url
            self.model = model.strip()
            self.api_key = api_key
            if timeout:
                self.timeout = timeout
            self._capabilities = None
            self._discovery = None
            self._session_tracker = None
            self.last_error = ""

    @property
    def capabilities(self) -> ModelCapabilities:
        """Read configured and cached capabilities without remote I/O."""
        return self.capability_snapshot().capabilities

    def capability_snapshot(self) -> ModelCapabilitySnapshot:
        """Read capability flags and discovery state from one local snapshot."""
        with self._capability_lock:
            if self._capabilities is None:
                self._capabilities = detect_capabilities(self.model, self.base_url)
            caps = self._capabilities
            if not caps.from_default_fallback:
                return ModelCapabilitySnapshot(caps, "config", "ready")
            snapshot = self._get_discovery(caps).snapshot()
            if snapshot.result is not None:
                caps = replace(caps, supports_tool_calling=snapshot.result["tool_calling"],
                               supports_json_mode=snapshot.result["json_mode"],
                               supports_parallel_tool_calls=caps.supports_parallel_tool_calls if caps.parallel_explicit else snapshot.result["parallel_tool_calls"],
                               from_default_fallback=False)
                return ModelCapabilitySnapshot(caps, "probe", "ready")
            return ModelCapabilitySnapshot(caps, "unknown", snapshot.status)

    def _get_discovery(self, caps: ModelCapabilities) -> CapabilityDiscovery:
        if self._discovery is None:
            self._discovery = CapabilityDiscovery(self.model, self.base_url, self.api_key, asdict(caps))
        return self._discovery

    def _probe_loader(self):
        model, base_url, api_key, timeout = self.model, self.base_url, self.api_key, self.timeout
        return lambda: probe_capabilities(select_provider(model, base_url),
                                          model=model, api_key=api_key, base_url=base_url, timeout=timeout)

    def ensure_capabilities(self) -> ModelCapabilities:
        """Confirm an unknown protocol before executing a model task."""
        with self._capability_lock:
            caps = self.capabilities
            if not caps.from_default_fallback or not self.api_key or not self.base_url:
                return caps
            discovery, loader = self._get_discovery(caps), self._probe_loader()
        discovery.ensure(loader)
        return self.capabilities

    @property
    def capability_discovery_status(self) -> str:
        """Read discovery progress without waiting for its worker."""
        return self.capability_snapshot().status

    def start_capability_discovery(self) -> None:
        """Warm unknown protocol capabilities without blocking the caller."""
        with self._capability_lock:
            caps = self.capabilities
            if caps.from_default_fallback and self.api_key and self.base_url:
                self._get_discovery(caps).start(self._probe_loader())

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
              on_token: Callable[[str], None] | None = None,
              effort: str | None = None) -> str:
        from agentnexus.core.hooks import HookType, fire_hook

        if not self.api_key or not self.base_url:
            return ""

        self.last_tool_calls = []
        self.last_error = ""
        self._cs().tool_call_mode = tools is not None and len(tools) > 0
        self._cs().non_transient = False

        hook_ctx = fire_hook(HookType.BEFORE_LLM_CALL, {
            "messages": messages,
            "model": self.model,
            "tools": tools,
            "temperature": temperature,
        })
        if hook_ctx.aborted:
            return hook_ctx.payload.get("response_text", "")

        effective_messages = projection_fn(messages) if projection_fn else messages

        stop_watcher = threading.Event()
        call = _ActiveCall(threading.get_ident(), getattr(self._cs(), "cancel_checker", self._default_cancel_checker))
        self._cs().active_call = call
        watcher = None
        if call.checker is not None:
            watcher = threading.Thread(
                target=self._cancel_watcher, args=(stop_watcher, call),
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
                    effort=effort,
                ) or ""
                if result or self.last_tool_calls:
                    break
                if self._cs().non_transient:
                    break
                if attempt < attempts - 1:
                    import random
                    delay = LLM_RETRY_BASE_DELAY * (2 ** attempt) * (0.5 + random.random())
                    time.sleep(delay)
        finally:
            stop_watcher.set()
            self._cs().active_call = None

        fire_hook(HookType.AFTER_LLM_CALL, {
            "messages": messages,
            "model": self.model,
            "response_text": result,
            "tool_calls": self.last_tool_calls,
        })
        return result

    def _call(self, messages, temperature, silent, attempt, tools=None, response_format=None, thinking=None,
              on_token=None, effort=None) -> str:
        model = self.model

        ctx = trace_manager.active
        span = None
        if ctx:
            context_refs = []
            for msg in messages:
                role = msg.get("role", "")
                if role == "system":
                    context_refs.append("system_prompt")
                elif role == "tool":
                    context_refs.append(f"tool_result:{msg.get('name', 'unknown')}")
                elif role == "assistant" and msg.get("tool_calls"):
                    context_refs.append("assistant_tool_calls")
            span = trace_manager.start_span("llm", {
                "model": model,
                "messages_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "input_preview": _preview(messages[-1]["content"]) if messages else "",
                "context_refs": context_refs,
            })

        self._cs().reasoning_buf = ""
        self.last_reasoning_content = ""

        try:
            provider = select_provider(model, self.base_url)
            provider_key = f"{type(provider).__name__}/{self.model}"
            with _provider_health_lock:
                health = _provider_health.get(provider_key)
                if health and health[0] >= _PROVIDER_FAILURE_THRESHOLD:
                    elapsed = time.time() - health[1]
                    if elapsed < _PROVIDER_COOLDOWN_SECONDS:
                        raise RuntimeError(
                            f"provider {provider_key} in cooldown after repeated failures"
                        )
                    _provider_health.pop(provider_key, None)
            try:
                result = self._call_via_provider(
                    provider, messages, temperature, tools,
                    response_format, thinking, on_token, effort=effort,
                )
                self.last_tool_calls = result.tool_calls
                self.last_error = ""
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

                with _provider_health_lock:
                    _provider_health.pop(provider_key, None)

                return result.text
            except Exception as exc:
                with _provider_health_lock:
                    fail_count, _ = _provider_health.get(provider_key, (0, 0))
                    _provider_health[provider_key] = (fail_count + 1, time.time())
                if _is_transient_error(exc):
                    logger.warning("Provider failed (transient, will retry): %s", exc)
                    raise
                logger.warning("Provider failed (non-transient): %s", exc)
                raise

        except Exception as e:
            if isinstance(e, AgentCancelled):
                raise
            call = self._cs().active_call
            if call is not None and call.checker is not None and call.checker():
                raise AgentCancelled("cancelled") from e

            error_msg = str(e)
            self.last_error = error_msg

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
                trace_manager.end_span(span, metadata={
                    "model": model, "status": "error", "error": error_msg,
                    "retry_attempt": attempt + 1, "transient": is_transient,
                })

            if not is_transient:
                self._cs().non_transient = True
                return ""

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
        effort: str | None = None,
    ) -> "StreamResult":
        """Call LLM via a direct provider (OpenAI SDK)."""
        caps = self.ensure_capabilities()
        tracker = self.session_tracker

        provider_tools = None
        parallel = None
        if tools and tracker.is_available("tool_calling", caps.supports_tool_calling):
            provider_tools = tools
            if caps.supports_parallel_tool_calls:
                parallel = True

        if effort == "none":
            reasoning_effort = "none"
        else:
            should_think = thinking if thinking is not None else caps.supports_thinking
            can_think = should_think and tracker.is_available("thinking", caps.supports_thinking)
            if effort in ("low", "medium", "high"):
                reasoning_effort = effort if can_think else "none"
            elif can_think:
                reasoning_effort = caps.thinking_effort if caps.thinking_effort != "none" else "medium"
            else:
                reasoning_effort = "none"

        provider_response_format = None
        if response_format:
            if tracker.is_available("json_mode", caps.supports_json_mode):
                provider_response_format = response_format
            elif isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                if tracker.is_available("json_schema", caps.supports_json_schema):
                    provider_response_format = response_format

        stream_opts = {"include_usage": True}

        call = self._cs().active_call
        if call is not None:
            call.provider = provider
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
            if call is not None:
                call.provider = None

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
        trace_manager.end_span(
            span,
            output_data={"output_preview": _preview(result_text), "output_length": len(result_text)},
            metadata=meta,
        )


def _preview(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
