"""Unit tests for codegraph.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app


@pytest.fixture
def runner():
    return CliRunner()


class TestBuildCommand:
    @patch("agentnexus.codegraph.updater.build_graph")
    def test_build_success(self, mock_build, runner):
        mock_build.return_value = MagicMock(
            files_processed=10,
            nodes_added=50,
            edges_added=100,
            embeddings_written=45,
            elapsed_ms=1234.5,
            summary="Processed 10 files in 1234ms\n  Nodes: +50 ~0 -0\n  Edges: +100\n  Embeddings: 45\n  Errors: 0",
        )
        result = runner.invoke(app, ["codegraph", "build"])
        assert result.exit_code == 0
        assert "图谱构建完成" in result.output

    @patch("agentnexus.codegraph.updater.build_graph")
    def test_build_with_force(self, mock_build, runner):
        mock_build.return_value = MagicMock(
            files_processed=5,
            summary="Processed 5 files in 100ms",
        )
        result = runner.invoke(app, ["codegraph", "build", "--force"])
        assert result.exit_code == 0
        mock_build.assert_called_once()

    @patch("agentnexus.codegraph.updater.build_graph")
    def test_build_error(self, mock_build, runner):
        mock_build.side_effect = Exception("Build failed")
        result = runner.invoke(app, ["codegraph", "build"])
        assert result.exit_code == 1
        assert "构建失败" in result.output


class TestSearchCommand:
    @patch("agentnexus.codegraph.queries.search_entities")
    def test_search_with_results(self, mock_search, runner):
        mock_search.return_value = [
            MagicMock(
                name="func",
                kind="function",
                file_path="test.py",
                start_line=1,
                signature="(x: int) -> str",
                docstring="Test function.",
            )
        ]
        result = runner.invoke(app, ["codegraph", "search", "test"])
        assert result.exit_code == 0
        assert "func" in result.output

    @patch("agentnexus.codegraph.queries.search_entities")
    def test_search_no_results(self, mock_search, runner):
        mock_search.return_value = []
        result = runner.invoke(app, ["codegraph", "search", "nonexistent"])
        assert result.exit_code == 0
        assert "未找到" in result.output


class TestCallersCommand:
    @patch("agentnexus.codegraph.queries.get_callers")
    def test_callers_with_results(self, mock_callers, runner):
        mock_callers.return_value = [
            MagicMock(
                name="caller_func",
                kind="function",
                file_path="test.py",
                start_line=10,
            )
        ]
        result = runner.invoke(app, ["codegraph", "callers", "func"])
        assert result.exit_code == 0
        assert "caller_func" in result.output

    @patch("agentnexus.codegraph.queries.get_callers")
    def test_callers_no_results(self, mock_callers, runner):
        mock_callers.return_value = []
        result = runner.invoke(app, ["codegraph", "callers", "func"])
        assert result.exit_code == 0
        assert "未找到" in result.output


class TestCalleesCommand:
    @patch("agentnexus.codegraph.queries.get_callees")
    def test_callees_with_results(self, mock_callees, runner):
        mock_callees.return_value = [
            MagicMock(
                name="callee_func",
                kind="function",
                file_path="test.py",
                start_line=20,
            )
        ]
        result = runner.invoke(app, ["codegraph", "callees", "func"])
        assert result.exit_code == 0
        assert "callee_func" in result.output

    @patch("agentnexus.codegraph.queries.get_callees")
    def test_callees_no_results(self, mock_callees, runner):
        mock_callees.return_value = []
        result = runner.invoke(app, ["codegraph", "callees", "func"])
        assert result.exit_code == 0
        assert "未调用" in result.output


class TestInheritsCommand:
    @patch("agentnexus.codegraph.queries.get_inheritance_tree")
    def test_inherits_with_results(self, mock_inherits, runner):
        mock_inherits.return_value = [
            MagicMock(
                name="ParentClass",
                kind="class",
                file_path="test.py",
                start_line=1,
            )
        ]
        result = runner.invoke(app, ["codegraph", "inherits", "ChildClass"])
        assert result.exit_code == 0
        assert "ParentClass" in result.output

    @patch("agentnexus.codegraph.queries.get_inheritance_tree")
    def test_inherits_no_results(self, mock_inherits, runner):
        mock_inherits.return_value = []
        result = runner.invoke(app, ["codegraph", "inherits", "MyClass"])
        assert result.exit_code == 0
        assert "未找到" in result.output


class TestImportsCommand:
    @patch("agentnexus.codegraph.queries.get_imports")
    def test_imports_with_results(self, mock_imports, runner):
        mock_imports.return_value = [
            MagicMock(
                name="os",
                kind="import",
                file_path="test.py",
                start_line=1,
            )
        ]
        result = runner.invoke(app, ["codegraph", "imports", "test"])
        assert result.exit_code == 0
        assert "os" in result.output

    @patch("agentnexus.codegraph.queries.get_imports")
    def test_imports_no_results(self, mock_imports, runner):
        mock_imports.return_value = []
        result = runner.invoke(app, ["codegraph", "imports", "module"])
        assert result.exit_code == 0
        assert "未找到" in result.output


class TestContextCommand:
    @patch("agentnexus.codegraph.queries.get_entity_context")
    def test_context_found(self, mock_context, runner):
        mock_context.return_value = {
            "entity": MagicMock(
                name="func",
                kind="function",
                qualified_name="pkg.func",
                file_path="test.py",
                start_line=1,
                signature="(x: int) -> str",
                docstring="Test function.",
            ),
            "callers": [
                MagicMock(name="caller", kind="function", file_path="test.py", start_line=10)
            ],
            "callees": [],
        }
        result = runner.invoke(app, ["codegraph", "context", "func"])
        assert result.exit_code == 0
        assert "func" in result.output

    @patch("agentnexus.codegraph.queries.get_entity_context")
    def test_context_not_found(self, mock_context, runner):
        mock_context.return_value = None
        result = runner.invoke(app, ["codegraph", "context", "nonexistent"])
        assert result.exit_code == 1
        assert "未找到" in result.output


class TestStatsCommand:
    @patch("agentnexus.codegraph.store.get_db_path")
    def test_stats_no_db(self, mock_db_path, runner, tmp_path):
        mock_db_path.return_value = tmp_path / "nonexistent.db"
        result = runner.invoke(app, ["codegraph", "stats"])
        assert result.exit_code == 0
        assert "未构建" in result.output

    @patch("agentnexus.codegraph.store.CodeGraphStore")
    @patch("agentnexus.codegraph.store.detect_project_root")
    @patch("agentnexus.codegraph.store.get_db_path")
    def test_stats_with_data(self, mock_db_path, mock_root, mock_store_cls, runner, tmp_path):
        db_path = tmp_path / ".agentnexus" / "codegraph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()
        mock_db_path.return_value = db_path
        mock_root.return_value = tmp_path

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "node_count": 100,
            "edge_count": 200,
            "file_count": 10,
            "last_updated": 1000,
            "node_kinds": {"function": 50, "class": 20, "method": 30},
            "edge_kinds": {"calls": 100, "contains": 80, "inherits": 20},
        }
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["codegraph", "stats"])
        assert result.exit_code == 0
        assert "100" in result.output


class TestVerifyCommand:
    @patch("agentnexus.codegraph.updater.verify_consistency")
    def test_verify_consistent(self, mock_verify, runner):
        mock_verify.return_value = []
        result = runner.invoke(app, ["codegraph", "verify"])
        assert result.exit_code == 0
        assert "通过" in result.output

    @patch("agentnexus.codegraph.updater.verify_consistency")
    def test_verify_with_issues(self, mock_verify, runner):
        mock_verify.return_value = ["5 nodes missing embeddings"]
        result = runner.invoke(app, ["codegraph", "verify"])
        assert result.exit_code == 0
        assert "missing embeddings" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
