"""CLI command: nexus sessions - list recent chat sessions."""

from pathlib import Path

import typer

from . import app, console


@app.command("sessions")
def sessions(
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of sessions to show"),
    restore: str = typer.Option(None, "--restore", "-r", help="Restore a specific session by ID"),
):
    """List recent chat sessions or restore a specific session."""
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    workspace = str(Path.cwd())

    # If restore is specified, launch TUI with that session
    if restore:
        if not ConversationVersionManager.session_belongs_to_workspace(
            settings.memory_db_path, restore, workspace
        ):
            console.print(f"[red]Session not found in this directory:[/red] {restore}")
            raise typer.Exit(1)
        from agentnexus.cli.tui_cmd import launch_tui
        launch_tui(session_id=restore, restore_session=True)
        return

    # List sessions
    sessions = ConversationVersionManager.find_recent_sessions(
        settings.memory_db_path, workspace, limit=limit
    )

    if not sessions:
        console.print("[yellow]No previous sessions found in this directory.[/yellow]")
        return

    from rich.table import Table

    table = Table(title="Recent Chat Sessions")
    table.add_column("Session ID", style="cyan")
    table.add_column("Last Message", style="green")
    table.add_column("Preview", style="dim")

    for s in sessions:
        last_msg = s.get("last_message_at", "") or s.get("updated_at", "")
        preview = s.get("preview", "")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        table.add_row(s["session_id"], last_msg, preview)

    console.print(table)
    console.print(
        "\n[dim]Use `nexus --continue=<session_id>` "
        "or `nexus sessions --restore=<session_id>` to resume a session.[/]"
    )
