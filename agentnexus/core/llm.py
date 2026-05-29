import json
import logging
import time
from collections.abc import Callable
from typing import Dict, List

from rich.console import Console
from rich.live import Live
from rich.text import Text

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

_default_llm: "AgentLLM | None" = None


def get_default_llm() -> "AgentLLM":
    global _default_llm
    if _default_llm is None:
        _default_llm = AgentLLM()
    return _default_llm


class AgentLLM:
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        settings = get_settings()
        self.base_url = baseUrl or settings.llm_base_url
        self.model = (model or settings.llm_model_id).strip()
        self.api_key = apiKey or settings.llm_api_key.get_secret_value()
        self.timeout = timeout or settings.llm_timeout
        self.last_error: str = ""
        self.last_truncated: bool = False
        self.last_usage: dict = {}
        self.total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        self.last_tool_calls: list[dict] = []
        self._tool_call_mode: bool = False
        self._capabilities: ModelCapabilities | None = None
        self._session_tracker: SessionCapabilityTracker | None = None
        self.last_reasoning_content: str = ""

    @property
    def capabilities(self) -> ModelCapabilities:
        if self._capabilities is None:
            self._capabilities = detect_capabilities(self.model, self.base_url)
        return self._capabilities

    @property
    def session_tracker(self) -> SessionCapabilityTracker:
        if self._session_tracker is None:
            self._session_tracker = SessionCapabilityTracker()
        return self._session_tracker

    def reset_session_capabilities(self):
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
        self._tool_call_mode = tools is not None and len(tools) > 0

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

        attempts = max(1, min(max_attempts or LLM_MAX_RETRIES, LLM_MAX_RETRIES))
        result = ""
        for attempt in range(attempts):
            result = self._call(
                effective_messages, temperature, silent, attempt,
                tools, response_format, thinking, on_token=on_token,
            ) or ""
            if result:
                break
            if attempt < attempts - 1:
                delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)

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
            span = ctx.start_span("llm", {
                "model": model,
                "messages_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "input_preview": _preview(messages[-1]["content"]) if messages else "",
            })

        self._reasoning_buf = ""
        self.last_reasoning_content = ""

        try:
            # ── Step 1: Try direct provider ─────────────────────
            provider = select_provider(model, self.base_url)
            if provider is not None:
                try:
                    result = self._call_via_provider(
                        provider, messages, temperature, tools,
                        response_format, thinking,
                    )
                    # Sync state from provider result
                    self.last_tool_calls = result.tool_calls
                    self.last_truncated = result.truncated
                    self.last_reasoning_content = result.reasoning_content
                    self.last_usage = result.usage or self._estimate_usage(model, messages, result.text)
                    self.total_usage["input_tokens"] += self.last_usage.get("input_tokens", 0)
                    self.total_usage["output_tokens"] += self.last_usage.get("output_tokens", 0)

                    if ctx and span:
                        meta = {"model": model, "status": "ok", "truncated": self.last_truncated, **self.last_usage}
                        if self.last_tool_calls:
                            meta["tool_calls"] = [tc["name"] for tc in self.last_tool_calls]
                        ctx.end_span(span,
                            output_data={"output_preview": _preview(result.text), "output_length": len(result.text)},
                            metadata=meta,
                        )

                    if not silent and result.text:
                        text = Text(result.text)
                        console.print(text)

                    if on_token and result.text:
                        on_token(result.text)

                    return result.text
                except Exception as provider_err:
                    logger.warning(f"Direct provider failed, falling back to LiteLLM: {provider_err}")

            # ── Step 2: LiteLLM fallback ────────────────────────
            result = self._call_via_litellm(
                messages, temperature, silent, tools,
                response_format, thinking, on_token, model,
            )

            if ctx and span:
                meta = {"model": model, "status": "ok", "truncated": self.last_truncated, **self.last_usage}
                if self.last_tool_calls:
                    meta["tool_calls"] = [tc["name"] for tc in self.last_tool_calls]
                ctx.end_span(span,
                    output_data={"output_preview": _preview(result), "output_length": len(result)},
                    metadata=meta,
                )

            return result

        except Exception as e:
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

            is_transient = any(
                k in str(type(e).__name__).lower() or k in error_msg.lower()
                for k in ("connection", "ssl", "timeout", "server",
                          "unexpected_eof", "incomplete", "peer closed")
            )
            status_code = getattr(e, "status_code", None)
            if status_code is None:
                for attr in ("response", "status", "http_status"):
                    inner = getattr(e, attr, None)
                    if inner is not None:
                        status_code = getattr(inner, "status_code", None)
                        if status_code is not None:
                            break
            if status_code in (429, 503):
                is_transient = True

            retry_tag = f"[retry {attempt + 1}/{LLM_MAX_RETRIES}]" if attempt < LLM_MAX_RETRIES - 1 else "[exhausted]"
            logger.error(f"LLM 错误{retry_tag}: {error_msg}")

            if ctx and span:
                ctx.end_span(span, metadata={
                    "model": model, "status": "error", "error": error_msg,
                    "retry_attempt": attempt + 1, "transient": is_transient,
                })

            if not is_transient:
                return ""

            # transient error — outer retry loop continues

    def _call_via_provider(self, provider, messages, temperature, tools, response_format, thinking):
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

        stream_opts = None
        if "openai.com" in (self.base_url or ""):
            stream_opts = {"include_usage": True}

        return provider.stream_chat(
            messages=messages,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=temperature,
            tools=provider_tools,
            max_tokens=caps.max_output_tokens,
            timeout=self.timeout,
            parallel_tool_calls=parallel,
            stream_options=stream_opts,
            reasoning_effort=reasoning_effort,
        )

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
                    self._reasoning_buf += rc

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
                fr = getattr(chunk.choices[0], "finish_reason", "")
                if fr:
                    finish_reason = fr
        finally:
            if live:
                live.__exit__(None, None, None)

        result = "".join(collected)
        self.last_truncated = finish_reason in ("length", "max_tokens")
        self.last_reasoning_content = getattr(self, "_reasoning_buf", "")

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
            return {}


def _preview(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
