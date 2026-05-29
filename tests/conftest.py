"""Shared pytest fixtures for AgentNexus tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_agentnexus_home():
    """临时 .agentnexus 目录，测试后自动清理"""
    import agentnexus.core.config as cfg
    old_home = os.environ.get("AGENTNEXUS_HOME")
    old_cache = cfg._settings_cache
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        os.environ["AGENTNEXUS_HOME"] = tmpdir
        cfg._settings_cache = None
        from agentnexus.memory.long_term import _reset_long_term_memory
        _reset_long_term_memory()
        try:
            yield Path(tmpdir)
        finally:
            cfg._settings_cache = old_cache
            if old_home:
                os.environ["AGENTNEXUS_HOME"] = old_home
            else:
                del os.environ["AGENTNEXUS_HOME"]


@pytest.fixture
def mock_llm_response():
    """返回模拟 LLM 响应的工厂函数"""

    def _make(text="模拟回答"):
        return text

    return _make


@pytest.fixture
def mock_llm(mocker):
    """Mock AgentLLM.think() 返回预设文本"""
    from agentnexus.core.llm import AgentLLM

    mock = mocker.patch.object(AgentLLM, "think", return_value="模拟 LLM 回答")
    return mock


def pytest_addoption(parser):
    parser.addoption("--run-e2e", action="store_true", default=False, help="run e2e tests that call real LLM APIs")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-e2e"):
        return
    skip_e2e = pytest.mark.skip(reason="need --run-e2e option to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def real_llm():
    """Create a real AgentLLM instance (session-scoped to reuse across tests).

    Requires AGENTNEXUS_LLM_API_KEY to be set.
    Falls back to deepseek/deepseek-v4-flash for cost efficiency.
    """
    from agentnexus.core.config import get_settings
    from agentnexus.core.llm import AgentLLM

    settings = get_settings()
    if not settings.llm_api_key:
        pytest.skip("AGENTNEXUS_LLM_API_KEY not set")

    llm = AgentLLM()
    return llm


@pytest.fixture
def real_agent(real_llm, temp_agentnexus_home):
    """Create a real ReActAgent with actual LLM and isolated home directory."""
    from agentnexus.agents.re_act_agent import ReActAgent

    agent = ReActAgent(llm_client=real_llm)
    return agent
