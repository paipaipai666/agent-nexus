"""CLI command: nexus tui - launch terminal-native chat with real ReActAgent."""

from . import app


def launch_tui(session_id: str | None = None, restore_session: bool = False):
    """Launch terminal-native chat with the shared app runtime."""
    from agentnexus.app import AppRuntime
    from agentnexus.tui.app import AgentNexusTUI

    runtime = AppRuntime.build(
        profile="tui",
        session_id=session_id,
        restore_session=restore_session,
    )
    tui_app = AgentNexusTUI(
        agent=runtime.agent,
        memory=runtime.memory_manager,
        version=runtime.version_manager,
        mcp_manager=runtime.mcp_manager,
        skill_service=runtime.services.skill,
        capability_runtime=runtime.capability_runtime,
    )
    tui_app._subagent_confirm = runtime.subagent_confirm
    try:
        tui_app.run()
    finally:
        runtime.close()


@app.command("tui")
def tui():
    """Start the terminal-native chat interface."""
    launch_tui()
