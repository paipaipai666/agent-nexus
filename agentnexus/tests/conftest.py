"""Shared pytest fixtures for AgentNexus tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_agentnexus_home():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        old = os.environ.get("AGENTNEXUS_HOME")
        os.environ["AGENTNEXUS_HOME"] = tmpdir
        try:
            yield Path(tmpdir)
        finally:
            if old is not None:
                os.environ["AGENTNEXUS_HOME"] = old
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
