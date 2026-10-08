"""Tests for tool description boundary enrichment.

Verifies that all registered tools include '[不适用]' boundary text
so the LLM knows when NOT to use each tool.
"""

from __future__ import annotations

import pytest

from agentnexus.tools.providers import register_tool_providers
from agentnexus.tools.registry import ToolRegistry


@pytest.fixture()
def registry_with_all_providers() -> ToolRegistry:
    """Register all default providers and return the registry."""
    reg = ToolRegistry()
    register_tool_providers(reg)
    return reg


# Tools that are self-explanatory (namespace-scoped action tools) — skip boundary check
_SKIP_TOOLS = {
    "subagent_run",          # checked separately (complex delegation tool)
    "shell_exec",            # description carries a "[!]" confirmation warning instead
}


class TestBoundaryDescriptions:
    """All tool descriptions should include '[不适用]' boundary text."""

    def test_all_tools_have_boundary_text(self, registry_with_all_providers: ToolRegistry):
        reg = registry_with_all_providers
        missing = []
        for meta in reg.list_tools_with_meta():
            if meta.name in _SKIP_TOOLS:
                continue
            if "[不适用]" not in meta.description:
                missing.append(meta.name)
        assert not missing, f"Tools missing '[不适用]' boundary: {missing}"

    def test_descriptions_concise(self, registry_with_all_providers: ToolRegistry):
        """Descriptions should stay under 500 chars for context efficiency."""
        reg = registry_with_all_providers
        too_long = []
        for meta in reg.list_tools_with_meta():
            if len(meta.description) > 500:
                too_long.append((meta.name, len(meta.description)))
        assert not too_long, f"Descriptions over 500 chars: {too_long}"

    def test_boundary_references_valid_tools(self, registry_with_all_providers: ToolRegistry):
        """Boundary text should reference tool names that actually exist."""
        import re
        reg = registry_with_all_providers
        all_names = set(reg.list_tools())

        errors = []
        for meta in reg.list_tools_with_meta():
            # Only check the [不适用] boundary portion
            boundary_match = re.search(r"\[不适用\](.+)", meta.description)
            if not boundary_match:
                continue
            boundary = boundary_match.group(1)
            # Extract tool references like "(用grep_search)" — ASCII tool names only
            refs = re.findall(r"用([a-zA-Z_][a-zA-Z0-9_]*)", boundary)
            for ref in refs:
                if ref not in all_names:
                    errors.append(f"{meta.name}: references unknown tool '{ref}'")
        assert not errors, f"Boundary reference errors: {errors}"
