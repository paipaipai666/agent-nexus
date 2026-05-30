"""CLI config and init commands"""
import os

import typer
from pydantic import SecretStr
from rich import box
from rich.panel import Panel
from rich.table import Table

from . import app, console


@app.command()
def config(
    set_key: str = typer.Argument(None, help="Config key name (nexus config <key> <value>)"),
    set_value: str = typer.Argument(None, help="Config value"),
    key: str = typer.Option(None, "--set", "-s", help="Set a config key (legacy)"),
    value: str = typer.Option(None, "--value", "-v", help="Config value (legacy)"),
):
    """View or modify configuration."""
    from agentnexus.core.config import Settings, get_config_dir, get_settings, load_config_yaml, write_config_yaml

    settings = get_settings()
    config_path = get_config_dir() / "config.yaml"

    SETTABLE_KEYS = [
        "llm_api_key", "llm_model_id", "llm_base_url", "llm_timeout",
        "tavily_api_key", "e2b_api_key", "max_agent_steps",
        "code_execution_backend",
        "code_execution_timeout",
        "code_execution_memory_mb",
        "code_execution_docker_image",
        "code_execution_allow_unsafe_local",
        "shell_execution_backend",
        "shell_execution_memory_mb",
        "shell_execution_docker_image",
        "enable_contextual_retrieval",
        "default_skill",
        "skill_auto_route",
        "skill_auto_route_llm_fallback",
        "skill_auto_route_min_score",
        "skill_auto_route_margin",
    ]

    # Support both positional args (nexus config <key> <value>) and legacy flags (--set/--value)
    if set_key is not None and key is None:
        key = set_key
        value = set_value

    if key is not None:
        # ── Set mode ──
        if key not in SETTABLE_KEYS:
            console.print(f"[red]Invalid config key: {key}[/red]")
            console.print(f"Available keys: [dim]{', '.join(SETTABLE_KEYS)}[/dim]")
            return
        if value is None:
            console.print("[yellow]Please provide a value with --value / -v[/yellow]")
            console.print("Example: nexus config --set llm_model_id --value deepseek/deepseek-chat")
            return

        data = load_config_yaml()
        data[key] = value
        write_config_yaml(data)
        console.print(f"[green]Saved[/green] {key} = [bold]{value}[/bold]")
        console.print(f"[dim]Config file: {config_path}[/dim]")
        return

    # ── View mode ──
    table = Table(title="AgentNexus Configuration", box=box.ROUNDED)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Source", style="dim")

    yaml_data = load_config_yaml()

    for name, field in Settings.model_fields.items():
        resolved = getattr(settings, name)
        env_var = f"AGENTNEXUS_{name.upper()}"
        if env_var in os.environ:
            source = "env"
        elif name in yaml_data:
            source = "config.yaml"
        else:
            source = "default"

        if isinstance(resolved, SecretStr):
            raw = resolved.get_secret_value()
            if raw:
                display = f"{raw[:3]}****{raw[-4:]}" if len(raw) > 7 else "****"
            else:
                display = "(not set)"
        else:
            display = str(resolved) if resolved != "" else "(not set)"

        table.add_row(name, display, source)

    console.print(table)
    console.print(f"[dim]Config file: {config_path}[/dim]")


@app.command()
def init():
    """First-time initialization wizard."""
    from agentnexus.core.config import get_config_dir, load_config_yaml, write_config_yaml

    console.print(Panel("[bold]AgentNexus Setup Wizard[/bold]", border_style="cyan"))
    console.print()

    api_key = input("LLM API Key (required): ").strip()
    while not api_key:
        console.print("[yellow]API Key cannot be empty[/yellow]")
        api_key = input("LLM API Key (required): ").strip()

    model = input("LLM Model [deepseek/deepseek-v4-flash]: ").strip()
    if not model:
        model = "deepseek/deepseek-v4-flash"

    base_url = input("LLM Base URL [https://api.deepseek.com]: ").strip()
    if not base_url:
        base_url = "https://api.deepseek.com"

    config_path = get_config_dir() / "config.yaml"

    data = load_config_yaml()
    data["llm_api_key"] = api_key
    data["llm_model_id"] = model
    data["llm_base_url"] = base_url
    write_config_yaml(data)

    console.print()
    console.print("[green]Configuration complete![/green] Try: nexus run 'hello'")
    console.print(f"[dim]Config file: {config_path}[/dim]")
