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
    key: str = typer.Option(None, "--set", "-s", help="设置配置项"),
    value: str = typer.Option(None, "--value", "-v", help="配置值"),
):
    """查看或修改配置"""
    from agentnexus.core.config import Settings, _config_dir, _load_yaml, _write_yaml_config, get_settings

    settings = get_settings()
    config_path = _config_dir() / "config.yaml"

    SETTABLE_KEYS = [
        "llm_api_key", "llm_model_id", "llm_base_url", "llm_timeout",
        "tavily_api_key", "e2b_api_key", "max_agent_steps",
        "enable_contextual_retrieval",
    ]

    if key is not None:
        # ── Set mode ──
        if key not in SETTABLE_KEYS:
            console.print(f"[red]无效的配置项: {key}[/red]")
            console.print(f"可用配置项: [dim]{', '.join(SETTABLE_KEYS)}[/dim]")
            return
        if value is None:
            console.print("[yellow]请用 --value / -v 提供值[/yellow]")
            console.print("示例: nexus config --set llm_model_id --value deepseek/deepseek-chat")
            return

        data = _load_yaml()
        data[key] = value
        _write_yaml_config(data)
        console.print(f"[green]已保存[/green] {key} = [bold]{value}[/bold]")
        console.print(f"[dim]配置文件: {config_path}[/dim]")
        return

    # ── View mode ──
    table = Table(title="AgentNexus 配置", box=box.ROUNDED)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Source", style="dim")

    yaml_data = _load_yaml()

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
                display = "(未设置)"
        else:
            display = str(resolved) if resolved != "" else "(未设置)"

        table.add_row(name, display, source)

    console.print(table)
    console.print(f"[dim]配置文件: {config_path}[/dim]")


@app.command()
def init():
    """首次初始化引导"""
    from agentnexus.core.config import _config_dir, _load_yaml, _write_yaml_config

    console.print(Panel("[bold]AgentNexus 初始化引导[/bold]", border_style="cyan"))
    console.print()

    api_key = input("LLM API Key (必填): ").strip()
    while not api_key:
        console.print("[yellow]API Key 不能为空[/yellow]")
        api_key = input("LLM API Key (必填): ").strip()

    model = input("LLM 模型 [deepseek/deepseek-v4-flash]: ").strip()
    if not model:
        model = "deepseek/deepseek-v4-flash"

    base_url = input("LLM Base URL [https://api.deepseek.com]: ").strip()
    if not base_url:
        base_url = "https://api.deepseek.com"

    config_path = _config_dir() / "config.yaml"

    data = _load_yaml()
    data["llm_api_key"] = api_key
    data["llm_model_id"] = model
    data["llm_base_url"] = base_url
    _write_yaml_config(data)

    console.print()
    console.print("[green]配置完成![/green] 试试 nexus run '你好'")
    console.print(f"[dim]配置文件: {config_path}[/dim]")
