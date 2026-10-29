"""Runtime statistics, health, trace, alert and audit commands."""

from dataclasses import asdict, is_dataclass

from . import COMMAND_HELP


def run(context, command, args, sid):
    """Read runtime evidence using the existing domain providers."""
    from agentnexus.core.config import get_settings
    from agentnexus.services import inspection

    if command == "/stats":
        from agentnexus.observability.alerting import evaluate_stats
        from agentnexus.observability.stats import compute_stats

        days = int(args or 7)
        if not 1 <= days <= 3650:
            raise ValueError("天数必须在 1 到 3650 之间")
        stats = compute_stats(get_settings().traces_dir, days)
        evaluate_stats(stats)
        return asdict(stats) if is_dataclass(stats) else stats
    if command == "/logs":
        return inspection.list_logs(int(args or 7))
    if command == "/trace" and args:
        return inspection.get_trace(args)
    if command == "/health":
        from agentnexus.server.health_checks import run_health_checks

        return run_health_checks(context.runtime)
    if command == "/audit":
        parts = args.split()
        return inspection.list_audit_entries(tool=parts[0] if parts else None, limit=int(parts[1]) if len(parts) > 1 else 50)
    if command == "/alerts":
        from agentnexus.observability.alerting import get_alert_manager

        manager = get_alert_manager()
        if args == "rules":
            return [{"type": type(rule).__name__, "index": index} for index, rule in enumerate(manager._rules)]
        parts = args.split()
        days = int(parts[0]) if parts else 7
        if not 1 <= days <= 3650:
            raise ValueError("天数必须在 1 到 3650 之间")
        return [asdict(alert) for alert in manager.get_history(days=days, severity=parts[1] if len(parts) > 1 else None)]
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
