"""Unified runtime assembly for AgentNexus applications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from agentnexus.services import AppServices, ChatService, ConfigService, EvalService, KnowledgeBaseService, SkillService


@dataclass
class AppRuntime:
    settings: Any
    llm: Any
    executor: Any
    agent: Any
    memory_manager: Any
    version_manager: Any
    mcp_manager: Any
    extension_manager: Any
    services: AppServices
    subagent_confirm: Any
    session_id: str

    @classmethod
    def build(cls, profile: str | None = None) -> "AppRuntime":
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.core.config import get_settings
        from agentnexus.core.llm import AgentLLM
        from agentnexus.extensions import ExtensionManager
        from agentnexus.memory.manager import MemoryManager
        from agentnexus.memory.versioned import ConversationVersionManager
        from agentnexus.observability.tracer import trace_manager
        from agentnexus.skills import SkillRegistry
        from agentnexus.tools import register_all_tools
        from agentnexus.tools.confirm_bridge import ConfirmBridge
        from agentnexus.tools.mcp_adapter import create_mcp_manager_from_settings
        from agentnexus.tools.tool_executor import ToolExecutor

        settings = get_settings()
        llm = AgentLLM()
        executor = ToolExecutor()
        subagent_confirm = ConfirmBridge()
        mcp_manager = create_mcp_manager_from_settings(settings)

        extension_manager = ExtensionManager(settings)
        extension_manager.discover()
        extension_manager.load_enabled(runtime=None)

        register_all_tools(
            executor,
            llm_client=llm,
            subagent_confirm=subagent_confirm,
            mcp_manager=mcp_manager,
        )

        try:
            from agentnexus.cli.audit import _global_audit_log

            executor.registry._audit_log = _global_audit_log
        except Exception:
            pass

        prefix = profile or "runtime"
        session_id = f"{prefix}_{uuid.uuid4().hex[:12]}"
        memory = MemoryManager(session_id, llm=llm)
        version = ConversationVersionManager(session_id, settings.memory_db_path)
        agent = ReActAgent(llm, executor, conversation_mode=True)
        skill_registry = SkillRegistry.from_settings(settings)
        skill_registry.discover()
        skill_service = SkillService(
            skill_registry,
            agent=agent,
            auto_route=getattr(settings, "skill_auto_route", True),
            auto_route_llm_fallback=getattr(settings, "skill_auto_route_llm_fallback", True),
            llm_client=llm,
        )
        skill_service.router.min_score = getattr(settings, "skill_auto_route_min_score", 2.0)
        skill_service.router.margin = getattr(settings, "skill_auto_route_margin", 0.75)
        skill_service.use_default(getattr(settings, "default_skill", ""))
        trace_manager.configure(settings.traces_dir)

        services = AppServices(
            chat=ChatService(agent, memory, version, skill_service=skill_service, tool_executor=executor),
            skill=skill_service,
            knowledge_base=KnowledgeBaseService(settings),
            eval=EvalService(settings),
            config=ConfigService(settings, extension_manager),
        )
        return cls(
            settings=settings,
            llm=llm,
            executor=executor,
            agent=agent,
            memory_manager=memory,
            version_manager=version,
            mcp_manager=mcp_manager,
            extension_manager=extension_manager,
            services=services,
            subagent_confirm=subagent_confirm,
            session_id=session_id,
        )

    def close(self) -> None:
        if self.mcp_manager is not None:
            self.mcp_manager.close()
