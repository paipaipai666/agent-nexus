"""Unit tests for codegraph.parser module."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnexus.codegraph.models import ParseResult
from agentnexus.codegraph.parser import (
    LanguageParser,
    _ext_to_language,
    _parser_registry,
    auto_register_parsers,
    get_parser,
    get_parser_for_file,
    list_parsers,
    register_parser,
)


class MockParser:
    """Mock parser for testing."""

    @property
    def language(self) -> str:
        return "mock"

    @property
    def file_extensions(self) -> list[str]:
        return [".mock", ".test"]

    def parse_file(self, file_path: Path, content: str) -> ParseResult:
        return ParseResult()


@pytest.fixture(autouse=True)
def clean_registry():
    """Save and restore registry state for each test."""
    old_registry = _parser_registry.copy()
    old_ext = _ext_to_language.copy()
    yield
    _parser_registry.clear()
    _parser_registry.update(old_registry)
    _ext_to_language.clear()
    _ext_to_language.update(old_ext)


class TestRegisterParser:
    def test_register_parser(self):
        parser = MockParser()
        register_parser(parser)
        assert "mock" in _parser_registry
        assert _parser_registry["mock"] is parser

    def test_register_maps_extensions(self):
        parser = MockParser()
        register_parser(parser)
        assert _ext_to_language[".mock"] == "mock"
        assert _ext_to_language[".test"] == "mock"

    def test_register_lowercase_extensions(self):
        class UpperExtParser:
            @property
            def language(self):
                return "upper"

            @property
            def file_extensions(self):
                return [".PY"]

            def parse_file(self, file_path, content):
                return ParseResult()

        register_parser(UpperExtParser())
        assert _ext_to_language[".py"] == "upper"


class TestGetParser:
    def test_get_existing_parser(self):
        parser = MockParser()
        register_parser(parser)
        result = get_parser("mock")
        assert result is parser

    def test_get_nonexistent_parser(self):
        result = get_parser("nonexistent")
        assert result is None


class TestGetParserForFile:
    def test_get_by_extension(self):
        parser = MockParser()
        register_parser(parser)
        result = get_parser_for_file(Path("test.mock"))
        assert result is parser

    def test_get_unknown_extension(self):
        result = get_parser_for_file(Path("test.xyz"))
        assert result is None

    def test_case_insensitive(self):
        parser = MockParser()
        register_parser(parser)
        result = get_parser_for_file(Path("test.MOCK"))
        assert result is parser


class TestListParsers:
    def test_empty_registry(self):
        # Registry might have parsers from other tests, just check it returns a list
        result = list_parsers()
        assert isinstance(result, list)

    def test_with_registered_parser(self):
        parser = MockParser()
        register_parser(parser)
        result = list_parsers()
        assert parser in result


class TestAutoRegisterParsers:
    def test_auto_register(self):
        auto_register_parsers()
        python_parser = get_parser("python")
        assert python_parser is not None
        assert python_parser.language == "python"
        assert ".py" in python_parser.file_extensions


class TestLanguageParserProtocol:
    def test_protocol_compliance(self):
        parser = MockParser()
        assert isinstance(parser, LanguageParser)

    def test_python_parser_compliance(self):
        auto_register_parsers()
        parser = get_parser("python")
        assert isinstance(parser, LanguageParser)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
