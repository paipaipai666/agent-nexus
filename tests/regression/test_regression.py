"""Core system regression tests.

Covers config, storage, memory, RAG, tools, CLI, and the new provider system.
"""

import pytest


def test_config_loading():
    from agentnexus.core.config import get_settings

    assert get_settings().llm_model_id


@pytest.mark.xfail(reason="Pre-existing circular import between storage.chroma and rag")
def test_chromadb_store_and_search(temp_agentnexus_home):
    from agentnexus.storage.chroma import delete_collection, insert_documents, search

    delete_collection()
    insert_documents(["Qdrant 是向量数据库", "LangGraph 是多智能体框架"])
    results = search("什么是多智能体", limit=1)

    assert results
    assert "LangGraph" in results[0]["text"]


def test_short_term_memory():
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    stm.append("user", "你好")
    stm.append("assistant", "你好")

    assert len(stm.get_all()) == 2


def test_long_term_memory(temp_agentnexus_home):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    ltm.save("test", "用户喜欢简洁回答", category="user_preference", importance=0.9)

    assert len(ltm.list_recent(3)) >= 1


def test_ingestion_clean_text():
    from agentnexus.rag.ingestion import clean_text

    assert len(clean_text("这是一个  \n\n测试文档  \x00")) > 0


def test_hybrid_retrieval(temp_agentnexus_home):
    from agentnexus.rag.retriever import build_knowledge_base, search_knowledge_base
    from agentnexus.storage.chroma import delete_collection

    delete_collection()
    build_knowledge_base(
        ["Python 用于 AI", "Qdrant 向量库", "LangGraph 多智能体", "BM25 文本检索"],
        load_reranker=False,
    )

    assert "BM25" in search_knowledge_base("检索用什么")


def test_tool_executor():
    from agentnexus.tools.tool_executor import ToolExecutor

    te = ToolExecutor()
    te.registerTool("Echo", "回显", lambda x: f"ECHO:{x}")

    assert te.getTool("Echo")("hello") == "ECHO:hello"


def test_cli_entry():
    from agentnexus.cli import app

    assert app is not None


# ── Provider system regression ─────────────────────────────────────────


def test_provider_router_anthropic_skipped():
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("anthropic/claude-4.5", "https://api.anthropic.com")
    assert provider is None


def test_provider_router_azure_skipped():
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("openai/gpt-4", "https://myresource.openai.azure.com")
    assert provider is None


def test_provider_router_deepseek_uses_openai():
    from agentnexus.core.providers.openai_provider import OpenAIProvider
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("deepseek/deepseek-v4-flash", "https://api.deepseek.com")
    assert isinstance(provider, OpenAIProvider)


def test_provider_router_openai_uses_openai():
    from agentnexus.core.providers.openai_provider import OpenAIProvider
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("openai/gpt-4o", "https://api.openai.com")
    assert isinstance(provider, OpenAIProvider)


def test_provider_router_zhipu_uses_openai():
    from agentnexus.core.providers.openai_provider import OpenAIProvider
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("zhipu/glm-4-flash", "https://open.bigmodel.cn/api/paas/v4/")
    assert isinstance(provider, OpenAIProvider)


def test_provider_router_unknown_defaults_to_openai():
    from agentnexus.core.providers.openai_provider import OpenAIProvider
    from agentnexus.core.providers.router import select_provider

    provider = select_provider("local/my-model", "http://localhost:11434/v1")
    assert isinstance(provider, OpenAIProvider)


def test_provider_singleton_cached():
    from agentnexus.core.providers.router import select_provider

    p1 = select_provider("openai/gpt-4", "https://api.openai.com")
    p2 = select_provider("deepseek/v4", "https://api.deepseek.com")
    assert p1 is p2


# ── Model normalization regression ─────────────────────────────────────


