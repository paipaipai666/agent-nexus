import time
from typing import Dict, List

from rich.console import Console
from rich.live import Live
from rich.text import Text

from agentnexus.core.config import get_settings
from agentnexus.observability.tracer import trace_manager

console = Console()

LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 2.0


class AgentLLM:
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        settings = get_settings()
        self.model = model or settings.llm_model_id
        self.api_key = apiKey or settings.llm_api_key.get_secret_value()
        self.base_url = baseUrl or settings.llm_base_url
        self.timeout = timeout or settings.llm_timeout
        self.last_error: str = ""

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
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
                api_key=self.api_key,
                api_base=self.base_url,
                timeout=self.timeout,
            )

            collected = []
            usage = {}
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
            finally:
                if live:
                    live.__exit__(None, None, None)

            result = "".join(collected)

            if ctx and span:
                ctx.end_span(span,
                    output_data={"output_preview": _preview(result), "output_length": len(result)},
                    metadata={"model": model, "status": "ok", **usage},
                )

            return result

        except Exception as e:
            error_msg = str(e)[:300]
            self.last_error = error_msg

            is_transient = any(
                k in str(type(e).__name__).lower() or k in error_msg.lower()
                for k in ("connection", "ssl", "timeout", "server",
                          "unexpected_eof", "incomplete", "peer closed", "429", "503")
            )

            retry_tag = f"[retry {attempt + 1}/{LLM_MAX_RETRIES}]" if attempt < LLM_MAX_RETRIES - 1 else "[exhausted]"
            console.print(f"[red]LLM 错误{retry_tag}: {error_msg[:120]}[/red]")

            if ctx and span:
                ctx.end_span(span, metadata={
                    "model": model, "status": "error", "error": error_msg[:200],
                    "retry_attempt": attempt + 1, "transient": is_transient,
                })

            if not is_transient:
                return ""

            return ""  # transient → retry in outer loop, but return empty so the loop continues


def _preview(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
