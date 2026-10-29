"""命令钩子管理：列出、信任审查、干跑测试。"""

import typer
from rich.table import Table

from . import console, hooks_app


@hooks_app.command("list")
def list_hooks_cmd():
    """列出所有已发现的命令钩子（含信任状态）。"""
    from agentnexus.core.hook_sources import (
        discover_hooks,
        load_trusted_fingerprints,
    )

    loaded, errors = discover_hooks(include_untrusted=True)
    trusted_store = load_trusted_fingerprints()

    table = Table(title="Command Hooks", show_lines=False)
    table.add_column("Source", style="cyan")
    table.add_column("Event", style="green")
    table.add_column("Matcher")
    table.add_column("Trust")
    table.add_column("Timeout")
    table.add_column("Command", overflow="fold")
    table.add_column("Fingerprint", style="dim")
    for item in loaded:
        trust = (
            "[green]trusted[/green]" if item.trusted
            else "[red]UNTRUSTED[/red]"
        )
        if item.source == "project" and item.fingerprint in trusted_store:
            trust += " (pinned)"
        table.add_row(
            item.source,
            item.config.event,
            item.config.matcher or "-",
            trust,
            f"{item.config.timeout:g}s",
            item.config.command,
            item.fingerprint[:12],
        )
    console.print(table)
    for message in errors:
        console.print(f"[yellow]warning:[/yellow] {message}")


@hooks_app.command("trust")
def trust_cmd(
    fingerprint: str = typer.Argument(..., help="钩子指纹（nexus hooks list 查看）"),
    action: str = typer.Argument("approve", help="approve | revoke"),
):
    """信任/撤销项目级命令钩子。"""
    from agentnexus.core.hook_sources import (
        approve_fingerprint,
        revoke_fingerprint,
    )

    if action == "approve":
        approve_fingerprint(fingerprint)
        console.print(f"[green]approved[/green] {fingerprint}")
    elif action == "revoke":
        revoke_fingerprint(fingerprint)
        console.print(f"[yellow]revoked[/yellow] {fingerprint}")
    else:
        console.print(f"[red]unknown action {action!r}[/red] (expected approve|revoke)")
        raise typer.Exit(code=2)


@hooks_app.command("test")
def test_cmd(
    event: str = typer.Argument(..., help="事件名，如 before_tool_call"),
    tool: str = typer.Option("", "--tool", "-t", help="模拟的工具名（工具事件用）"),
):
    """干跑：用示例 payload 触发某事件的命令钩子，观察阻断/输出。"""
    from agentnexus.core.hook_executor import run_command_hooks_for
    from agentnexus.core.hooks import HookContext, HookType

    try:
        hook_type = HookType(event)
    except ValueError:
        valid = ", ".join(h.value for h in HookType)
        console.print(f"[red]unknown event {event!r}[/red]; valid: {valid}")
        raise typer.Exit(code=2)

    payload: dict = {"session_id": "cli-test"}
    if "tool" in event:
        payload["name"] = tool or "example_tool"
        payload["params"] = {}
    import time

    ctx = HookContext(hook_type, payload)
    started = time.perf_counter()
    run_command_hooks_for(ctx)
    ctx.elapsed_ms = (time.perf_counter() - started) * 1000

    console.print(f"elapsed: {ctx.elapsed_ms:.1f}ms")
    if ctx.aborted:
        console.print(f"[red]BLOCKED[/red] {ctx.abort_code}: {ctx.abort_reason}")
        raise typer.Exit(code=2)
    console.print("[green]ok[/green] (not blocked)")
