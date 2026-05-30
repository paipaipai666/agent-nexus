"""Unit tests for codegraph.updater module."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from agentnexus.codegraph.models import NodeData, NodeKind
from agentnexus.codegraph.store import CodeGraphStore
from agentnexus.codegraph.updater import (
    BuildResult,
    _compute_content_hash,
    _get_changed_files,
    _process_deleted_file,
    _scan_project_files,
    build_graph,
    check_and_sync_file,
    sync_file,
    verify_consistency,
)


@pytest.fixture
def project_dir(tmp_path):
    """Create a sample project directory."""
    # Create .agentnexus directory
    (tmp_path / ".agentnexus").mkdir(exist_ok=True)

    # Create a sample Python file
    sample_code = '''
def hello():
    """Say hello"""
    print("hello")

class Greeter:
    """A greeter class"""
    def greet(self, name: str) -> str:
        """Greet someone"""
        return f"Hello, {name}!"
'''
    (tmp_path / "sample.py").write_text(sample_code)

    # Create another file
    (tmp_path / "utils.py").write_text("def helper():\n    return 42\n")

    # Create a subdirectory with a file
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "module.py").write_text("X = 1\n")

    # Create hidden directory (should be skipped)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.py").write_text("SECRET = True\n")

    return tmp_path


@pytest.fixture
def store(tmp_path):
    """Create a temporary store."""
    db_path = tmp_path / ".agentnexus" / "codegraph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    s = CodeGraphStore(db_path)
    s.init_schema()
    yield s
    s.close()


class TestBuildResult:
    def test_defaults(self):
        result = BuildResult()
        assert result.files_processed == 0
        assert result.files_skipped == 0
        assert result.files_errored == 0
        assert result.nodes_added == 0
        assert result.elapsed_ms == 0.0

    def test_summary(self):
        result = BuildResult(
            files_processed=10,
            nodes_added=50,
            edges_added=100,
            embeddings_written=45,
            elapsed_ms=1234.5,
        )
        summary = result.summary
        assert "10 files" in summary
        assert "+50" in summary
        assert "+100" in summary


class TestComputeContentHash:
    def test_hash_consistency(self):
        content = "def hello(): pass"
        h1 = _compute_content_hash(content)
        h2 = _compute_content_hash(content)
        assert h1 == h2

    def test_hash_format(self):
        h = _compute_content_hash("test")
        assert len(h) == 64  # SHA-256 hex digest
        assert h == hashlib.sha256("test".encode("utf-8")).hexdigest()

    def test_different_content_different_hash(self):
        h1 = _compute_content_hash("abc")
        h2 = _compute_content_hash("def")
        assert h1 != h2


class TestScanProjectFiles:
    def test_finds_python_files(self, project_dir):
        files = _scan_project_files(project_dir)
        file_names = [f.name for f in files]
        assert "sample.py" in file_names
        assert "utils.py" in file_names

    def test_skips_hidden_dirs(self, project_dir):
        files = _scan_project_files(project_dir)
        file_paths = [str(f) for f in files]
        assert not any(".hidden" in p for p in file_paths)

    def test_includes_subdirs(self, project_dir):
        files = _scan_project_files(project_dir)
        file_names = [f.name for f in files]
        assert "module.py" in file_names

    def test_skips_non_python(self, project_dir):
        (project_dir / "readme.txt").write_text("hello")
        files = _scan_project_files(project_dir)
        file_names = [f.name for f in files]
        assert "readme.txt" not in file_names


class TestGetChangedFiles:
    def test_new_files_detected(self, store, project_dir):
        files = _scan_project_files(project_dir)
        changed, deleted = _get_changed_files(store, files, project_dir)
        assert len(changed) >= 2  # sample.py, utils.py
        assert deleted == []

    def test_unchanged_files_skipped(self, store, project_dir):
        # First pass - add files
        files = _scan_project_files(project_dir)
        for fpath in files:
            content = fpath.read_text(encoding="utf-8")
            content_hash = _compute_content_hash(content)
            rel = str(fpath.relative_to(project_dir)).replace("\\", "/")
            store.upsert_file(rel, content_hash, "python", len(content), 1000, 1)

        # Second pass - should find no changes
        changed, deleted = _get_changed_files(store, files, project_dir)
        assert len(changed) == 0

    def test_modified_files_detected(self, store, project_dir):
        # First pass
        files = _scan_project_files(project_dir)
        for fpath in files:
            content = fpath.read_text(encoding="utf-8")
            content_hash = _compute_content_hash(content)
            rel = str(fpath.relative_to(project_dir)).replace("\\", "/")
            store.upsert_file(rel, content_hash, "python", len(content), 1000, 1)

        # Modify a file
        (project_dir / "sample.py").write_text("def new_func(): pass\n")

        # Should detect the change
        changed, deleted = _get_changed_files(store, files, project_dir)
        assert len(changed) == 1

    def test_deleted_files_detected(self, store, project_dir):
        # Add all files
        files = _scan_project_files(project_dir)
        for fpath in files:
            content = fpath.read_text(encoding="utf-8")
            content_hash = _compute_content_hash(content)
            rel = str(fpath.relative_to(project_dir)).replace("\\", "/")
            store.upsert_file(rel, content_hash, "python", len(content), 1000, 1)

        # Also add a tracked file that no longer exists
        store.upsert_file("deleted.py", "hash", "python", 100, 1000, 1)

        # Should detect the deleted file
        changed, deleted = _get_changed_files(store, files, project_dir)
        assert "deleted.py" in deleted


class TestProcessDeletedFile:
    def test_removes_file_tracking(self, store):
        store.upsert_file("test.py", "hash", "python", 100, 1000, 1)
        result = BuildResult()
        _process_deleted_file(store, "test.py", result)
        assert store.get_file("test.py") is None
        assert result.files_processed == 1


class TestSyncFile:
    @patch("agentnexus.codegraph.updater.vector_store")
    @patch("agentnexus.codegraph.updater.detect_project_root")
    def test_sync_file(self, mock_detect, mock_vs, project_dir):
        mock_detect.return_value = project_dir
        mock_vs.upsert_nodes.return_value = 0
        mock_vs.delete_by_file.return_value = 0

        # Disable foreign keys for this test
        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store._get_conn().execute("PRAGMA foreign_keys=OFF")
        store.close()

        sample_file = project_dir / "sample.py"
        sync_file(sample_file, project_dir)

        # Should have created the file tracking
        store = CodeGraphStore(db_path)
        store.init_schema()
        f = store.get_file("sample.py")
        assert f is not None
        store.close()


class TestCheckAndSyncFile:
    @patch("agentnexus.codegraph.updater.vector_store")
    @patch("agentnexus.codegraph.updater.detect_project_root")
    def test_syncs_untracked_file(self, mock_detect, mock_vs, project_dir):
        mock_detect.return_value = project_dir
        mock_vs.upsert_nodes.return_value = 0
        mock_vs.delete_by_file.return_value = 0

        # Disable foreign keys for this test
        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store._get_conn().execute("PRAGMA foreign_keys=OFF")
        store.close()

        sample_file = project_dir / "sample.py"
        check_and_sync_file(sample_file, project_dir)

        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store.init_schema()
        f = store.get_file("sample.py")
        assert f is not None
        store.close()

    @patch("agentnexus.codegraph.updater.vector_store")
    @patch("agentnexus.codegraph.updater.detect_project_root")
    def test_skips_unchanged_file(self, mock_detect, mock_vs, project_dir):
        mock_detect.return_value = project_dir

        # Pre-populate the store
        sample_file = project_dir / "sample.py"
        content = sample_file.read_text(encoding="utf-8")
        content_hash = _compute_content_hash(content)

        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store.init_schema()
        store.upsert_file("sample.py", content_hash, "python", len(content), 1000, 1)
        store.close()

        # Should not call sync since file hasn't changed
        check_and_sync_file(sample_file, project_dir)


class TestBuildGraph:
    @patch("agentnexus.codegraph.updater.vector_store")
    def test_build_creates_graph(self, mock_vs, project_dir):
        mock_vs.upsert_nodes.return_value = 0
        mock_vs.delete_by_file.return_value = 0
        mock_vs.get_all_ids.return_value = set()
        mock_vs.clear_collection.return_value = None

        # Disable foreign keys for this test to avoid cross-file edge issues
        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store._get_conn().execute("PRAGMA foreign_keys=OFF")
        store.close()

        result = build_graph(project_dir)
        assert result.files_processed >= 2
        assert result.nodes_added > 0

    @patch("agentnexus.codegraph.updater.vector_store")
    def test_force_build_clears_data(self, mock_vs, project_dir):
        mock_vs.upsert_nodes.return_value = 0
        mock_vs.delete_by_file.return_value = 0
        mock_vs.get_all_ids.return_value = set()
        mock_vs.clear_collection.return_value = None

        # Disable foreign keys for this test
        db_path = project_dir / ".agentnexus" / "codegraph.db"
        store = CodeGraphStore(db_path)
        store._get_conn().execute("PRAGMA foreign_keys=OFF")
        store.close()

        result = build_graph(project_dir, force=True)
        assert result.files_processed >= 2


class TestVerifyConsistency:
    @patch("agentnexus.codegraph.updater.vector_store")
    def test_consistent_state(self, mock_vs, tmp_path):
        # Create a fresh store
        db_path = tmp_path / ".agentnexus" / "codegraph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = CodeGraphStore(db_path)
        store.init_schema()
        store.upsert_file("test.py", "hash", "python", 100, 1000, 0)
        store.close()

        mock_vs.get_all_ids.return_value = set()

        issues = verify_consistency(tmp_path)
        assert issues == []

    @patch("agentnexus.codegraph.updater.vector_store")
    def test_detects_missing_embeddings(self, mock_vs, tmp_path):
        # Create a fresh store
        db_path = tmp_path / ".agentnexus" / "codegraph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = CodeGraphStore(db_path)
        store.init_schema()

        # Add a node but no matching embedding
        node = NodeData(
            id="function:test.func",
            kind=NodeKind.FUNCTION,
            name="func",
            qualified_name="test.func",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=10,
        )
        store.upsert_node(node)
        store.upsert_file("test.py", "hash", "python", 100, 1000, 1)
        store.close()

        mock_vs.get_all_ids.return_value = set()

        issues = verify_consistency(tmp_path)
        assert any("missing embeddings" in i for i in issues)

    @patch("agentnexus.codegraph.updater.vector_store")
    def test_detects_orphaned_embeddings(self, mock_vs, tmp_path):
        # Create a fresh store
        db_path = tmp_path / ".agentnexus" / "codegraph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = CodeGraphStore(db_path)
        store.init_schema()
        store.upsert_file("test.py", "hash", "python", 100, 1000, 0)
        store.close()

        mock_vs.get_all_ids.return_value = {"orphan:node"}

        issues = verify_consistency(tmp_path)
        assert any("orphaned" in i for i in issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
