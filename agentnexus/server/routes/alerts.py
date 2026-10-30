"""Alerts API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
def list_alerts(days: int = 7, severity: str | None = None):
    from agentnexus.core.modules import get_module_host

    manager = get_module_host().require("observability").alert_manager
    alerts = manager.get_history(days=days, severity=severity)
    return {
        "alerts": [
            {
                "alert_type": a.alert_type.value,
                "severity": a.severity.value,
                "message": a.message,
                "details": a.details,
                "timestamp": a.timestamp,
                "trace_id": a.trace_id,
            }
            for a in alerts
        ],
        "total": len(alerts),
    }


@router.get("/alerts/rules")
def list_alert_rules():
    from agentnexus.core.modules import get_module_host

    manager = get_module_host().require("observability").alert_manager
    return {
        "rules": [
            {"type": type(r).__name__, "index": i}
            for i, r in enumerate(manager.rules)
        ],
        "total": len(manager.rules),
    }
