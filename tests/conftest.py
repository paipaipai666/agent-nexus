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
