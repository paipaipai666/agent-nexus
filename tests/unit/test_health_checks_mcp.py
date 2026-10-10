from types import SimpleNamespace

from agentnexus.server.health_checks import check_mcp_health


def test_mcp_health_uses_connected_server_snapshot():
    manager = SimpleNamespace(
        status_snapshot=lambda: {
            "servers": [{"name": "audit", "state": "healthy", "connected": True}],
            "connected_count": 1,
        }
    )

    assert check_mcp_health(SimpleNamespace(mcp_manager=manager)) == {
        "status": "ok", "total": 1, "healthy": 1, "degraded": 0, "failed": 0,
    }


def test_mcp_health_reports_degraded_server_snapshot():
    manager = SimpleNamespace(
        status_snapshot=lambda: {
            "servers": [
                {"name": "audit", "state": "healthy", "connected": True},
                {"name": "slow", "state": "degraded", "connected": False},
            ]
        }
    )

    assert check_mcp_health(SimpleNamespace(mcp_manager=manager)) == {
        "status": "degraded", "total": 2, "healthy": 1, "degraded": 1, "failed": 0,
    }