def test_normalize_model_id_with_prefix():
    from agentnexus.core.capabilities import _normalize_model_id

    assert _normalize_model_id("openai/gpt-4", "https://api.openai.com") == "openai/gpt-4"
    assert _normalize_model_id("deepseek/v4", "https://api.deepseek.com") == "deepseek/v4"


def test_normalize_model_id_infers_prefix():
    from agentnexus.core.capabilities import _normalize_model_id

    assert _normalize_model_id("gpt-4", "https://api.openai.com") == "openai/gpt-4"
    assert _normalize_model_id("v4", "https://api.deepseek.com") == "deepseek/v4"
    assert _normalize_model_id("claude", "https://api.anthropic.com") == "anthropic/claude"
    assert _normalize_model_id("glm-4", "https://open.bigmodel.cn") == "zhipu/glm-4"


def test_normalize_model_id_unknown_defaults_to_openai():
    from agentnexus.core.capabilities import _normalize_model_id

    assert _normalize_model_id("my-model", "https://custom.api.com") == "openai/my-model"


# ── Capability detection regression ────────────────────────────────────


def test_capability_registry_deepseek():
    from agentnexus.core.capabilities import detect_capabilities

    caps = detect_capabilities("deepseek/deepseek-v4-flash", "https://api.deepseek.com")
    assert caps.supports_tool_calling is True
    assert caps.supports_thinking is True


def test_capability_registry_openai_gpt4():
    from agentnexus.core.capabilities import detect_capabilities

    caps = detect_capabilities("openai/gpt-4o", "https://api.openai.com")
    assert caps.supports_tool_calling is True
    assert caps.supports_json_mode is True


def test_capability_registry_unknown_model():
    from agentnexus.core.capabilities import detect_capabilities

    caps = detect_capabilities("unknown/model", "https://example.com")
    # Falls back to default — conservative
    assert caps.supports_tool_calling is False


def test_session_capability_tracker():
    from agentnexus.core.capabilities import SessionCapabilityTracker

    tracker = SessionCapabilityTracker()
    assert tracker.is_available("tool_calling", True) is True

    tracker.mark_failed("tool_calling")
    assert tracker.is_available("tool_calling", True) is False
    assert tracker.is_available("json_mode", True) is True  # other features unaffected

    tracker.reset("tool_calling")
    assert tracker.is_available("tool_calling", True) is True


# ── Token estimation regression ────────────────────────────────────────


def test_short_term_memory_token_estimation():
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    stm.append("user", "Hello world")
    stm.append("assistant", "Hi there, how can I help?")

    tokens = stm.estimate_tokens()
    assert tokens > 0
    assert tokens < 100  # sanity check


def test_short_term_memory_token_estimation_chinese():
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    stm.append("user", "你好世界，这是一个测试消息")

    tokens = stm.estimate_tokens()
    assert tokens > 0


def test_short_term_memory_token_estimation_empty():
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    assert stm.estimate_tokens() == 0


def test_llm_estimate_usage():
    from unittest.mock import patch

    with patch("agentnexus.core.llm.get_settings") as mock_s:
        mock_s.return_value.llm_model_id = "openai/gpt-4"
        mock_s.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_s.return_value.llm_base_url = "https://api.openai.com"
        mock_s.return_value.llm_timeout = 30
        from agentnexus.core.llm import AgentLLM
        llm = AgentLLM()

    usage = llm._estimate_usage(
        "openai/gpt-4",
        [{"role": "user", "content": "Hello world"}],
        "Hi there!",
    )
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


# ── StreamResult regression ────────────────────────────────────────────


def test_stream_result_properties():
    from agentnexus.core.providers.base import StreamResult

    r = StreamResult(
        text="Hello",
        finish_reason="stop",
        tool_calls=[{"name": "test"}],
        reasoning_content="thinking...",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    assert r.text == "Hello"
    assert r.truncated is False
    assert len(r.tool_calls) == 1

    r_truncated = StreamResult(finish_reason="length")
    assert r_truncated.truncated is True

    r_max = StreamResult(finish_reason="max_tokens")
    assert r_max.truncated is True
