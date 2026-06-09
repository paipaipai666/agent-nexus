"""CLI commands for the hybrid Wiki + RAG system."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from . import console, wiki_app

review_app = typer.Typer(help="Review queue management")
wiki_app.add_typer(review_app, name="review")


def _get_wiki_service():
    from agentnexus.wiki.wiki_service import WikiService
    return WikiService()


# ── wiki init ───────────────────────────────────────────────────────

@wiki_app.command("init")
def wiki_init(
    namespace: str = typer.Argument(..., help="RAG namespace to bind wiki to"),
):
    """Initialize wiki for a RAG namespace."""
    from agentnexus.rag.store import get_knowledge_base_catalog
    from agentnexus.wiki.store import get_wiki_store

    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(namespace)
    if not kb:
        console.print(f"[red]RAG namespace '{namespace}' not found. Add documents first with `nexus kb add`.[/red]")
        raise typer.Exit(1)

    store = get_wiki_store()
    stats = store.get_stats(namespace)
    console.print(f"[green]Wiki initialized for namespace '{namespace}'[/green]")
    console.print(f"  Existing pages: {stats['page_count']}")
    console.print(f"  Existing statements: {stats['statement_count']}")


# ── wiki ingest ─────────────────────────────────────────────────────

@wiki_app.command("ingest")
def wiki_ingest(
    source: str = typer.Argument(..., help="Path to source document"),
    namespace: str = typer.Option("default", "--namespace", "-n", help="RAG namespace"),
    page_type: str = typer.Option("concept", "--type", "-t", help="Page type: entity|concept|overview|source_summary"),
):
    """Ingest a source document into the wiki."""
    source_path = Path(source)
    if not source_path.exists():
        console.print(f"[red]Source file not found: {source}[/red]")
        raise typer.Exit(1)

    text = source_path.read_text(encoding="utf-8")
    service = _get_wiki_service()

    console.print(f"Ingesting [cyan]{source}[/cyan] into wiki...")
    page = service.ingest_source(
        source_text=text,
        source_uri=str(source_path),
        source_namespace=namespace,
        page_type=page_type,
    )

    console.print(f"[green]✓ Created page '{page.title}'[/green]")
    console.print(f"  Page ID: {page.page_id}")
    console.print(f"  Statements: {len(page.statements)}")
    console.print(f"  Confidence: {page.confidence}")

    if page.canonical_definitions:
        console.print(f"  Canonical definitions: {len(page.canonical_definitions)}")


# ── wiki query ──────────────────────────────────────────────────────

@wiki_app.command("query")
def wiki_query(
    question: str = typer.Argument(..., help="Question to ask"),
    namespace: str = typer.Option("default", "--namespace", "-n", help="RAG namespace"),
    rag_fallback: bool = typer.Option(False, "--rag-fallback", "-r", help="Force RAG fallback"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
):
    """Query the wiki with confidence-based routing."""
    service = _get_wiki_service()
    result = service.query(
        question=question,
        source_namespace=namespace,
        force_rag=rag_fallback,
    )

    if result.used_wiki:
        console.print(f"[bold green]Wiki Answer[/bold green] (confidence: {result.confidence}, decision: {result.decision})")
        console.print(result.answer)

        if result.disclaimer:
            console.print(f"\n[yellow]⚠ {result.disclaimer}[/yellow]")

        if result.source_chunks:
            console.print(f"\n[dim]Source chunks: {', '.join(result.source_chunks[:5])}[/dim]")
    else:
        console.print("[bold blue]RAG Fallback[/bold blue]")
        if result.rag_results:
            for i, r in enumerate(result.rag_results[:top_k], 1):
                score = r.get("score", 0)
                text = r.get("text", "")[:200]
                console.print(f"\n[bold]{i}.[/bold] (score: {score:.3f}) {text}...")
        else:
            console.print("No results found.")


# ── wiki lint ───────────────────────────────────────────────────────

@wiki_app.command("lint")
def wiki_lint(
    namespace: str = typer.Option("default", "--namespace", "-n", help="RAG namespace"),
    enqueue: bool = typer.Option(True, "--enqueue/--no-enqueue", help="Add items to review queue"),
):
    """Run wiki health checks (consistency, drift, coverage)."""
    service = _get_wiki_service()
    items = service.run_lint(source_namespace=namespace)

    if not items:
        console.print("[green]✓ No issues found[/green]")
        return

    console.print(f"[yellow]Found {len(items)} issues:[/yellow]")
    for item in items:
        priority_label = {1: "P1", 2: "P2", 3: "P3"}.get(item["priority"], "?")
        color = {1: "red", 2: "yellow", 3: "dim"}.get(item["priority"], "white")
        console.print(f"  [{color}]{priority_label}[/{color}] {item['description'][:120]}")

    if enqueue:
        console.print("\n[dim]Items added to review queue.[/dim]")


# ── wiki review ─────────────────────────────────────────────────────

@review_app.command("list")
def review_list(
    status: str = typer.Option("pending", "--status", "-s", help="Filter by status: pending|resolved|auto_degraded"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max items to show"),
):
    """List review queue items."""
    from agentnexus.wiki.store import get_wiki_store

    store = get_wiki_store()
    items = store.list_review_items(status=status, limit=limit)

    if not items:
        console.print(f"[green]No {status} review items.[/green]")
        return

    table = Table(title=f"Review Queue ({status})")
    table.add_column("ID", style="dim")
    table.add_column("Priority", justify="center")
    table.add_column("Page")
    table.add_column("Description", max_width=60)
    table.add_column("Deadline")

    for item in items:
        priority_label = {1: "P1", 2: "P2", 3: "P3"}.get(item.priority, "?")
        priority_color = {1: "red", 2: "yellow", 3: "dim"}.get(item.priority, "white")
        table.add_row(
            item.item_id,
            f"[{priority_color}]{priority_label}[/{priority_color}]",
            item.page_id or "-",
            item.description[:60] + ("..." if len(item.description) > 60 else ""),
            item.deadline[:10] if item.deadline else "-",
        )

    console.print(table)


@review_app.command("resolve")
def review_resolve(
    item_id: str = typer.Argument(..., help="Review item ID to resolve"),
):
    """Resolve a review item."""
    from agentnexus.wiki.store import get_wiki_store

    store = get_wiki_store()
    store.resolve_review_item(item_id)
    console.print(f"[green]✓ Resolved {item_id}[/green]")


@review_app.command("process")
def review_process():
    """Process overdue review items (auto-degradation)."""
    service = _get_wiki_service()
    actions = service.process_overdue_reviews()

    if not actions:
        console.print("[green]No overdue items.[/green]")
        return

    console.print(f"[yellow]Processed {len(actions)} overdue items:[/yellow]")
    for action in actions:
        console.print(f"  {action['action']}: {action.get('item_id', '?')}")


# ── wiki stats ──────────────────────────────────────────────────────

@wiki_app.command("stats")
def wiki_stats(
    namespace: str = typer.Option("default", "--namespace", "-n", help="RAG namespace"),
):
    """Show wiki health statistics."""
    service = _get_wiki_service()
    stats = service.get_stats(namespace)

    console.print("[bold]Wiki Statistics[/bold]")
    console.print(f"  Pages: {stats['page_count']}")
    console.print(f"  Statements: {stats['statement_count']}")
    console.print(f"  Pending reviews: {stats['pending_reviews']}")
    console.print(f"  Calibration needed: {'[yellow]Yes[/yellow]' if stats['calibration_needed'] else '[green]No[/green]'}")

    if stats["confidence_distribution"]:
        console.print("\n  Confidence distribution:")
        for level, count in sorted(stats["confidence_distribution"].items()):
            color = {"high": "green", "medium": "yellow", "low": "red", "untrusted": "red bold"}.get(level, "white")
            console.print(f"    [{color}]{level}[/{color}]: {count}")


# ── wiki calibrate ──────────────────────────────────────────────────

@wiki_app.command("calibrate")
def wiki_calibrate(
    sample_file: str = typer.Argument(..., help="Path to calibration samples JSON file"),
):
    """Run threshold calibration with human-labeled samples.

    Sample file format:
    [
        {
            "statement_id": "stmt_001",
            "text": "Statement text...",
            "source_chunk_ids": ["chunk_001"],
            "source_texts": ["Source chunk text..."],
            "human_label": "direct_quote"
        },
        ...
    ]
    """
    from agentnexus.wiki.calibration import CalibrationSample

    path = Path(sample_file)
    if not path.exists():
        console.print(f"[red]Sample file not found: {sample_file}[/red]")
        raise typer.Exit(1)

    raw = json.loads(path.read_text(encoding="utf-8"))
    samples = [
        CalibrationSample(
            statement_id=s["statement_id"],
            text=s["text"],
            source_chunk_ids=s["source_chunk_ids"],
            source_texts=s["source_texts"],
            human_label=s["human_label"],
        )
        for s in raw
    ]

    console.print(f"Running calibration with {len(samples)} samples...")
    service = _get_wiki_service()
    result = service.calibrate(samples)

    console.print("[green]✓ Calibration complete[/green]")
    console.print(f"  Thresholds: {json.dumps(result['thresholds'], indent=2)}")
    console.print(f"  False degradation rate: {result['confusion_matrix']['false_degradation_rate']:.2%}")
    console.print(f"  Miss rate: {result['confusion_matrix']['miss_rate']:.2%}")
    console.print(f"  Rounds: {result['rounds']}")


# ── wiki full-check ─────────────────────────────────────────────────

@wiki_app.command("full-check")
def wiki_full_check(
    namespace: str = typer.Option("default", "--namespace", "-n", help="RAG namespace"),
):
    """Run full wiki health check (stats + lint)."""
    wiki_stats(namespace)
    console.print()
    wiki_lint(namespace, enqueue=True)
