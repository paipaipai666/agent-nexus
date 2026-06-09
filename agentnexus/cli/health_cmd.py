"""CLI health command — 运行健康检查"""

import logging

logger = logging.getLogger(__name__)

from . import app, console


@app.command()
def health():
    """Run system health checks."""
    from rich.panel import Panel

    from agentnexus.core.config import get_settings
    from agentnexus.server.health_checks import run_health_checks

    try:
        settings = get_settings()
        # 构建轻量 runtime 用于健康检查
        from agentnexus.core.llm import AgentLLM

        # 构造一个最小 runtime 对象给 health_checks 使用
        class _MiniRuntime:
            def __init__(self):
                self.llm = AgentLLM()
                self.mcp_manager = None
                self.memory_manager = None

        rt = _MiniRuntime()
        result = run_health_checks(rt)

        status = result["status"]
        status_style = "green" if status == "ok" else "red"
        lines = [f"[bold {status_style}]Overall: {status.upper()}[/bold {status_style}]"]
        lines.append("")

        checks = result.get("checks", {})
        for name, check in checks.items():
            s = check.get("status", "unknown")
            icon = "✅" if s == "ok" else "⚠️" if s == "degraded" else "❌"
            detail = check.get("detail", "")
            model = check.get("model", "")
            extra = f" ({model})" if model else ""
            lines.append(f"  {icon} {name}: {s}{extra}")
            if detail:
                lines.append(f"     {detail}")
            # 磁盘空间特殊展示
            if "free_gb" in check:
                lines.append(f"     Free: {check['free_gb']}GB / {check['total_gb']}GB ({check['used_pct']}% used)")

        uptime = result.get("uptime_seconds", 0)
        if uptime > 0:
            lines.append("")
            lines.append(f"[dim]Uptime: {uptime:.0f}s[/dim]")

        console.print(Panel(
            "\n".join(lines),
            title="System Health",
            border_style=status_style,
        ))

    except Exception as e:
        console.print(f"[red]Health check failed: {e}[/red]")
        raise SystemExit(1)
