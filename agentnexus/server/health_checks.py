"""健康检查子系统 — 增强的 readiness probe

检查各子系统的健康状态：LLM、MCP、Memory、Traces 目录、磁盘空间。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def check_llm_health(runtime: Any) -> dict[str, Any]:
    """检查 LLM 连通性"""
    try:
        llm = runtime.llm
        if not llm.api_key:
            return {"status": "degraded", "detail": "API key not configured"}
        if not llm.base_url:
            return {"status": "degraded", "detail": "Base URL not configured"}
        return {"status": "ok", "model": llm.model}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def check_mcp_health(runtime: Any) -> dict[str, Any]:
    """聚合所有 MCP 服务器健康状态"""
    try:
        mcp = runtime.mcp_manager
        if mcp is None:
            return {"status": "ok", "detail": "MCP not enabled"}

        servers = getattr(mcp, "_servers", {})
        if not servers:
            return {"status": "ok", "detail": "No MCP servers configured"}

        healthy = 0
        degraded = 0
        failed = 0
        for name, server in servers.items():
            state = getattr(server, "state", None)
            state_str = str(state).lower() if state else "unknown"
            if "healthy" in state_str:
                healthy += 1
            elif "degraded" in state_str:
                degraded += 1
            else:
                failed += 1

        total = healthy + degraded + failed
        if failed == total and total > 0:
            status = "error"
        elif degraded > 0 or failed > 0:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "failed": failed,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def check_memory_health(runtime: Any) -> dict[str, Any]:
    """检查 memory DB 可访问性"""
    try:
        mm = runtime.memory_manager
        if mm is None:
            return {"status": "ok", "detail": "Memory manager not initialized"}
        # 简单检查：尝试获取记忆数量
        count = len(mm) if hasattr(mm, "__len__") else "unknown"
        return {"status": "ok", "memory_count": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def check_traces_dir(runtime: Any) -> dict[str, Any]:
    """检查 traces 目录可写"""
    try:
        from agentnexus.core.config import get_settings
        traces_dir = Path(get_settings().traces_dir)
        if not traces_dir.exists():
            traces_dir.mkdir(parents=True, exist_ok=True)
        # 测试写入
        test_file = traces_dir / ".health_check"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return {"status": "ok", "path": str(traces_dir)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def check_disk_space() -> dict[str, Any]:
    """检查磁盘空间"""
    try:
        import shutil

        from agentnexus.core.config import _config_dir
        usage = shutil.disk_usage(str(_config_dir()))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = (usage.used / usage.total) * 100

        status = "ok"
        if free_gb < 1:
            status = "error"
        elif free_gb < 5:
            status = "degraded"

        return {
            "status": status,
            "free_gb": round(free_gb, 1),
            "total_gb": round(total_gb, 1),
            "used_pct": round(used_pct, 1),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def run_health_checks(runtime: Any) -> dict[str, Any]:
    """运行所有健康检查，返回聚合结果"""
    start_time = getattr(runtime, "_start_time", time.time())
    checks = {
        "llm": check_llm_health(runtime),
        "mcp": check_mcp_health(runtime),
        "memory": check_memory_health(runtime),
        "traces_dir": check_traces_dir(runtime),
        "disk_space": check_disk_space(),
    }

    overall = all(c.get("status") == "ok" for c in checks.values())
    return {
        "status": "ok" if overall else "degraded",
        "checks": checks,
        "uptime_seconds": round(time.time() - start_time, 1),
        "timestamp": time.time(),
    }
