"""Unified runtime assembly for AgentNexus applications."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnexus.services import AppServices, ChatService, ConfigService, EvalService, KnowledgeBaseService, SkillService

logger = logging.getLogger(__name__)


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
    capability_runtime: Any
    services: AppServices
    subagent_confirm: Any
    session_id: str

    @classmethod
    def build(
        cls,
        profile: str | None = None,
        session_id: str | None = None,
        workspace_path: str | None = None,
        restore_session: bool = False,
    ) -> "AppRuntime":
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_APP_BUILD, {
            "profile": profile, "session_id": session_id,
            "restore_session": restore_session,
        })

        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.capabilities.runtime import CapabilityRuntime
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
        from agentnexus.tools.registry import ToolRegistry

        settings = get_settings()
        llm = AgentLLM()
        executor = ToolRegistry()
        subagent_confirm = ConfirmBridge()
        mcp_manager = create_mcp_manager_from_settings(settings)

        # Resolve session_id early so TodoList can persist to SQLite
        prefix = profile or "runtime"
        session_id = session_id or f"{prefix}_{uuid.uuid4().hex[:12]}"

        from agentnexus.memory.todo import SessionTodoList
        todo_list = SessionTodoList(
            session_id=session_id,
            db_path=settings.memory_db_path,
        )

        extension_manager = ExtensionManager(settings)
        extension_manager.discover()
        extension_manager.load_enabled(runtime=None)

        register_all_tools(
            executor,
            llm_client=llm,
            subagent_confirm=subagent_confirm,
            mcp_manager=mcp_manager,
            extra_providers=[],
            todo_list=todo_list,
        )

        try:
            from agentnexus.observability.audit_log import _global_audit_log

            executor._audit_log = _global_audit_log
        except Exception as e:
            logger.debug("Audit log binding failed: %s", e)

        workspace_path = workspace_path or str(Path.cwd())
        memory = MemoryManager(session_id, llm=llm)
        version = ConversationVersionManager(
            session_id,
            settings.memory_db_path,
            workspace_path=workspace_path,
            profile=profile or "",
        )
        if restore_session:
            cls._restore_memory_from_version(memory, version)
        agent_output = (lambda _: None) if profile == "server" else None
        if profile == "server":
            llm.silent = True

        # Capture MCP context once for all per-session agents
        mcp_ctx = mcp_manager.auto_context() if mcp_manager is not None else None

        # ── Per-session factories (Phase 1: multi-session) ──

        def agent_factory(session_id: str | None = None) -> ReActAgent:
            """Create a lightweight agent per session. Shares LLM, tools, etc."""
            a = ReActAgent(llm, executor, conversation_mode=True, output=agent_output, confirm_fn=subagent_confirm)
            if mcp_ctx is not None and hasattr(a, "set_mcp_context"):
                a.set_mcp_context(mcp_ctx)
            # Each agent needs its own todo_list for persistence
            if session_id:
                from agentnexus.memory.todo import SessionTodoList
                a._todo_list = SessionTodoList(session_id=session_id, db_path=settings.memory_db_path)
            return a

        def make_memory_factory(stm_store: dict, session_id: str):
            """R7: Closure-based factory — absorbs STM on first call, then gone."""
            _restore = cls._restore_memory_from_version if restore_session else None
            _version_for_restore = version
            def factory() -> MemoryManager:
                mm = MemoryManager(session_id, llm=llm)
                # One-time STM migration from legacy _stms dict
                if session_id in stm_store:
                    from agentnexus.memory.short_term import ShortTermMemory
                    restored = ShortTermMemory.from_json(stm_store.pop(session_id))
                    mm.short_term._messages = restored._messages
                    mm.short_term._summary = restored._summary
                # Restore from version manager if this is the build-time session
                elif _restore is not None and _version_for_restore is not None:
                    _restore(mm, _version_for_restore)
                return mm
            return factory

        # Create a build-time agent for SkillService and CapabilityRuntime
        # (they need a reference agent at build time)
        build_agent = agent_factory()
        build_agent._todo_list = todo_list
        skill_registry = SkillRegistry.from_settings(settings)
        skill_registry.discover()
        auto_route = getattr(settings, "skill_auto_route", True)
        auto_route_llm_fallback = getattr(settings, "skill_auto_route_llm_fallback", True)
        min_score = getattr(settings, "skill_auto_route_min_score", 2.0)
        margin = getattr(settings, "skill_auto_route_margin", 0.75)
        default_skill = getattr(settings, "default_skill", "")
        skill_service = SkillService(
            skill_registry,
            agent=build_agent,
            auto_route=auto_route if isinstance(auto_route, bool) else True,
            auto_route_llm_fallback=(
                auto_route_llm_fallback if isinstance(auto_route_llm_fallback, bool) else True
            ),
            llm_client=llm,
        )
        skill_service.router.min_score = min_score if isinstance(min_score, (int, float)) else 2.0
        skill_service.router.margin = margin if isinstance(margin, (int, float)) else 0.75
        skill_service.use_default(default_skill if isinstance(default_skill, str) else "")
        capability_runtime = CapabilityRuntime(
            settings=settings,
            executor=executor,
            agent=build_agent,
            skill_service=skill_service,
            mcp_manager=mcp_manager,
            extension_manager=extension_manager,
            register_tools=register_all_tools,
            llm_client=llm,
            subagent_confirm=subagent_confirm,
        )
        trace_manager.configure(settings.traces_dir)

        # 初始化告警管道
        try:
            from agentnexus.observability.alerting import setup_default_alerts
            setup_default_alerts(settings.traces_dir)
        except Exception as e:
            logger.debug("Alerting setup failed (non-fatal): %s", e)

        hook_mgr.fire(HookType.AFTER_APP_BUILD, {
            "profile": profile, "session_id": session_id,
            "mcp_enabled": mcp_manager is not None,
        })

        # Capture _stms reference for closure-based factory migration (R7)
        # ChatService will be created with factories; _stms is populated lazily
        _stm_store: dict = {}

        services = AppServices(
            chat=ChatService(
                agent_factory=agent_factory,
                memory_factory_builder=lambda sid: make_memory_factory(_stm_store, sid),
                version_manager=version,
                skill_service=skill_service,
                tool_executor=executor,
                capability_runtime=capability_runtime,
            ),
            skill=skill_service,
            knowledge_base=KnowledgeBaseService(settings),
            eval=EvalService(settings),
            config=ConfigService(settings, extension_manager),
        )

        # Restore existing sessions from database into chat._sessions
        # so the frontend can access them without needing restoreSession first.
        try:
            from agentnexus.memory.versioned import ConversationVersionManager
            from agentnexus.services.chat import SessionHandle
            recent = ConversationVersionManager.find_recent_sessions(
                settings.memory_db_path, workspace_path, limit=50
            )
            restored_count = 0
            for s in recent:
                sid = s.get("session_id")
                if sid and sid not in services.chat._sessions:
                    services.chat._sessions[sid] = SessionHandle(
                        id=sid, skill=None, profile=s.get("profile")
                    )
                    restored_count += 1
            print(f"[SessionRestore] Restored {restored_count} sessions from DB (total {len(recent)} found, workspace={ConversationVersionManager.normalize_workspace_path(workspace_path)})")
        except Exception as e:
            print(f"[SessionRestore] FAILED: {e}")
            logger.warning("Session restore from DB failed: %s", e, exc_info=True)

        return cls(
            settings=settings,
            llm=llm,
            executor=executor,
            agent=build_agent,
            memory_manager=memory,
            version_manager=version,
            mcp_manager=mcp_manager,
            extension_manager=extension_manager,
            capability_runtime=capability_runtime,
            services=services,
            subagent_confirm=subagent_confirm,
            session_id=session_id,
        )

    @staticmethod
    def _restore_memory_from_version(memory: Any, version: Any) -> None:
        try:
            snapshot = version.get_head_stm()
        except Exception as e:
            logger.debug("Failed to get head STM snapshot for restore: %s", e)
            snapshot = ""
        if not snapshot:
            return
        from agentnexus.memory.short_term import ShortTermMemory

        restored = ShortTermMemory.from_json(snapshot)
        memory.short_term._messages = restored._messages
        memory.short_term._summary = restored._summary

    def close(self) -> None:
        if self.mcp_manager is not None:
            self.mcp_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
