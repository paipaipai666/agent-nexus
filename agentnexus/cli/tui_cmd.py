"""CLI command: nexus tui — launch terminal-native chat with real ReActAgent."""

import uuid

from . import app


@app.command("tui")
def tui():
    """启动终端原生对话界面（Textual TUI + ReActAgent 后端）"""
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.core.config import get_settings
    from agentnexus.core.llm import AgentLLM
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.observability.tracer import trace_manager
    from agentnexus.tools import register_all_tools
    from agentnexus.tools.confirm_bridge import ConfirmBridge
    from agentnexus.tools.mcp_adapter import create_mcp_manager_from_settings
    from agentnexus.tools.tool_executor import ToolExecutor
    from agentnexus.tui.app import AgentNexusTUI

    settings = get_settings()

    # LLM
    llm = AgentLLM()

    # Tool executor with metadata
    executor = ToolExecutor()
    subagent_confirm = ConfirmBridge()
    mcp_manager = create_mcp_manager_from_settings(settings)
    register_all_tools(
        executor,
        llm_client=llm,
        subagent_confirm=subagent_confirm,
        mcp_manager=mcp_manager,
    )

    # Share audit log with CLI
    try:
        from agentnexus.cli.audit import _global_audit_log
        executor.registry._audit_log = _global_audit_log
    except Exception:
        pass

    # Memory & version control
    session_id = f"tui_{uuid.uuid4().hex[:12]}"
    memory = MemoryManager(session_id, llm=llm)
    version = ConversationVersionManager(session_id, settings.memory_db_path)

    # Agent
    agent = ReActAgent(llm, executor, conversation_mode=True)

    # Initialize trace system (each user input creates its own trace)
    trace_manager.configure(settings.traces_dir)

    # Launch TUI
    tui_app = AgentNexusTUI(agent=agent, memory=memory, version=version, mcp_manager=mcp_manager)
    tui_app._subagent_confirm = subagent_confirm
    try:
        tui_app.run()
    finally:
        if mcp_manager is not None:
            mcp_manager.close()
