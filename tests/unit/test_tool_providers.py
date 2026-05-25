from agentnexus.tools.providers import (
    ProviderSpec,
    ToolProviderContext,
    default_tool_providers,
    register_tool_providers,
)
from agentnexus.tools.tool_executor import ToolExecutor


def test_default_providers_expose_specs():
    specs = [provider.metadata() for provider in default_tool_providers()]
    assert all(isinstance(spec, ProviderSpec) for spec in specs)
    assert [spec.name for spec in specs] == [
        "memory",
        "search",
        "filesystem",
        "execution",
        "mcp-bridge",
        "subagent",
    ]


def test_provider_registration_respects_include_tools():
    executor = ToolExecutor()
    registered = register_tool_providers(
        executor,
        context=ToolProviderContext(include_tools={"file_read", "kb_search"}, enable_subagent=False),
    )
    assert set(registered) == {"file_read", "kb_search"}
    assert set(executor.registry.list_tools()) == {"file_read", "kb_search"}

