"""FastAPI application for AgentNexus HTTP/WebSocket server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_current_runtime: Any = None


def set_runtime(rt: Any) -> None:
    global _current_runtime
    _current_runtime = rt


def _get_runtime() -> Any:
    if _current_runtime is None:
        raise RuntimeError("Server runtime not initialized")
    return _current_runtime


def _mark_stale_ingestion_runs() -> None:
    """Mark interrupted ingestion runs as failed on server startup."""
    import logging
    import time

    logger = logging.getLogger(__name__)

    try:
        from agentnexus.rag.store import get_knowledge_base_catalog

        catalog = get_knowledge_base_catalog()
        runs = catalog.list_ingestion_runs()

        stale_count = 0
        for run in runs:
            if run.status == "running":
                # This run was interrupted by a server restart
                run.status = "failed"
                run.error_message = "Interrupted by server restart"
                run.metadata = {
                    **run.metadata,
                    "progress_stage": "failed",
                    "progress_pct": 0,
                    "progress_message": "Interrupted by server restart",
                }
                run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                catalog.upsert_ingestion_run(run)
                stale_count += 1

        if stale_count > 0:
            logger.info("Marked %d interrupted ingestion run(s) as failed", stale_count)
    except Exception as e:
        logger.warning("Failed to mark stale ingestion runs: %s", e)


def create_app(runtime: Any | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from agentnexus.app.runtime import AppRuntime

        if runtime is not None:
            set_runtime(runtime)
        else:
            set_runtime(AppRuntime.build(profile="server"))

        # Mark any interrupted ingestion runs as failed on startup
        _mark_stale_ingestion_runs()

        yield
        _current_runtime.close()

    app = FastAPI(
        title="AgentNexus API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from agentnexus.server.error_handlers import register_error_handlers
    register_error_handlers(app)

    from agentnexus.server.routes.alerts import router as alerts_router
    from agentnexus.server.routes.audit import router as audit_router
    from agentnexus.server.routes.chat import router as chat_router
    from agentnexus.server.routes.codegraph import router as codegraph_router
    from agentnexus.server.routes.config import router as config_router
    from agentnexus.server.routes.eval_routes import router as eval_router
    from agentnexus.server.routes.knowledge import router as knowledge_router
    from agentnexus.server.routes.mcp import router as mcp_router
    from agentnexus.server.routes.memory import router as memory_router
    from agentnexus.server.routes.runtime import router as runtime_router
    from agentnexus.server.routes.skills import router as skills_router
    from agentnexus.server.routes.stats import router as stats_router
    from agentnexus.server.routes.version import router as version_router
    from agentnexus.server.routes.wiki import router as wiki_router

    app.include_router(chat_router, prefix="/api")
    app.include_router(knowledge_router, prefix="/api/kb")
    app.include_router(memory_router, prefix="/api/memory")
    app.include_router(skills_router, prefix="/api/skills")
    app.include_router(stats_router, prefix="/api")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(audit_router, prefix="/api/audit")
    app.include_router(codegraph_router, prefix="/api/codegraph")
    app.include_router(eval_router, prefix="/api/eval")
    app.include_router(mcp_router, prefix="/api/mcp")
    app.include_router(version_router, prefix="/api/version")
    app.include_router(runtime_router, prefix="/api/runtime")
    app.include_router(alerts_router, prefix="/api")
    app.include_router(wiki_router, prefix="/api/wiki")

    @app.get("/health")
    def health():
        try:
            from agentnexus.server.health_checks import run_health_checks
            rt = _get_runtime()
            result = run_health_checks(rt)
            status_code = 200 if result["status"] == "ok" else 503
            from fastapi.responses import JSONResponse
            return JSONResponse(content=result, status_code=status_code)
        except Exception:
            return {"status": "ok"}

    return app
