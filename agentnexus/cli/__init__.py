"""AgentNexus CLI."""
# ruff: noqa: E402,F401
import sys
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(
    name="nexus",
    help="AgentNexus CLI",
)
console = Console()

kb_app = typer.Typer(help="Knowledge base management")
app.add_typer(kb_app, name="kb")

memory_app = typer.Typer(help="Memory management")
app.add_typer(memory_app, name="memory")

logs_app = typer.Typer(help="Trace log viewer")
app.add_typer(logs_app, name="logs")

eval_app = typer.Typer(help="RAG evaluation")
app.add_typer(eval_app, name="eval")

skill_app = typer.Typer(help="Skill / workflow management")
app.add_typer(skill_app, name="skill")

from agentnexus.cli import (
    audit,  # noqa: E402
    config,  # noqa: E402
    eval_cmd,  # noqa: E402
    kb,  # noqa: E402
    logs,  # noqa: E402
    memory_cmd,  # noqa: E402
    skill_cmd,  # noqa: E402
    stats,  # noqa: E402
    tui_cmd,  # noqa: E402
)


def _continue_session(session_id: str | None) -> None:
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    workspace = str(Path.cwd())

    if session_id:
        if not ConversationVersionManager.session_belongs_to_workspace(
            settings.memory_db_path,
            session_id,
            workspace,
        ):
            console.print(f"[red]Session not found in this directory:[/red] {session_id}")
            raise SystemExit(1)
    else:
        session_id = ConversationVersionManager.find_latest_session(settings.memory_db_path, workspace)
        if not session_id:
            console.print("[yellow]No previous session found in this directory.[/yellow]")
            raise SystemExit(1)

    from agentnexus.cli.tui_cmd import launch_tui

    launch_tui(session_id=session_id, restore_session=True)


def main(argv: list[str] | None = None) -> None:
    """Console-script entrypoint with pre-parsing for `nexus --continue [session_id]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and (args[0] == "--continue" or args[0].startswith("--continue=")):
        if args[0].startswith("--continue="):
            session_id = args[0].split("=", 1)[1] or None
            extra = args[1:]
        else:
            session_id = args[1] if len(args) > 1 else None
            extra = args[2:]
        if extra:
            console.print("[red]--continue accepts at most one session_id.[/red]")
            raise SystemExit(2)
        _continue_session(session_id)
        return
    app(args=args, prog_name="nexus")


@app.command()
def version():
    """Show version."""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
