"""Unified runtime assembly for AgentNexus applications."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentnexus.core.modules import ModuleContext, ModuleFactories, ModuleHost
from agentnexus.services import ChatService, SkillService

if TYPE_CHECKING:
    from agentnexus.capabilities.runtime import CapabilityRuntime
    from agentnexus.core.config import Settings
    from agentnexus.core.llm import AgentLLM
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.tools.confirm_bridge import ConfirmBridge
    from agentnexus.tools.mcp.adapter import MCPManager
    from agentnexus.tools.registry import ToolRegistry
    from agentnexus.wiki.wiki_service import WikiService

logger = logging.getLogger(__name__)


@dataclass
class AppRuntime:
    settings: "Settings"
    llm: "AgentLLM | None"
    executor: "ToolRegistry"
    agent: Any
    memory_manager: "MemoryManager | None"
    version_manager: "ConversationVersionManager"
    mcp_manager: "MCPManager | None"
    capability_runtime: "CapabilityRuntime"
    chat: ChatService
    skill: SkillService
    subagent_confirm: "ConfirmBridge"
    session_id: str
    wiki_service: "WikiService | None" = None
    modules: ModuleHost | None = None
    _closed: bool = field(default=False, init=False, repr=False)
    _module_scopes: list = field(default_factory=list, init=False, repr=False)

    @classmethod
    def build(
        cls,
        profile: str | None = None,
        session_id: str | None = None,
        workspace_path: str | None = None,
        restore_session: bool = False,
        *,
        settings=None,
        modules: ModuleHost | None = None,
        module_factories: ModuleFactories | None = None,
    ) -> "AppRuntime":
        """Assemble selected modules without loading disabled implementations."""
        host = modules or ModuleHost(settings, factories=module_factories)
        try:
            with host.activate():
                return cls._build(profile, session_id, workspace_path, restore_session, modules=host)
        except Exception:
            host.close()
            raise

    @classmethod
    def _build(
        cls,
        profile: str | None,
        session_id: str | None,
        workspace_path: str | None,
        restore_session: bool,
        *,
        modules: ModuleHost,
    ) -> "AppRuntime":
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_APP_BUILD, {
            "profile": profile, "session_id": session_id,
            "restore_session": restore_session,
        })

        from agentnexus.capabilities.runtime import CapabilityRuntime
        from agentnexus.core.llm import AgentLLM
        from agentnexus.memory.versioned import ConversationVersionManager
        from agentnexus.skills import SkillRegistry
        from agentnexus.tools import register_all_tools
        from agentnexus.tools.confirm_bridge import ConfirmBridge
        from agentnexus.tools.mcp.adapter import create_mcp_manager_from_settings
        from agentnexus.tools.registry import ToolRegistry

        settings = modules.settings
        llm = AgentLLM() if modules.is_enabled("agent") else None
        executor = ToolRegistry()
        subagent_confirm = ConfirmBridge()
        mcp_manager = create_mcp_manager_from_settings(settings)

        prefix = profile or "runtime"
        session_id = session_id or f"{prefix}_{uuid.uuid4().hex[:12]}"

        from agentnexus.memory.todo import SessionTodoList
        todo_list = SessionTodoList(
            session_id=session_id,
            db_path=settings.memory_db_path,
        )

        agent_output = (lambda _: None) if profile == "server" else None
        if profile == "server" and llm is not None:
            llm.silent = True
        build_agent = modules.optional("agent", ModuleContext(
            settings=settings, llm=llm, tools=executor, session_id=session_id,
            workspace_path=workspace_path, output=agent_output, confirm_fn=subagent_confirm,
        ))

        register_all_tools(
            executor,
            llm_client=llm,
            subagent_confirm=subagent_confirm,
            mcp_manager=mcp_manager,
            extra_providers=[],
            todo_list=todo_list,
            modules=modules,
            enable_subagent=build_agent is not None,
        )

        workspace_path = workspace_path or str(Path.cwd())

        def create_memory(sid: str, workspace: str | None):
            if not modules.is_enabled("short_term") and not modules.is_enabled("long_term"):
                return None
            from agentnexus.memory.manager import MemoryManager

            return MemoryManager(sid, llm=llm, workspace_path=workspace, modules=modules)

        memory = create_memory(session_id, workspace_path)
        version = ConversationVersionManager(
            session_id,
            settings.memory_db_path,
            workspace_path=workspace_path,
            profile=profile or "",
        )
        if restore_session and memory is not None:
            cls._restore_memory_from_version(memory, version)

        mcp_ctx = mcp_manager.auto_context() if mcp_manager is not None else None

        def agent_factory(session_id: str | None = None):
            """Create a lightweight agent per session. Shares LLM, tools, etc."""
            a = modules.require("agent", ModuleContext(
                settings=settings, llm=llm, tools=executor, session_id=session_id,
                workspace_path=workspace_path, output=agent_output, confirm_fn=subagent_confirm,
            ))
            if mcp_ctx is not None and hasattr(a, "set_mcp_context"):
                a.set_mcp_context(mcp_ctx)
            if session_id:
                from agentnexus.memory.todo import SessionTodoList
                if hasattr(a, "todo_list"):
                    a.todo_list = SessionTodoList(session_id=session_id, db_path=settings.memory_db_path)
            return a

        def make_memory_factory(session_id: str, workspace_resolver=None):
            """Closure-based factory for a session MemoryManager.

            workspace_resolver: optional callable(session_id) -> str | None, used
            to bind project memory to the session's workspace (falls back to the
            build-time workspace).
            """
            _restore = cls._restore_memory_from_version if restore_session else None
            def factory():
                ws = workspace_path
                if workspace_resolver is not None:
                    try:
                        ws = workspace_resolver(session_id) or workspace_path
                    except Exception:
                        pass
                mm = create_memory(session_id, ws)
                if _restore is not None and mm is not None:
                    session_version = version if session_id == version.session_id else ConversationVersionManager(
                        session_id, settings.memory_db_path, workspace_path=ws,
                    )
                    _restore(mm, session_version)
                return mm
            return factory

        if build_agent is not None:
            if hasattr(build_agent, "todo_list"):
                build_agent.todo_list = todo_list
            if mcp_ctx is not None and hasattr(build_agent, "set_mcp_context"):
                build_agent.set_mcp_context(mcp_ctx)
        skill_registry = SkillRegistry.from_settings(settings, workspace_path=workspace_path)
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
            register_tools=register_all_tools,
            llm_client=llm,
            subagent_confirm=subagent_confirm,
            modules=modules,
        )

        hook_mgr.fire(HookType.AFTER_APP_BUILD, {
            "profile": profile, "session_id": session_id,
            "mcp_enabled": mcp_manager is not None,
        })

        def _resolve_session_workspace(sid: str) -> str | None:
            """Late-binding lookup of a session's workspace (chat assigned below)."""
            try:
                handle = chat_service._sessions.get(sid)
                return handle.workspace if handle else None
            except Exception:
                return None

        chat_service = ChatService(
            agent_factory=agent_factory,
            memory_factory_builder=lambda sid: make_memory_factory(sid, workspace_resolver=_resolve_session_workspace),
            version_manager=version,
            skill_service=skill_service,
            tool_executor=executor,
            capability_runtime=capability_runtime,
            modules=modules,
        )

        from agentnexus.services.chat import SessionHandle

        chat_service.attach_session(
            SessionHandle(session_id, profile=profile, workspace=workspace_path),
            agent=build_agent, memory=memory, version=version,
        )

        try:
            from agentnexus.memory.versioned import ConversationVersionManager
            from agentnexus.services.chat import SessionHandle
            recent = ConversationVersionManager.find_recent_sessions(
                settings.memory_db_path, None, limit=50
            )
            restored_count = 0
            for s in recent:
                sid = s.get("session_id")
                if sid and sid not in chat_service._sessions:
                    chat_service._sessions[sid] = SessionHandle(
                        id=sid, skill=None, profile=s.get("profile"),
                        workspace=s.get("workspace_path") or None,
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
            capability_runtime=capability_runtime,
            chat=chat_service,
            skill=skill_service,
            subagent_confirm=subagent_confirm,
            session_id=session_id,
            modules=modules,
        )

    @staticmethod
    def _restore_memory_from_version(memory: Any, version: Any) -> None:
        if getattr(memory, "retention_enabled", True) is False:
            return
        try:
            snapshot = version.get_head_stm()
        except Exception as e:
            logger.debug("Failed to get head STM snapshot for restore: %s", e)
            snapshot = ""
        if not snapshot:
            return
        from agentnexus.core.memory_contracts import restore_snapshot

        restore_snapshot(memory.short_term, snapshot)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.mcp_manager is not None:
                self.mcp_manager.close()
        finally:
            if self.modules is not None:
                self.modules.close()

    def __enter__(self):
        if self._closed:
            raise RuntimeError("Application runtime is closed")
        if self.modules is not None:
            scope = self.modules.activate()
            scope.__enter__()
            self._module_scopes.append(scope)
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        finally:
            if self._module_scopes:
                self._module_scopes.pop().__exit__(*exc)
        return False
