"""Unit tests for codegraph.hooks module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.codegraph.hooks import (
    sync_codegraph_on_file_read,
    sync_codegraph_on_file_write,
)


@pytest.fixture
def mock_ctx():
    """Create a mock hook context."""
    ctx = MagicMock()
    ctx.payload = {}
    return ctx


class TestSyncCodegraphOnFileWrite:
    def test_ignores_non_file_write(self, mock_ctx):
        mock_ctx.payload = {"name": "shell_exec", "result": {"status": "ok"}}
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_not_called()

    def test_ignores_failed_write(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": {"status": "error"},
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_not_called()

    def test_ignores_non_python_file(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": {"status": "ok"},
            "params": {"path": "test.js"},
        }
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_not_called()

    def test_syncs_python_file(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": {"status": "ok"},
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_called_once_with("test.py")

    def test_handles_sync_error_silently(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": {"status": "ok"},
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.sync_file", side_effect=Exception("error")):
            # Should not raise
            sync_codegraph_on_file_write(mock_ctx)

    def test_handles_dict_result(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": {"status": "ok", "path": "test.py"},
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_called_once()

    def test_handles_string_result(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_write",
            "result": "File written successfully",
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.sync_file") as mock_sync:
            sync_codegraph_on_file_write(mock_ctx)
            mock_sync.assert_called_once()


class TestSyncCodegraphOnFileRead:
    def test_ignores_non_file_read(self, mock_ctx):
        mock_ctx.payload = {"name": "shell_exec", "result": "output"}
        with patch("agentnexus.codegraph.updater.check_and_sync_file") as mock_check:
            sync_codegraph_on_file_read(mock_ctx)
            mock_check.assert_not_called()

    def test_ignores_error_result(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_read",
            "result": "错误: 文件不存在",
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.check_and_sync_file") as mock_check:
            sync_codegraph_on_file_read(mock_ctx)
            mock_check.assert_not_called()

    def test_ignores_non_python_file(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_read",
            "result": "[文件] test.js ...",
            "params": {"path": "test.js"},
        }
        with patch("agentnexus.codegraph.updater.check_and_sync_file") as mock_check:
            sync_codegraph_on_file_read(mock_ctx)
            mock_check.assert_not_called()

    def test_checks_python_file(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_read",
            "result": "[文件] test.py ...",
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.check_and_sync_file") as mock_check:
            sync_codegraph_on_file_read(mock_ctx)
            mock_check.assert_called_once_with("test.py")

    def test_handles_check_error_silently(self, mock_ctx):
        mock_ctx.payload = {
            "name": "file_read",
            "result": "[文件] test.py ...",
            "params": {"path": "test.py"},
        }
        with patch("agentnexus.codegraph.updater.check_and_sync_file", side_effect=Exception("error")):
            # Should not raise
            sync_codegraph_on_file_read(mock_ctx)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
