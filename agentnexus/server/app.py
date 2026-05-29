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


def create_app(runtime: Any | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from agentnexus.app.runtime import AppRuntime

        if runtime is not None:
            set_runtime(runtime)
        else:
            set_runtime(AppRuntime.build(profile="server"))
        yield
        _current_runtime.close()

    app = FastAPI(
        title="AgentNexus API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from agentnexus.server.error_handlers import register_error_handlers
    register_error_handlers(app)

    from agentnexus.server.routes.audit import router as audit_router
    from agentnexus.server.routes.chat import router as chat_router
    from agentnexus.server.routes.codegraph import router as codegraph_router
    from agentnexus.server.routes.config import router as config_router
    from agentnexus.server.routes.eval_routes import router as eval_router
    from agentnexus.server.routes.knowledge import router as knowledge_router
    from agentnexus.server.routes.memory import router as memory_router
    from agentnexus.server.routes.skills import router as skills_router
    from agentnexus.server.routes.stats import router as stats_router

    app.include_router(chat_router, prefix="/api")
    app.include_router(knowledge_router, prefix="/api/kb")
    app.include_router(memory_router, prefix="/api/memory")
    app.include_router(skills_router, prefix="/api/skills")
    app.include_router(stats_router, prefix="/api")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(audit_router, prefix="/api/audit")
    app.include_router(codegraph_router, prefix="/api/codegraph")
    app.include_router(eval_router, prefix="/api/eval")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
