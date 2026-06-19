"""Shared infrastructure for tool providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from agentnexus.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agentnexus.core.llm import AgentLLM
    from agentnexus.memory.todo import SessionTodoList
    from agentnexus.tools.confirm_bridge import ConfirmBridge
    from agentnexus.tools.mcp_adapter import MCPManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata describing a group of tools that can be registered together."""

    name: str
    version: str = "1.0"
    default_enabled: bool = True
    required_config: tuple[str, ...] = ()
    exposed_agents: tuple[str, ...] = ("*",)
    description: str = ""


@dataclass
class ToolProviderContext:
    """Runtime inputs shared by tool providers during registration."""

    non_interactive: bool = False
    llm_client: "AgentLLM | None" = None
    include_tools: set[str] | None = None
    enable_subagent: bool = True
    subagent_confirm: "ConfirmBridge | None" = None
    mcp_manager: "MCPManager | None" = None
    runtime: Any = None
    extension_context: Any = None
    source_type: str = "builtin"
    source_id: str = ""
    generation: int = 0
    registered_tools: list[str] = field(default_factory=list)
    todo_list: "SessionTodoList | None" = None

    def want(self, name: str) -> bool:
        return self.include_tools is None or name in self.include_tools

    def mark_registered(self, executor: ToolRegistry, before: set[str]) -> None:
        after = set(executor.list_tools())
        added = sorted(after - before)
        for name in added:
            logger.warning("mark_registered() bypasses register() validation for tool '%s'", name)
        source_id = self.source_id or self.source_type
        for name in added:
            entry = executor._tools.get(name)
            if entry is None:
                continue
            meta, func = entry
            if meta.source_type != "unknown" or meta.source_id != "unknown":
                continue
            meta.source_type = self.source_type
            meta.source_id = source_id
            meta.generation = self.generation
            executor._tools[name] = (meta, func)
        self.registered_tools.extend(added)

    def for_provider(self, provider_name: str, source_type: str | None = None, generation: int | None = None):
        return ToolProviderContext(
            non_interactive=self.non_interactive,
            llm_client=self.llm_client,
            include_tools=self.include_tools,
            enable_subagent=self.enable_subagent,
            subagent_confirm=self.subagent_confirm,
            mcp_manager=self.mcp_manager,
            runtime=self.runtime,
            extension_context=self.extension_context,
            source_type=source_type or self.source_type,
            source_id=provider_name,
            generation=self.generation if generation is None else generation,
            registered_tools=self.registered_tools,
            todo_list=self.todo_list,
        )


class ToolProvider(Protocol):
    """Register one cohesive group of tools on a ToolRegistry."""

    def metadata(self) -> ProviderSpec:
        ...

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        ...
