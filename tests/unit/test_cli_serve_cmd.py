"""Tests for CLI serve command."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


class TestServeDefaultOptions:
    """Default options: generate_token called, uvicorn.run called with defaults."""

    def test_generate_token_called_by_default(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="tok-abc") as mock_gen,
            patch("uvicorn.run"),
        ):
            runner.invoke(app, ["serve"])

        mock_gen.assert_called_once()

    def test_uvicorn_run_called_with_defaults(self):
        mock_app = object()
        with (
            patch("agentnexus.server.app.create_app", return_value=mock_app),
            patch("agentnexus.server.auth.generate_token", return_value="tok-abc"),
            patch("uvicorn.run") as mock_run,
        ):
            runner.invoke(app, ["serve"])

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] is mock_app
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 18765

    def test_ready_message_contains_token(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="tok-xyz"),
            patch("uvicorn.run"),
        ):
            result = runner.invoke(app, ["serve"])

        out = result.output
        data = json.loads(out.strip().splitlines()[0])
        assert data["status"] == "ready"
        assert data["port"] == 18765
        assert data["auth_token"] == "tok-xyz"


class TestServeNoAuth:
    """no_auth=True: generate_token NOT called, auth_token is None."""

    def test_generate_token_not_called(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token") as mock_gen,
            patch("uvicorn.run"),
        ):
            runner.invoke(app, ["serve", "--no-auth"])

        mock_gen.assert_not_called()

    def test_ready_message_token_is_none(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token"),
            patch("uvicorn.run"),
        ):
            result = runner.invoke(app, ["serve", "--no-auth"])

        out = result.output
        data = json.loads(out.strip().splitlines()[0])
        assert data["auth_token"] is None


class TestServeWithAuth:
    """no_auth=False (explicit): generate_token IS called."""

    def test_generate_token_called_when_auth_enabled(self):
        """Default (no --no-auth flag) means auth is on."""
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="tok") as mock_gen,
            patch("uvicorn.run"),
        ):
            runner.invoke(app, ["serve"])

        mock_gen.assert_called_once()


class TestServeCustomPortHost:
    """Custom port/host forwarded to uvicorn.run."""

    def test_custom_port_and_host(self):
        with (
            patch("agentnexus.server.app.create_app", return_value=object()),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run") as mock_run,
        ):
            runner.invoke(app, ["serve", "--port", "9999", "--host", "0.0.0.0"])

        _, kwargs = mock_run.call_args
        assert kwargs["port"] == 9999
        assert kwargs["host"] == "0.0.0.0"

    def test_custom_port_in_ready_message(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run"),
        ):
            result = runner.invoke(app, ["serve", "--port", "5555"])

        out = result.output
        data = json.loads(out.strip().splitlines()[0])
        assert data["port"] == 5555


class TestServeErrors:
    """Error handling: OSError variants and unexpected exceptions."""

    def test_address_already_in_use(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run", side_effect=OSError("address already in use")),
        ):
            result = runner.invoke(app, ["serve"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)

    def test_only_one_usage(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run", side_effect=OSError("only one usage of each address")),
        ):
            result = runner.invoke(app, ["serve"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)

    def test_other_oserror(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run", side_effect=OSError("permission denied")),
        ):
            result = runner.invoke(app, ["serve"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)

    def test_unexpected_runtime_error(self):
        with (
            patch("agentnexus.server.app.create_app"),
            patch("agentnexus.server.auth.generate_token", return_value="t"),
            patch("uvicorn.run", side_effect=RuntimeError("boom")),
        ):
            result = runner.invoke(app, ["serve"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
