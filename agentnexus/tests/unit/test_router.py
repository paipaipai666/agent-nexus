"""Tests for query router"""
from agentnexus.rag.router import is_code_query


class TestCodeQueryDetection:
    def test_code_keyword(self):
        assert is_code_query("def parse function") == True
        assert is_code_query("import os and run") == True

    def test_chinese_code_keyword(self):
        assert is_code_query("这段代码怎么改") == True
        assert is_code_query("帮我写个函数") == True

    def test_natural_language(self):
        assert is_code_query("什么是向量数据库") == False
        assert is_code_query("如何配置LLM") == False

    def test_empty_query(self):
        assert is_code_query("") == False
