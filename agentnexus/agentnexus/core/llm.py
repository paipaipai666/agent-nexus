from typing import List, Dict

from rich.console import Console
from rich.live import Live
from rich.text import Text

from agentnexus.core.config import get_settings
from agentnexus.observability.tracer import trace_manager

console = Console()


class AgentLLM:
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        settings = get_settings()
        self.model = model or settings.llm_model_id
        self.api_key = apiKey or settings.llm_api_key.get_secret_value()
        self.base_url = baseUrl or settings.llm_base_url
        self.timeout = timeout or settings.llm_timeout

    def think(self, messages: List[Dict[str, str]], temperature: float = 0, silent: bool = False) -> str:
        if not self.api_key or not self.base_url:
            return ""

        try:
            import litellm
            model = self.model
            if "/" not in model:
                if "deepseek.com" in (self.base_url or ""):
                    model = f"deepseek/{model}"
                elif "openai.com" in (self.base_url or ""):
                    model = f"openai/{model}"
                else:
                    model = f"openai/{model}"

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

            ctx = trace_manager.active
            if ctx:
                span = ctx.start_span("llm", {
                    "model": self.model,
                    "messages_count": len(messages),
                    "input_preview": _preview(messages[-1]["content"]) if messages else "",
                })
                ctx.end_span(span,
                    output_data={"output_preview": _preview(result), "output_length": len(result)},
                    metadata={"model": self.model, "status": "ok", **usage},
                )

            return result

        except Exception as e:
            console.print(f"[red]LLM 错误: {e}[/red]")
            ctx = trace_manager.active
            if ctx:
                span = ctx.start_span("llm", {"model": self.model, "error": str(e)})
                ctx.end_span(span, metadata={"model": self.model, "status": "error", "error": str(e)[:200]})
            return ""


def _preview(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
