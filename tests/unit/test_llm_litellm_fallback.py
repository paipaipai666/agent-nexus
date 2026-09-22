"""Regression: litellm fallback must not receive the project's provider-prefixed
model ids (zhipu/..., agnes/...) — litellm's prefix world is incompatible and
dies with 'LLM Provider NOT provided'. Fallback rewrites to openai/<bare-name>
+ api_base, which is correct for every OpenAI-compatible endpoint we use.
"""

from types import SimpleNamespace

import pytest

from agentnexus.core import llm as llm_module
from agentnexus.core.capabilities import ModelCapabilities
from agentnexus.core.llm import AgentLLM


@pytest.mark.parametrize(
    "model, expected",
    [
        ("zhipu/glm-4.7-flash", "openai/glm-4.7-flash"),
        ("agnes/agnes-3.0-flash", "openai/agnes-3.0-flash"),
        ("deepseek-ai/DeepSeek-V4-Flash", "openai/DeepSeek-V4-Flash"),
        ("deepseek/deepseek-v4-flash", "openai/deepseek-v4-flash"),
        ("glm-4.7-flash", "glm-4.7-flash"),  # no prefix: left untouched
    ],
)
def test_litellm_model_rewrite(model, expected):
    assert AgentLLM._litellm_model(model) == expected


class _FailingProvider:
    """Direct provider that fails with a non-transient error (e.g. 401)."""

    def stream_chat(self, **kwargs):
        raise RuntimeError("Error code: 401 - invalid authentication")


class _FakeChunk:
    def __init__(self, text):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None, tool_calls=None), finish_reason="stop")]
        self.usage = None


@pytest.fixture
def patched(monkeypatch):
    calls = {}

    def fake_completion(**kwargs):
        calls.update(kwargs)
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(llm_module, "select_provider", lambda model, base_url: _FailingProvider())
    monkeypatch.setattr("litellm.completion", fake_completion)
    monkeypatch.setattr(
        AgentLLM,
        "capabilities",
        property(lambda self: ModelCapabilities(max_output_tokens=100)),
    )
    return calls


def test_fallback_rewrites_zhipu_prefixed_model(patched):
    llm = AgentLLM(
        model="zhipu/glm-4.7-flash",
        apiKey="test-key",
        baseUrl="https://open.bigmodel.cn/api/paas/v4",
        timeout=10,
    )
    result = llm.think([{"role": "user", "content": "ping"}], silent=True)

    assert result == "ok"
    assert patched["model"] == "openai/glm-4.7-flash"
    assert patched["api_base"] == "https://open.bigmodel.cn/api/paas/v4"
    assert patched["api_key"] == "test-key"


def test_fallback_rewrites_agnes_prefixed_model(patched):
    llm = AgentLLM(
        model="agnes/agnes-3.0-flash",
        apiKey="agnes-key",
        baseUrl="https://api.agnes-ai.cn/v1",
        timeout=10,
    )
    result = llm.think([{"role": "user", "content": "ping"}], silent=True)

    assert result == "ok"
    assert patched["model"] == "openai/agnes-3.0-flash"
    assert patched["api_base"] == "https://api.agnes-ai.cn/v1"
