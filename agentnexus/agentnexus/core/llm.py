import time
from typing import Dict, List

from rich.console import Console
from rich.live import Live
from rich.markup import escape as _e
from rich.text import Text

from agentnexus.core.config import get_settings
from agentnexus.observability.tracer import trace_manager

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
        self.model = model or settings.llm_model_id
        self.api_key = apiKey or settings.llm_api_key.get_secret_value()
        self.base_url = baseUrl or settings.llm_base_url
        self.timeout = timeout or settings.llm_timeout
        self.last_error: str = ""
        self.last_truncated: bool = False

    def think(self, messages: List[Dict[str, str]], temperature: float = 0, silent: bool = False) -> str:
        if not self.api_key or not self.base_url:
            return ""

        for attempt in range(LLM_MAX_RETRIES):
            result = self._call(messages, temperature, silent, attempt)
            if result:
                return result
            if attempt < LLM_MAX_RETRIES - 1:
                delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
        return ""

    def _call(self, messages, temperature, silent, attempt) -> str:
        import litellm
        model = self.model
        if "/" not in model:
            if "deepseek.com" in (self.base_url or ""):
                model = f"deepseek/{model}"
            elif "openai.com" in (self.base_url or ""):
                model = f"openai/{model}"
            else:
                model = f"openai/{model}"

        ctx = trace_manager.active
        span = None
        if ctx:
            span = ctx.start_span("llm", {
                "model": model,
                "messages_count": len(messages),
                "input_preview": _preview(messages[-1]["content"]) if messages else "",
            })

        try:
            completion_kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
                "api_key": self.api_key,
                "api_base": self.base_url,
                "timeout": self.timeout,
            }
            if "openai.com" in (self.base_url or ""):
                completion_kwargs["stream_options"] = {"include_usage": True}
            response = litellm.completion(**completion_kwargs)

            collected = []
            usage = {}
            finish_reason = ""
            text = Text()
            live = None
            if not silent:
                live = Live(text, console=console, refresh_per_second=15, transient=True)
                live.__enter__()
            try:
                for chunk in response:
                    content = chunk.choices[0].delta.content or ""
                    collected.append(content)
                    text.append(content)
                    if live:
                        live.update(text)
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

            if not usage:
                try:
                    import litellm as _litellm
                    usage = {
                        "input_tokens": _litellm.token_counter(model=model, messages=messages),
                        "output_tokens": _litellm.token_counter(model=model, text=result),
                    }
                    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                except Exception:
                    pass

            if ctx and span:
                ctx.end_span(span,
                    output_data={"output_preview": _preview(result), "output_length": len(result)},
                    metadata={"model": model, "status": "ok", "truncated": self.last_truncated, **usage},
                )

            return result

        except Exception as e:
            error_msg = str(e)[:300]
            self.last_error = error_msg

            is_transient = any(
                k in str(type(e).__name__).lower() or k in error_msg.lower()
                for k in ("connection", "ssl", "timeout", "server",
                          "unexpected_eof", "incomplete", "peer closed")
            )
            # Check HTTP status code on wrapped exceptions (e.g. httpx.HTTPStatusError)
            status_code = getattr(e, "status_code", None)
            if status_code is None:
                # LiteLLM may wrap status code deeper — try common patterns
                for attr in ("response", "status", "http_status"):
                    inner = getattr(e, attr, None)
                    if inner is not None:
                        status_code = getattr(inner, "status_code", None)
                        if status_code is not None:
                            break
            if status_code in (429, 503):
                is_transient = True

            retry_tag = f"[retry {attempt + 1}/{LLM_MAX_RETRIES}]" if attempt < LLM_MAX_RETRIES - 1 else "[exhausted]"
            console.print(f"[red]LLM 错误{retry_tag}: {_e(error_msg[:120])}[/red]")

            if ctx and span:
                ctx.end_span(span, metadata={
                    "model": model, "status": "error", "error": error_msg[:200],
                    "retry_attempt": attempt + 1, "transient": is_transient,
                })

            if not is_transient:
                return ""

            # transient error — outer retry loop continues


def _preview(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
