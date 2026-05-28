"""Language parser protocol and registry.

Defines the pluggable parser interface and manages parser registration
by language and file extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from agentnexus.codegraph.models import ParseResult


@runtime_checkable
class LanguageParser(Protocol):
    """Pluggable language parser interface."""

    @property
    def language(self) -> str:
        """Language identifier, e.g. 'python', 'typescript'."""
        ...

    @property
    def file_extensions(self) -> list[str]:
        """Supported file extensions, e.g. ['.py']."""
        ...

    def parse_file(self, file_path: Path, content: str) -> ParseResult:
        """Parse file content and return nodes/edges."""
        ...


# Parser registry: language -> parser instance
_parser_registry: dict[str, LanguageParser] = {}
# Extension -> language mapping (populated on registration)
_ext_to_language: dict[str, str] = {}


def register_parser(parser: LanguageParser) -> None:
    """Register a language parser."""
    _parser_registry[parser.language] = parser
    for ext in parser.file_extensions:
        _ext_to_language[ext.lower()] = parser.language


def get_parser(language: str) -> LanguageParser | None:
    """Get parser by language identifier."""
    return _parser_registry.get(language)


def get_parser_for_file(file_path: Path) -> LanguageParser | None:
    """Get parser by file extension."""
    suffix = file_path.suffix.lower()
    language = _ext_to_language.get(suffix)
    if language:
        return _parser_registry.get(language)
    return None


def list_parsers() -> list[LanguageParser]:
    """List all registered parsers."""
    return list(_parser_registry.values())


def auto_register_parsers() -> None:
    """Register all built-in parsers."""
    try:
        from agentnexus.codegraph.parsers.python_parser import PythonParser
        register_parser(PythonParser())
    except ImportError:
        pass


__all__ = [
    "LanguageParser",
    "register_parser",
    "get_parser",
    "get_parser_for_file",
    "list_parsers",
    "auto_register_parsers",
]
