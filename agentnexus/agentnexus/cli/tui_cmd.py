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
    from agentnexus.tools.code_executor import python_execute
    from agentnexus.tools.memory_save import memory_save
    from agentnexus.tools.memory_search import memory_search
    from agentnexus.tools.tool_executor import ToolExecutor
    from agentnexus.tools.web_search import web_search
    from agentnexus.tui.app import AgentNexusTUI

    # LLM
    llm = AgentLLM()

    # Tool executor with metadata
    executor = ToolExecutor()
    executor.registerTool(
        "memory_search",
        "检索长期记忆中的用户偏好、历史事实和结论，参数为搜索关键词",
        memory_search,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        risk_level="low",
        rate_limit_per_min=10,
    )
    executor.registerTool(
        "memory_save",
        "主动保存重要信息到长期记忆。当用户明确分享个人信息(姓名/偏好/背景)或发现重要事实时使用",
        memory_save,
        param_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "category": {"type": "string", "default": "entity_fact"},
                "importance": {"type": "number", "default": 0.7},
            },
            "required": ["content"],
        },
        risk_level="low",
        rate_limit_per_min=10,
    )
    executor.registerTool(
        "web_search",
        "搜索互联网获取实时信息，参数为搜索关键词",
        web_search,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        risk_level="low",
        rate_limit_per_min=10,
    )
    executor.registerTool(
        "python_execute",
        "在安全沙箱中执行Python代码，参数为代码字符串",
        python_execute,
        param_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        risk_level="high",
        require_hitl=True,
        timeout_sec=60,
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
    version = ConversationVersionManager(session_id, get_settings().memory_db_path)

    # Agent
    agent = ReActAgent(llm, executor, conversation_mode=True)

    # Launch TUI
    tui_app = AgentNexusTUI(agent=agent, memory=memory, version=version)
    tui_app.run()
