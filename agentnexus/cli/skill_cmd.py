"""CLI skill management commands."""

import re
from pathlib import Path

import typer
import yaml
from rich.table import Table

from . import console, skill_app

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _registry():
    from agentnexus.core.config import get_settings
    from agentnexus.skills import SkillRegistry

    registry = SkillRegistry.from_settings(get_settings())
    registry.discover()
    return registry


def _parse_target(target: str, default_namespace: str) -> tuple[str, str]:
    value = target.strip().strip("/")
    if not value:
        raise typer.BadParameter("skill name cannot be empty")
    if "/" in value:
        namespace, workflow_id = value.split("/", 1)
    else:
        namespace, workflow_id = value, value
    if not _ID_PATTERN.match(namespace) or not _ID_PATTERN.match(workflow_id):
        raise typer.BadParameter("skill ids may only contain letters, numbers, '_', '-', and '.'")
    return namespace or default_namespace, workflow_id


def _workflow_template(workflow_id: str, display_name: str) -> dict:
    return {
        "id": workflow_id,
        "version": "1",
        "display_name": display_name,
        "description": f"{display_name} workflow.",
        "entry_mode": "chat",
        "prompt_profile": {
            "system": "react",
            "fragments": [],
            "variables": {},
        },
        "tool_policy": {
            "allow": [],
            "deny": [],
            "max_risk": "high",
            "allow_subagents": False,
        },
        "memory_policy": {
            "inject_long_term": True,
            "allow_save": True,
        },
        "retrieval_policy": {
            "namespace": "default",
            "view": "section",
            "top_k": 5,
            "filters": {},
        },
        "steps": [
            {
                "type": "prompt",
                "id": "scope",
                "prompt": "Clarify the user's goal, constraints, and expected output.",
            },
            {
                "type": "finalize",
                "id": "done",
                "prompt": "Return a concise answer that satisfies the workflow success criteria.",
            },
        ],
        "success_criteria": [
            "The answer addresses the user's request directly.",
            "Important assumptions and risks are stated clearly.",
        ],
    }


def _skill_md_template(workflow_id: str, display_name: str) -> str:
    return f"""---
id: {workflow_id}
name: {display_name}
description: Custom AgentNexus skill.
version: "1"
system: react
max_risk: high
allow_subagents: false
---

# {display_name}

Describe when this skill should be used and how the agent should behave.

## Instructions

- Clarify the user's goal and constraints.
- Use available context and tools only when they materially improve the answer.
- Return a concise answer that satisfies the user's request.

## Success Criteria

- The answer addresses the request directly.
- Important assumptions and risks are stated clearly.
"""


def _resource_summary(entry) -> str:
    counts = {"script": 0, "reference": 0, "asset": 0}
    for resource in getattr(entry.workflow, "resources", []) or []:
        counts[resource.type] = counts.get(resource.type, 0) + 1
    parts = []
    if counts["script"]:
        parts.append(f"scripts={counts['script']}")
    if counts["reference"]:
        parts.append(f"references={counts['reference']}")
    if counts["asset"]:
        parts.append(f"assets={counts['asset']}")
    return ", ".join(parts) or "-"


@skill_app.command("list")
def list_skills():
    """List available skills."""
    registry = _registry()
    table = Table(title="Skills")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Description", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Resources", style="dim")
    for entry in registry.list():
        table.add_row(
            entry.qualified_id,
            entry.display_name,
            entry.description,
            entry.source_kind,
            _resource_summary(entry),
        )
    console.print(table)
    if registry.errors:
        console.print(f"[red]{len(registry.errors)} skills failed to load, run nexus skill validate for details[/red]")


@skill_app.command("init")
def init_skill(
    target: str = typer.Argument(..., help="Skill name, or namespace/skill_id"),
    display_name: str = typer.Option("", "--name", help="Display name (defaults to derived from skill_id)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing SKILL.md or workflow.yaml"),
    workflow: bool = typer.Option(False, "--workflow", help="Generate legacy workflow.yaml instead of generic SKILL.md"),
):
    """Create a generic SKILL.md skill template under ~/.agentnexus/skills."""
    from agentnexus.core.config import get_config_dir, get_settings

    settings = get_settings()
    namespace, workflow_id = _parse_target(target, settings.skills_default_namespace)
    name = display_name or workflow_id.replace("_", " ").replace("-", " ").title()
    path = Path(get_config_dir()) / "skills" / namespace / ("workflow.yaml" if workflow else "SKILL.md")
    if path.exists() and not force:
        console.print(f"[red]Skill already exists:[/red] {path}")
        console.print("[dim]Use --force to overwrite[/dim]")
        raise typer.Exit(code=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    if workflow:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(_workflow_template(workflow_id, name), f, allow_unicode=True, sort_keys=False)
    else:
        path.write_text(_skill_md_template(workflow_id, name), encoding="utf-8")
        for child in ("scripts", "references", "assets"):
            (path.parent / child).mkdir(exist_ok=True)
    console.print(f"[green]Skill created[/green] {namespace}/{workflow_id}")
    console.print(f"[dim]{path}[/dim]")
    console.print("[dim]Next: nexus skill validate && nexus skill use " f"{namespace}/{workflow_id}[/dim]")


@skill_app.command("validate")
def validate_skill(
    target: str = typer.Argument("", help="Optional skill_id or namespace/skill_id"),
):
    """Validate skill and prompt resources."""
    registry = _registry()
    errors = registry.validate(target or None)
    if not errors:
        console.print(f"[green]Skill validation passed[/green] ({target or 'all'})")
        return
    console.print(f"[red]Skill validation failed[/red] ({len(errors)} errors)")
    for error in errors:
        console.print(f"- {error}")
    raise typer.Exit(code=1)


@skill_app.command("use")
def use_skill(target: str = typer.Argument(..., help="skill_id or namespace/skill_id")):
    """Set the default skill for TUI sessions."""
    from agentnexus.core.config import load_config_yaml, write_config_yaml

    registry = _registry()
    try:
        entry = registry.get(target)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if entry is None:
        console.print(f"[red]Skill not found: {target}[/red]")
        raise typer.Exit(code=1)
    errors = registry.validate(entry.qualified_id)
    if errors:
        console.print("[red]Cannot set default skill, validation failed:[/red]")
        for error in errors:
            console.print(f"- {error}")
        raise typer.Exit(code=1)
    data = load_config_yaml()
    data["default_skill"] = entry.qualified_id
    write_config_yaml(data)
    console.print(f"[green]Default skill set[/green] {entry.qualified_id}")


@skill_app.command("reset")
def reset_skill():
    """Clear the default skill."""
    from agentnexus.core.config import load_config_yaml, write_config_yaml

    data = load_config_yaml()
    data.pop("default_skill", None)
    write_config_yaml(data)
    console.print("[green]Default skill cleared[/green]")


@skill_app.command("status")
def skill_status():
    """Show the default skill and discovery status."""
    from agentnexus.core.config import get_settings

    settings = get_settings()
    registry = _registry()
    default = settings.default_skill or "default/default"
    console.print(f"[bold]default:[/bold] {default}")
    console.print(f"[bold]available:[/bold] {len(registry.list())}")
    try:
        selected = registry.get(default) if default != "default/default" else None
    except ValueError as exc:
        selected = None
        registry.errors.append(str(exc))
    if selected is not None:
        console.print(f"[bold]resources:[/bold] {_resource_summary(selected)}")
    if registry.errors:
        console.print(f"[red]errors:[/red] {len(registry.errors)}")
        for error in registry.errors[:5]:
            console.print(f"- {error}")
