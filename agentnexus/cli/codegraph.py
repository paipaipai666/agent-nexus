"""CLI commands for the code knowledge graph.

Provides nexus codegraph subcommands for building, searching,
querying, and managing the code graph.
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import codegraph_app, console


@codegraph_app.command()
def build(
    force: bool = typer.Option(False, "--force", "-f", help="Force full rebuild"),
    path: str = typer.Option(".", "--path", "-p", help="Project path"),
):
    """Build or update the code knowledge graph."""
    from agentnexus.codegraph.updater import build_graph

    try:
        result = build_graph(project_path=Path(path), force=force)
        console.print("[green]Graph built successfully[/green]")
        console.print(result.summary)
    except Exception as e:
        console.print(f"[red]Build failed: {e}[/red]")
        raise typer.Exit(1)


@codegraph_app.command()
def search(
    query: str = typer.Argument(help="Search query"),
    kind: str = typer.Option(None, "--kind", "-k", help="Filter by node type"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
):
    """Search code entities by semantics."""
    from agentnexus.codegraph.queries import search_entities

    results = search_entities(query, kind=kind, limit=limit)
    if not results:
        console.print("[dim]No matching code entities found[/dim]")
        return

    for r in results:
        console.print(f"[bold]{r.name}[/bold] ({r.kind}) - {r.file_path}:{r.start_line}")
        if r.signature:
            console.print(f"  [cyan]{r.signature}[/cyan]")
        if r.docstring:
            console.print(f"  [dim]{r.docstring[:100]}[/dim]")


@codegraph_app.command()
def callers(
    symbol: str = typer.Argument(help="Entity name"),
    depth: int = typer.Option(2, "--depth", "-d", help="Recursion depth"),
):
    """Find callers of a given entity."""
    from agentnexus.codegraph.queries import get_callers

    results = get_callers(symbol, depth=depth)
    if not results:
        console.print(f"[dim]No callers found for {symbol}[/dim]")
        return

    console.print(f"[bold]Callers of {symbol}:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def callees(
    symbol: str = typer.Argument(help="Entity name"),
    depth: int = typer.Option(2, "--depth", "-d", help="Recursion depth"),
):
    """Find callees of a given entity."""
    from agentnexus.codegraph.queries import get_callees

    results = get_callees(symbol, depth=depth)
    if not results:
        console.print(f"[dim]{symbol} does not call any entities[/dim]")
        return

    console.print(f"[bold]Entities called by {symbol}:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def inherits(
    cls: str = typer.Argument(help="Class name"),
):
    """View inheritance tree."""
    from agentnexus.codegraph.queries import get_inheritance_tree

    results = get_inheritance_tree(cls)
    if not results:
        console.print(f"[dim]No inheritance found for {cls}[/dim]")
        return

    console.print(f"[bold]Inheritance tree of {cls}:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def imports(
    module: str = typer.Argument(help="Module path"),
):
    """View import relationships."""
    from agentnexus.codegraph.queries import get_imports

    results = get_imports(module)
    if not results:
        console.print(f"[dim]No imports found for {module}[/dim]")
        return

    console.print(f"[bold]Imports of {module}:[/bold]")
    for r in results:
        console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def context(
    symbol: str = typer.Argument(help="Entity name"),
):
    """Get full context for an entity."""
    from agentnexus.codegraph.queries import get_entity_context

    ctx = get_entity_context(symbol)
    if not ctx:
        console.print(f"[red]Entity not found: {symbol}[/red]")
        raise typer.Exit(1)

    entity = ctx["entity"]
    console.print(f"[bold]{entity.name}[/bold] ({entity.kind})")
    console.print(f"  Qualified name: {entity.qualified_name}")
    console.print(f"  File: {entity.file_path}:{entity.start_line}")
    if entity.signature:
        console.print(f"  Signature: [cyan]{entity.signature}[/cyan]")
    if entity.docstring:
        console.print(f"  Docs: [dim]{entity.docstring[:200]}[/dim]")

    if ctx["callers"]:
        console.print(f"\n[bold]Callers ({len(ctx['callers'])}):[/bold]")
        for r in ctx["callers"][:5]:
            console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")

    if ctx["callees"]:
        console.print(f"\n[bold]Callees ({len(ctx['callees'])}):[/bold]")
        for r in ctx["callees"][:5]:
            console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def stats():
    """Show code graph statistics."""
    from agentnexus.codegraph.store import CodeGraphStore, detect_project_root, get_db_path

    project_root = detect_project_root()
    db_path = get_db_path(project_root)

    if not db_path.exists():
        console.print("[yellow]Graph not built. Run: nexus codegraph build[/yellow]")
        return

    store = CodeGraphStore(db_path)
    store.init_schema()
    try:
        s = store.get_stats()
        console.print("[bold]Code Graph Statistics[/bold]")
        console.print(f"  Total nodes: {s['node_count']}")
        console.print(f"  Total edges: {s['edge_count']}")
        console.print(f"  Total files: {s['file_count']}")

        if s["node_kinds"]:
            console.print("\n[bold]Node type distribution:[/bold]")
            for kind, count in sorted(s["node_kinds"].items()):
                console.print(f"  {kind}: {count}")

        if s["edge_kinds"]:
            console.print("\n[bold]Edge type distribution:[/bold]")
            for kind, count in sorted(s["edge_kinds"].items()):
                console.print(f"  {kind}: {count}")
    finally:
        store.close()


@codegraph_app.command()
def verify(
    fix: bool = typer.Option(False, "--fix", help="Auto-fix inconsistencies"),
):
    """Run consistency diagnostics."""
    from agentnexus.codegraph.updater import verify_consistency

    issues = verify_consistency()
    if not issues:
        console.print("[green]Consistency check passed[/green]")
    else:
        for issue in issues:
            console.print(f"[yellow]⚠ {issue}[/yellow]")
        if fix:
            console.print("[dim]Auto-fix requires: nexus codegraph build --force[/dim]")


__all__ = ["codegraph_app"]
