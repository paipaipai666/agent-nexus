"""Independent Judge LLM — separate model instance for evaluation.

Article rule: "Use a different model family for the judge than for the generator."
Prevents score inflation from self-evaluation bias (15-30% overestimate).

Default: GLM-4.7-Flash (Zhipu) — different family from DeepSeek generator.
"""

from __future__ import annotations

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM

_judge_llm: AgentLLM | None = None


def get_judge_llm() -> AgentLLM:
    """Return the singleton judge LLM instance (different model family from generator)."""
    global _judge_llm
    if _judge_llm is None:
        settings = get_settings()
        judge_key = settings.judge_api_key.get_secret_value()
        gen_key = settings.llm_api_key.get_secret_value()
        effective_key = judge_key or gen_key  # fall back to main key if judge key not set
        _judge_llm = AgentLLM(
            model=settings.judge_model_id,
            apiKey=effective_key,
            baseUrl=settings.judge_base_url,
        )
    return _judge_llm
