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
    force: bool = typer.Option(False, "--force", "-f", help="全量重建"),
    path: str = typer.Option(".", "--path", "-p", help="项目路径"),
):
    """构建/更新代码图谱"""
    from agentnexus.codegraph.updater import build_graph

    try:
        result = build_graph(project_path=Path(path), force=force)
        console.print("[green]图谱构建完成[/green]")
        console.print(result.summary)
    except Exception as e:
        console.print(f"[red]构建失败: {e}[/red]")
        raise typer.Exit(1)


@codegraph_app.command()
def search(
    query: str = typer.Argument(help="搜索词"),
    kind: str = typer.Option(None, "--kind", "-k", help="节点类型过滤"),
    limit: int = typer.Option(10, "--limit", "-l", help="返回条数"),
):
    """语义搜索代码实体"""
    from agentnexus.codegraph.queries import search_entities

    results = search_entities(query, kind=kind, limit=limit)
    if not results:
        console.print("[dim]未找到匹配的代码实体[/dim]")
        return

    for r in results:
        console.print(f"[bold]{r.name}[/bold] ({r.kind}) - {r.file_path}:{r.start_line}")
        if r.signature:
            console.print(f"  [cyan]{r.signature}[/cyan]")
        if r.docstring:
            console.print(f"  [dim]{r.docstring[:100]}[/dim]")


@codegraph_app.command()
def callers(
    symbol: str = typer.Argument(help="实体名"),
    depth: int = typer.Option(2, "--depth", "-d", help="递归深度"),
):
    """查找谁调用了指定实体"""
    from agentnexus.codegraph.queries import get_callers

    results = get_callers(symbol, depth=depth)
    if not results:
        console.print(f"[dim]未找到调用 {symbol} 的实体[/dim]")
        return

    console.print(f"[bold]调用 {symbol} 的实体:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def callees(
    symbol: str = typer.Argument(help="实体名"),
    depth: int = typer.Option(2, "--depth", "-d", help="递归深度"),
):
    """查找指定实体调用了谁"""
    from agentnexus.codegraph.queries import get_callees

    results = get_callees(symbol, depth=depth)
    if not results:
        console.print(f"[dim]{symbol} 未调用任何实体[/dim]")
        return

    console.print(f"[bold]{symbol} 调用的实体:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def inherits(
    cls: str = typer.Argument(help="类名"),
):
    """查看继承树"""
    from agentnexus.codegraph.queries import get_inheritance_tree

    results = get_inheritance_tree(cls)
    if not results:
        console.print(f"[dim]未找到 {cls} 的继承关系[/dim]")
        return

    console.print(f"[bold]{cls} 的继承树:[/bold]")
    for r in results:
        console.print(f"  {r.kind}: {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def imports(
    module: str = typer.Argument(help="模块路径"),
):
    """查看导入关系"""
    from agentnexus.codegraph.queries import get_imports

    results = get_imports(module)
    if not results:
        console.print(f"[dim]未找到 {module} 的导入关系[/dim]")
        return

    console.print(f"[bold]{module} 的导入:[/bold]")
    for r in results:
        console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def context(
    symbol: str = typer.Argument(help="实体名"),
):
    """获取实体完整上下文"""
    from agentnexus.codegraph.queries import get_entity_context

    ctx = get_entity_context(symbol)
    if not ctx:
        console.print(f"[red]未找到实体: {symbol}[/red]")
        raise typer.Exit(1)

    entity = ctx["entity"]
    console.print(f"[bold]{entity.name}[/bold] ({entity.kind})")
    console.print(f"  完整名称: {entity.qualified_name}")
    console.print(f"  文件: {entity.file_path}:{entity.start_line}")
    if entity.signature:
        console.print(f"  签名: [cyan]{entity.signature}[/cyan]")
    if entity.docstring:
        console.print(f"  文档: [dim]{entity.docstring[:200]}[/dim]")

    if ctx["callers"]:
        console.print(f"\n[bold]调用者 ({len(ctx['callers'])}):[/bold]")
        for r in ctx["callers"][:5]:
            console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")

    if ctx["callees"]:
        console.print(f"\n[bold]被调用者 ({len(ctx['callees'])}):[/bold]")
        for r in ctx["callees"][:5]:
            console.print(f"  {r.name} [{r.file_path}:{r.start_line}]")


@codegraph_app.command()
def stats():
    """显示图谱统计信息"""
    from agentnexus.codegraph.store import CodeGraphStore, detect_project_root, get_db_path

    project_root = detect_project_root()
    db_path = get_db_path(project_root)

    if not db_path.exists():
        console.print("[yellow]图谱未构建，请先运行 nexus codegraph build[/yellow]")
        return

    store = CodeGraphStore(db_path)
    store.init_schema()
    try:
        s = store.get_stats()
        console.print("[bold]代码图谱统计[/bold]")
        console.print(f"  节点总数: {s['node_count']}")
        console.print(f"  边总数: {s['edge_count']}")
        console.print(f"  文件总数: {s['file_count']}")

        if s["node_kinds"]:
            console.print("\n[bold]节点类型分布:[/bold]")
            for kind, count in sorted(s["node_kinds"].items()):
                console.print(f"  {kind}: {count}")

        if s["edge_kinds"]:
            console.print("\n[bold]边类型分布:[/bold]")
            for kind, count in sorted(s["edge_kinds"].items()):
                console.print(f"  {kind}: {count}")
    finally:
        store.close()


@codegraph_app.command()
def verify(
    fix: bool = typer.Option(False, "--fix", help="自动修复不一致"),
):
    """一致性诊断"""
    from agentnexus.codegraph.updater import verify_consistency

    issues = verify_consistency()
    if not issues:
        console.print("[green]一致性检查通过[/green]")
    else:
        for issue in issues:
            console.print(f"[yellow]⚠ {issue}[/yellow]")
        if fix:
            console.print("[dim]自动修复需要运行 nexus codegraph build --force[/dim]")


__all__ = ["codegraph_app"]
