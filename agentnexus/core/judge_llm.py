"""Independent Judge LLM — separate model instance for evaluation.

Resolution order (Settings.get_judge_profile):
1. ``judge_model`` selector ("provider/model") — picks a configured provider model.
2. Legacy ``judge_*`` flat fields when explicitly configured.
3. Follow the active task model (default).

Article rule: "Use a different model family for the judge than for the generator."
Prevents score inflation from self-evaluation bias (15-30% overestimate).
"""

from __future__ import annotations

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM

_judge_llm: AgentLLM | None = None


def get_judge_llm() -> AgentLLM:
    """Return the singleton judge LLM instance."""
    global _judge_llm
    if _judge_llm is None:
        model_id, base_url, api_key, timeout = get_settings().get_judge_profile()
        _judge_llm = AgentLLM(
            model=model_id,
            apiKey=api_key.get_secret_value(),
            baseUrl=base_url,
            timeout=timeout,
        )
    return _judge_llm


def reset_judge_llm() -> None:
    """Drop the cached instance so the next call re-resolves settings."""
    global _judge_llm
    _judge_llm = None
