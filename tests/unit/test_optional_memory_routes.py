"""Optional memory HTTP behavior keeps durable history independent."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.server.deps import get_runtime
from agentnexus.server.routes.memory import router


def test_disabled_retention_has_an_explicit_unavailable_response():
    from agentnexus.server.error_handlers import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/memory")
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(memory_manager=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        for response in (client.get("/memory/short"), client.post("/memory/short/clear")):
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "module_unavailable"
