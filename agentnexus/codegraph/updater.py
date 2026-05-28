"""Code knowledge graph updater.

Handles incremental and full builds, WAL-based crash recovery,
affected-file scanning, and consistency checks between SQLite and ChromaDB.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from agentnexus.codegraph import embeddings as codegraph_embeddings
from agentnexus.codegraph import vector_store
from agentnexus.codegraph.models import NodeData
from agentnexus.codegraph.parser import auto_register_parsers, get_parser_for_file
from agentnexus.codegraph.store import CodeGraphStore, detect_project_root, get_db_path

logger = logging.getLogger(__name__)

# File extensions to scan
_SUPPORTED_EXTENSIONS = {".py"}

# Directories to skip during scanning
_SKIP_DIRS = {
    ".git",
    ".hg",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    ".tox",
    ".eggs",
    "*.egg-info",
    "dist",
    "build",
    ".agentnexus",
    "venv",
    ".venv",
    "env",
}


@dataclass
class BuildResult:
    """Result of a build operation."""

    files_processed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_added: int = 0
    nodes_updated: int = 0
    nodes_deleted: int = 0
    edges_added: int = 0
    embeddings_written: int = 0
    elapsed_ms: float = 0.0

    @property
    def summary(self) -> str:
        return (
            f"Processed {self.files_processed} files in {self.elapsed_ms:.0f}ms\n"
            f"  Nodes: +{self.nodes_added} ~{self.nodes_updated} -{self.nodes_deleted}\n"
            f"  Edges: +{self.edges_added}\n"
            f"  Embeddings: {self.embeddings_written}\n"
            f"  Errors: {self.files_errored}"
        )


def _compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _scan_project_files(project_root: Path) -> list[Path]:
    """Find all supported source files in a project."""
    files: list[Path] = []
    for path in project_root.rglob("*"):
        # Skip hidden and special directories
        parts = path.relative_to(project_root).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in parts):
            continue
        if path.is_file() and path.suffix.lower() in _SUPPORTED_EXTENSIONS:
            files.append(path)
    return sorted(files)


def _get_changed_files(
    store: CodeGraphStore,
    files: list[Path],
    project_root: Path,
) -> tuple[list[tuple[Path, str]], list[str]]:
    """Find files that have changed since last index.

    Returns (changed_files, deleted_files).
    changed_files: list of (path, content) tuples
    deleted_files: list of file paths (relative to project root)
    """
    tracked = {f["path"]: f for f in store.get_all_files()}
    tracked_paths = set(tracked.keys())

    changed: list[tuple[Path, str]] = []
    current_paths: set[str] = set()

    for fpath in files:
        rel = str(fpath.relative_to(project_root)).replace("\\", "/")
        current_paths.add(rel)

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        content_hash = _compute_content_hash(content)
        existing = tracked.get(rel)

        if existing is None or existing["content_hash"] != content_hash:
            changed.append((fpath, content))

    deleted = list(tracked_paths - current_paths)
    return changed, deleted


def build_graph(
    project_path: Path | str | None = None,
    force: bool = False,
) -> BuildResult:
    """Build or update the code knowledge graph.

    Args:
        project_path: Project root directory. Auto-detected if None.
        force: If True, clear all data and rebuild from scratch.

    Returns:
        BuildResult with statistics.
    """
    start_time = time.perf_counter()

    if project_path is None:
        project_path = detect_project_root()
    project_path = Path(project_path)

    auto_register_parsers()

    db_path = get_db_path(project_path)
    store = CodeGraphStore(db_path)
    store.init_schema()

    result = BuildResult()

    try:
        if force:
            _full_build(store, project_path, result)
        else:
            # Recover from WAL first
            _recover_wal(store, project_path, result)
            # Then do incremental build
            _incremental_build(store, project_path, result)

        result.elapsed_ms = (time.perf_counter() - start_time) * 1000
    finally:
        store.close()

    return result


def _full_build(store: CodeGraphStore, project_root: Path, result: BuildResult) -> None:
    """Clear all data and rebuild from scratch."""
    # Clear WAL
    store.wal_clear_all()

    # Clear SQLite
    store.clear_all()

    # Clear ChromaDB collection
    vector_store.clear_collection()

    # Scan and parse all files
    files = _scan_project_files(project_root)
    _process_files(store, files, project_root, result, skip_hash_check=True)


def _incremental_build(
    store: CodeGraphStore,
    project_root: Path,
    result: BuildResult,
) -> None:
    """Incremental build: only process changed files."""
    files = _scan_project_files(project_root)
    changed_files, deleted_files = _get_changed_files(store, files, project_root)

    # Process deleted files
    for rel_path in deleted_files:
        _process_deleted_file(store, rel_path, result)

    # Process changed files
    _process_files(store, [f for f, _ in changed_files], project_root, result)

    # Consistency check
    _check_consistency(store)


def _process_files(
    store: CodeGraphStore,
    files: list[Path],
    project_root: Path,
    result: BuildResult,
    skip_hash_check: bool = False,
) -> None:
    """Process a list of files."""
    for fpath in files:
        rel_path = str(fpath.relative_to(project_root)).replace("\\", "/")
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            result.files_errored += 1
            logger.warning(f"Failed to read {rel_path}: {e}")
            continue

        content_hash = _compute_content_hash(content)
        file_size = len(content.encode("utf-8"))

        # Check if unchanged
        if not skip_hash_check:
            existing = store.get_file(rel_path)
            if existing and existing["content_hash"] == content_hash:
                result.files_skipped += 1
                continue

        _process_single_file(
            store, fpath, rel_path, content, content_hash, file_size, project_root, result
        )


def _process_single_file(
    store: CodeGraphStore,
    fpath: Path,
    rel_path: str,
    content: str,
    content_hash: str,
    file_size: int,
    project_root: Path,
    result: BuildResult,
) -> None:
    """Process a single file: parse, update SQLite and ChromaDB."""
    # Record WAL
    store.wal_begin(rel_path, content_hash)

    # Parse
    parser = get_parser_for_file(fpath)
    if parser is None:
        store.wal_clear(rel_path)
        result.files_skipped += 1
        return

    parse_result = parser.parse_file(fpath, content)

    # Handle parse errors
    if parse_result.partial:
        store.update_file_errors(rel_path, parse_result.errors)
        store.wal_clear(rel_path)
        result.files_errored += 1
        return

    # Get old node IDs for affected-file scan
    old_nodes = store.get_nodes_by_file(rel_path)
    old_node_ids = {n["id"] for n in old_nodes}

    # Find affected files (before deleting old nodes)
    affected_files = store.find_affected_files(old_node_ids, rel_path)

    # Compute which new nodes need embeddings
    new_nodes = parse_result.nodes
    new_node_ids = {n.id for n in new_nodes}

    # Generate embeddings (Blue-Green: write new before deleting old)
    embeddings = codegraph_embeddings.generate_embeddings_batch(new_nodes)
    emb_count = vector_store.upsert_nodes(new_nodes, embeddings, content_hash)
    result.embeddings_written += emb_count

    # SQLite transaction
    conn = store._get_conn()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with store.transaction():
            # Delete old nodes not in new set (CASCADE handles edges)
            deleted = store.delete_nodes_by_file(rel_path, exclude_ids=new_node_ids)
            result.nodes_deleted += deleted

            # Insert new nodes
            store.upsert_nodes_batch(new_nodes)
            result.nodes_added += len(new_nodes)

            # Insert new edges
            store.upsert_edges_batch(parse_result.edges)
            result.edges_added += len(parse_result.edges)

            # Update file tracking
            modified_at = int(fpath.stat().st_mtime)
            store.upsert_file(
                path=rel_path,
                content_hash=content_hash,
                language=parser.language,
                size=file_size,
                modified_at=modified_at,
                node_count=len(new_nodes),
                errors=None,
            )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    # Delete old embeddings (Blue-Green cleanup)
    vector_store.delete_by_file(rel_path, exclude_content_hash=content_hash)

    # Clear WAL
    store.wal_clear(rel_path)
    result.files_processed += 1

    # Process affected files
    for affected_path in affected_files:
        affected_full = project_root / affected_path
        if affected_full.exists():
            try:
                content = affected_full.read_text(encoding="utf-8", errors="replace")
                content_hash = _compute_content_hash(content)
                file_size = len(content.encode("utf-8"))
                _process_single_file(
                    store, affected_full, affected_path, content,
                    content_hash, file_size, project_root, result
                )
            except Exception as e:
                logger.warning(f"Failed to re-process affected file {affected_path}: {e}")


def _process_deleted_file(
    store: CodeGraphStore,
    rel_path: str,
    result: BuildResult,
) -> None:
    """Handle a file that has been deleted from disk."""
    # Delete from ChromaDB
    vector_store.delete_by_file(rel_path)

    # Delete from SQLite (nodes cascade to edges)
    store.delete_file(rel_path)
    store.delete_nodes_by_file(rel_path)

    result.files_processed += 1


def _recover_wal(store: CodeGraphStore, project_root: Path, result: BuildResult) -> None:
    """Recover from incomplete operations using WAL entries."""
    pending = store.wal_get_pending()
    if not pending:
        return

    for entry in pending:
        file_path = project_root / entry["file_path"]
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                content_hash = _compute_content_hash(content)
                file_size = len(content.encode("utf-8"))
                _process_single_file(
                    store, file_path, entry["file_path"], content,
                    content_hash, file_size, project_root, result
                )
            except Exception as e:
                logger.warning(f"WAL recovery failed for {entry['file_path']}: {e}")
                store.wal_clear(entry["file_path"])
        else:
            # File no longer exists, clean up
            _process_deleted_file(store, entry["file_path"], result)
            store.wal_clear(entry["file_path"])


def _check_consistency(store: CodeGraphStore) -> None:
    """Lightweight consistency check between SQLite and ChromaDB.

    Silently repairs orphaned embeddings.
    """
    try:
        sqlite_ids = store.get_all_node_ids()
        chroma_ids = vector_store.get_all_ids()

        only_in_sqlite = sqlite_ids - chroma_ids
        only_in_chroma = chroma_ids - sqlite_ids

        if only_in_sqlite:
            # Nodes without embeddings - regenerate
            nodes_data = store.get_nodes_by_ids(only_in_sqlite)
            node_objs = [
                NodeData(
                    id=n["id"],
                    kind=n["kind"],
                    name=n["name"],
                    qualified_name=n["qualified_name"],
                    file_path=n["file_path"],
                    language=n["language"],
                    start_line=n["start_line"],
                    end_line=n["end_line"],
                )
                for n in nodes_data
                if n["kind"] not in ("variable", "import")
            ]
            if node_objs:
                embeddings = codegraph_embeddings.generate_embeddings_batch(node_objs)
                # Get content_hash from file tracking
                file_hashes: dict[str, str] = {}
                for n in node_objs:
                    if n.file_path not in file_hashes:
                        f = store.get_file(n.file_path)
                        file_hashes[n.file_path] = f["content_hash"] if f else ""
                for n, emb in zip(node_objs, embeddings):
                    if emb:
                        vector_store.upsert_nodes(
                            [n], [emb], file_hashes.get(n.file_path, "")
                        )

        if only_in_chroma:
            # Orphaned embeddings - delete
            vector_store.delete_by_ids(list(only_in_chroma))

    except Exception as e:
        logger.warning(f"Consistency check failed: {e}")


def sync_file(file_path: str | Path, project_root: Path | None = None) -> None:
    """Sync a single file into the codegraph.

    Called by hooks after file_write operations.
    Silently fails on errors.
    """
    try:
        file_path = Path(file_path)
        if project_root is None:
            project_root = detect_project_root()

        auto_register_parsers()

        db_path = get_db_path(project_root)
        store = CodeGraphStore(db_path)
        store.init_schema()

        try:
            rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
            content = file_path.read_text(encoding="utf-8", errors="replace")
            content_hash = _compute_content_hash(content)
            file_size = len(content.encode("utf-8"))

            result = BuildResult()
            _process_single_file(
                store, file_path, rel_path, content,
                content_hash, file_size, project_root, result
            )
        finally:
            store.close()
    except Exception as e:
        logger.debug(f"sync_file failed for {file_path}: {e}")


def check_and_sync_file(file_path: str | Path, project_root: Path | None = None) -> None:
    """Check if a file has changed and sync if needed.

    Called by hooks after file_read operations to detect external modifications.
    """
    try:
        file_path = Path(file_path)
        if project_root is None:
            project_root = detect_project_root()

        db_path = get_db_path(project_root)
        store = CodeGraphStore(db_path)
        store.init_schema()

        try:
            rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
            existing = store.get_file(rel_path)
            if existing is None:
                # Not tracked yet, sync it
                sync_file(file_path, project_root)
                return

            content = file_path.read_text(encoding="utf-8", errors="replace")
            content_hash = _compute_content_hash(content)
            if existing["content_hash"] != content_hash:
                sync_file(file_path, project_root)
        finally:
            store.close()
    except Exception as e:
        logger.debug(f"check_and_sync_file failed for {file_path}: {e}")


def verify_consistency(project_root: Path | None = None) -> list[str]:
    """Verify consistency between SQLite and ChromaDB.

    Returns a list of issue descriptions. Empty list means consistent.
    """
    if project_root is None:
        project_root = detect_project_root()

    issues: list[str] = []
    db_path = get_db_path(project_root)
    store = CodeGraphStore(db_path)
    store.init_schema()

    try:
        sqlite_ids = store.get_all_node_ids()
        chroma_ids = vector_store.get_all_ids()

        only_in_sqlite = sqlite_ids - chroma_ids
        only_in_chroma = chroma_ids - sqlite_ids

        if only_in_sqlite:
            issues.append(f"{len(only_in_sqlite)} nodes missing embeddings")
        if only_in_chroma:
            issues.append(f"{len(only_in_chroma)} orphaned embeddings")

        # Check file tracking consistency
        for f in store.get_all_files():
            nodes = store.get_nodes_by_file(f["path"])
            if len(nodes) != f["node_count"]:
                issues.append(
                    f"Node count mismatch for {f['path']}: "
                    f"tracked={f['node_count']}, actual={len(nodes)}"
                )

    finally:
        store.close()

    return issues


__all__ = [
    "BuildResult",
    "build_graph",
    "sync_file",
    "check_and_sync_file",
    "verify_consistency",
]
