"""CLI command: nexus tui — launch terminal-native chat with real ReActAgent."""

from . import app


@app.command("tui")
def tui():
    """启动终端原生对话界面（Textual TUI + ReActAgent 后端）"""
    from agentnexus.app import AppRuntime
    from agentnexus.tui.app import AgentNexusTUI

    runtime = AppRuntime.build(profile="tui")
    tui_app = AgentNexusTUI(
        agent=runtime.agent,
        memory=runtime.memory_manager,
        version=runtime.version_manager,
        mcp_manager=runtime.mcp_manager,
    )
    tui_app._subagent_confirm = runtime.subagent_confirm
    try:
        tui_app.run()
    finally:
        runtime.close()
