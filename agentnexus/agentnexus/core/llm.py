from openai import OpenAI
from typing import List, Dict

from rich.console import Console
from rich.live import Live
from rich.text import Text

from agentnexus.core.config import get_settings

console = Console()


class AgentLLM:
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        settings = get_settings()
        self.model = model or settings.llm_model_id
        apiKey = apiKey or settings.llm_api_key.get_secret_value()
        baseUrl = baseUrl or settings.llm_base_url
        timeout = timeout or settings.llm_timeout

        self._client = None
        if apiKey and baseUrl:
            self._client = OpenAI(api_key=apiKey, base_url=baseUrl, timeout=timeout)

    def think(self, messages: List[Dict[str, str]], temperature: float = 0) -> str:
        if not self._client:
            return ""

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )

            collected = []
            text = Text()
            with Live(text, console=console, refresh_per_second=15, transient=False) as live:
                for chunk in response:
                    content = chunk.choices[0].delta.content or ""
                    collected.append(content)
                    text.append(content)
                    live.update(text)
            return "".join(collected)

        except Exception as e:
            console.print(f"[red]LLM 错误: {e}[/red]")
            return ""