"""Tests for agentnexus.server.error_handlers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.server.error_handlers import APIError, register_error_handlers


class TestAPIError:
    def test_inherits_from_exception(self):
        assert issubclass(APIError, Exception)

    def test_stores_attributes(self):
        err = APIError(400, "bad_request", "invalid input")
        assert err.status_code == 400
        assert err.code == "bad_request"
        assert err.message == "invalid input"

    def test_can_be_raised(self):
        with pytest.raises(APIError):
            raise APIError(400, "bad_request", "test")

    def test_can_be_caught_as_exception(self):
        with pytest.raises(Exception):
            raise APIError(500, "internal", "test")


def _make_app_with_errors() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/api-error")
    async def raise_api_error():
        raise APIError(400, "bad_request", "invalid input")

    @app.get("/api-error-403")
    async def raise_api_error_403():
        raise APIError(403, "forbidden", "access denied")

    @app.get("/api-error-422")
    async def raise_api_error_422():
        raise APIError(422, "validation_error", "unprocessable")

    @app.get("/api-error-empty-msg")
    async def raise_api_error_empty():
        raise APIError(400, "bad_request", "")

    @app.get("/api-error-unicode")
    async def raise_api_error_unicode():
        raise APIError(400, "bad_request", "错误：输入无效")

    @app.get("/value-error")
    async def raise_value_error():
        raise ValueError("bad value")

    @app.get("/type-error")
    async def raise_type_error():
        raise TypeError("wrong type")

    @app.get("/runtime-error")
    async def raise_runtime_error():
        raise RuntimeError("something broke")

    return app


class TestAPIErrorHandler:
    @pytest.fixture()
    def client(self):
        return TestClient(_make_app_with_errors(), raise_server_exceptions=False)

    def test_api_error_400_returns_correct_json(self, client):
        resp = client.get("/api-error")
        assert resp.status_code == 400
        body = resp.json()
        assert body == {"error": {"code": "bad_request", "message": "invalid input"}}

    def test_api_error_403_returns_correct_code(self, client):
        resp = client.get("/api-error-403")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "forbidden"

    def test_api_error_422_returns_correct_status(self, client):
        resp = client.get("/api-error-422")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["message"] == "unprocessable"

    def test_api_error_with_empty_message(self, client):
        resp = client.get("/api-error-empty-msg")
        assert resp.status_code == 400
        assert resp.json()["error"]["message"] == ""

    def test_api_error_with_unicode_message(self, client):
        resp = client.get("/api-error-unicode")
        assert resp.status_code == 400
        assert resp.json()["error"]["message"] == "错误：输入无效"

    def test_generic_value_error_returns_500(self, client):
        resp = client.get("/value-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal_error"
        assert "bad value" in body["error"]["message"]

    def test_generic_type_error_returns_500(self, client):
        resp = client.get("/type-error")
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "internal_error"

    def test_generic_runtime_error_returns_500(self, client):
        resp = client.get("/runtime-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal_error"
        assert "something broke" in body["error"]["message"]


class TestRegisterErrorHandlers:
    def test_register_on_fastapi_app(self):
        app = FastAPI()
        register_error_handlers(app)  # should not raise
