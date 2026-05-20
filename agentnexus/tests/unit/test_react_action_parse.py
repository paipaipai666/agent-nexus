"""Tests for ReAct action parsing with structured XML params."""
from unittest.mock import MagicMock
from agentnexus.agents.re_act_agent import ReActAgent


class TestStructuredActionParse:
    def setup_method(self):
        self.agent = ReActAgent(
            llm_client=MagicMock(),
            tool_executor=MagicMock(),
        )

    def test_parse_flat_text_xml(self):
        """旧版扁平文本格式仍然兼容"""
        action = '<action type="tool" name="web_search">北京天气</action>'
        name, params = self.agent._parse_action(action)
        assert name == "web_search"
        assert params == "北京天气"

    def test_parse_structured_xml_single_param(self):
        """结构化格式：单个参数"""
        action = (
            '<action type="tool" name="web_search">'
            '<query>北京天气</query>'
            '</action>'
        )
        name, params = self.agent._parse_action(action)
        assert name == "web_search"
        assert params == {"query": "北京天气"}

    def test_parse_structured_xml_multi_params(self):
        """结构化格式：多个参数"""
        action = (
            '<action type="tool" name="web_search">'
            '<query>AI新闻</query>'
            '<max_results>10</max_results>'
            '<time_range>week</time_range>'
            '<topic>news</topic>'
            '</action>'
        )
        name, params = self.agent._parse_action(action)
        assert name == "web_search"
        assert params["query"] == "AI新闻"
        assert params["max_results"] == 10
        assert params["time_range"] == "week"
        assert params["topic"] == "news"

    def test_parse_structured_include_answer(self):
        """include_answer 布尔值"""
        action = (
            '<action type="tool" name="web_search">'
            '<query>北京天气</query>'
            '<include_answer>true</include_answer>'
            '</action>'
        )
        name, params = self.agent._parse_action(action)
        assert params["include_answer"] is True

    def test_parse_legacy_text_format(self):
        """旧版文本格式兼容"""
        action = 'web_search[北京天气]'
        name, params = self.agent._parse_action(action)
        assert name == "web_search"
        assert params == "北京天气"

    def test_parse_finish_xml(self):
        """Finish action 不受影响"""
        action = '<action type="finish">答案是42</action>'
        result = self.agent._parse_finish(action)
        assert result == "答案是42"

    def test_parse_structured_with_newlines(self):
        """多行结构化"""
        action = (
            '<action type="tool" name="web_search">\n'
            '  <query>北京天气</query>\n'
            '  <max_results>10</max_results>\n'
            '</action>'
        )
        name, params = self.agent._parse_action(action)
        assert params["query"] == "北京天气"
        assert params["max_results"] == 10
