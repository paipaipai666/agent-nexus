"""FastAPI application for AgentNexus HTTP/WebSocket server."""

from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_current_runtime: Any = None
_request_runtime: ContextVar[Any] = ContextVar("agentnexus_server_runtime", default=None)


def set_runtime(rt: Any) -> None:
    global _current_runtime
    _current_runtime = rt


def _get_runtime() -> Any:
    current = _request_runtime.get()
    if current is not None:
        return current
    if _current_runtime is None:
        raise RuntimeError("Server runtime not initialized")
    return _current_runtime


class RuntimeContextMiddleware:
    """Bind the owning runtime for HTTP and WebSocket request lifetimes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        from agentnexus.core.modules import ModuleHost

        runtime = getattr(scope["app"].state, "runtime", None)
        if runtime is None:
            await self.app(scope, receive, send)
            return
        token = _request_runtime.set(runtime)
        modules = getattr(runtime, "modules", None)
        try:
            with modules.activate() if isinstance(modules, ModuleHost) else nullcontext():
                await self.app(scope, receive, send)
        finally:
            _request_runtime.reset(token)


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
    from agentnexus.core.modules import active_module_host

    selected_modules = active_module_host()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from agentnexus.app.runtime import AppRuntime

        rt = runtime if runtime is not None else AppRuntime.build(profile="server", modules=selected_modules)
        app.state.runtime = rt
        try:
            _mark_stale_ingestion_runs()
            yield
        finally:
            try:
                chat = getattr(rt, "chat", None)
                if chat is not None and hasattr(chat, "cancel_all_runs"):
                    chat.cancel_all_runs(reason="server_shutdown")
            finally:
                rt.close()

    app = FastAPI(
        title="AgentNexus API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(RuntimeContextMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from agentnexus.server.error_handlers import register_error_handlers
    register_error_handlers(app)

    from agentnexus.server.routes.alerts import router as alerts_router
    from agentnexus.server.routes.audit import router as audit_router
    from agentnexus.server.routes.chat import router as chat_router
    from agentnexus.server.routes.config import router as config_router
    from agentnexus.server.routes.eval_routes import router as eval_router
    from agentnexus.server.routes.hooks import router as hooks_router
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
    app.include_router(eval_router, prefix="/api/eval")
    app.include_router(mcp_router, prefix="/api/mcp")
    app.include_router(version_router, prefix="/api/version")
    app.include_router(runtime_router, prefix="/api/runtime")
    app.include_router(alerts_router, prefix="/api")
    app.include_router(wiki_router, prefix="/api/wiki")
    app.include_router(hooks_router, prefix="/api/hooks")

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
