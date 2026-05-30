"""Performance: register_all_tools registration speed."""
from __future__ import annotations

REGISTER_TOOLS_P95_MAX_MS = 50
REGISTER_TOOLS_FILTER_P95_MAX_MS = 30


def test_register_all_tools_full(benchmark):
    from agentnexus.tools import register_all_tools
    from agentnexus.tools.registry import ToolRegistry

    def _run():
        executor = ToolRegistry()
        register_all_tools(executor)
    benchmark(_run)


def test_register_all_tools_filtered(benchmark):
    from agentnexus.tools import register_all_tools
    from agentnexus.tools.registry import ToolRegistry

    def _run():
        executor = ToolRegistry()
        register_all_tools(executor, include_tools={"web_search", "file_read", "file_list"})
    benchmark(_run)
