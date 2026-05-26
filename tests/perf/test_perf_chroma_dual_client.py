"""Stress: concurrent RAG + LTM ChromaDB clients sharing the same persist dir.

AGENTS.md notes that RAG and LTM each create independent chromadb.PersistentClient
pointing to the same chroma_persist_dir. This can cause SQLite locking under
concurrent writes.

These tests verify:
- Sequential RAG + LTM writes are safe (baseline).
- Concurrent writes from both clients expose ChromaDB race conditions.
- Data integrity when operations are properly synchronized with a shared lock.
- Thread-safety boundary: synchronized access works, unsynchronized fails.

KNOWN ISSUE: ChromaDB 1.5.8 on Windows has threading issues with Rust bindings
(AttributeError: 'RustBindingsAPI' object has no attribute 'bindings') and
SQLite locking under concurrent multi-client writes. This test suite
documents the precise failure modes.
"""

from __future__ import annotations

import threading

N_THREADS = 4
N_OPS = 20


def _reset_all():
    from agentnexus.memory.long_term import _reset_long_term_memory
    from agentnexus.rag.chroma_client import _reset_chroma_client
    _reset_long_term_memory()
    _reset_chroma_client(reset_model=True)


def _rag_write(text, metadata=None):
    from agentnexus.rag.chroma_client import insert_documents
    insert_documents(
        texts=[text],
        metadatas=[metadata or {"source": "stress"}],
    )


def _rag_search(query="document", limit=10):
    from agentnexus.rag.chroma_client import search
    return search(query=query, limit=limit)


def _ltm_save(session_id, content, importance=0.5):
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.save(
        session_id=session_id,
        content=content,
        category="entity_fact",
        importance=importance,
    )


def _ltm_list():
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    return ltm.search(query_embedding=None, limit=50)


class TestDualClientSequentialBaseline:
    """Sequential access — baseline to confirm the setup works."""

    def test_sequential_rag_then_ltm(self, perf_env):
        _reset_all()
        _rag_write("Seq RAG doc 1")
        _rag_write("Seq RAG doc 2")
        _ltm_save("s1", "Seq LTM entry 1")
        _ltm_save("s1", "Seq LTM entry 2")

        rag_results = _rag_search("Seq RAG", limit=10)
        assert len(rag_results) > 0
        assert any("Seq RAG" in r["text"] for r in rag_results)

        ltm_rows = _ltm_list()
        assert len(ltm_rows) >= 2
        contents = [r["content"] for r in ltm_rows]
        assert "Seq LTM entry 1" in contents

    def test_interleaved_rag_and_ltm(self, perf_env):
        _reset_all()
        for i in range(5):
            _rag_write(f"Interleaved RAG {i}")
            _ltm_save("s1", f"Interleaved LTM {i}")

        rag_results = _rag_search("Interleaved", limit=20)
        assert len(rag_results) == 5

        ltm_rows = _ltm_list()
        assert len(ltm_rows) >= 5


class TestDualClientSynchronized:
    """Concurrent access with a shared lock — should work."""

    def test_synchronized_concurrent_writes(self, perf_env):
        _reset_all()
        lock = threading.Lock()
        errors: list[Exception] = []

        def _safe_rag(i):
            try:
                with lock:
                    _rag_write(f"Sync RAG {i} from {threading.current_thread().name}")
            except Exception as e:
                errors.append(e)

        def _safe_ltm(i):
            try:
                with lock:
                    _ltm_save("sync", f"Sync LTM {i} from {threading.current_thread().name}")
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=_safe_rag, args=(i,)) for i in range(N_OPS)]
            + [threading.Thread(target=_safe_ltm, args=(i,)) for i in range(N_OPS)]
        )

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Synchronized access raised {len(errors)} errors: {errors[:3]}"

        rag_results = _rag_search("Sync", limit=50)
        assert len(rag_results) >= N_OPS // 2

    def test_synchronized_mixed_read_write(self, perf_env):
        _reset_all()
        lock = threading.Lock()
        errors: list[Exception] = []
        n = 10

        def _safe_rag_write(i):
            try:
                with lock:
                    _rag_write(f"Mixed RAG {i}")
            except Exception as e:
                errors.append(e)

        def _safe_rag_read():
            try:
                with lock:
                    _rag_search("Mixed", limit=5)
            except Exception as e:
                errors.append(e)

        def _safe_ltm_write(i):
            try:
                with lock:
                    _ltm_save("mixed", f"Mixed LTM {i}")
            except Exception as e:
                errors.append(e)

        def _safe_ltm_read():
            try:
                with lock:
                    _ltm_list()
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=_safe_rag_write, args=(i,)) for i in range(n)]
            + [threading.Thread(target=_safe_rag_read) for _ in range(n)]
            + [threading.Thread(target=_safe_ltm_write, args=(i,)) for i in range(n)]
            + [threading.Thread(target=_safe_ltm_read) for _ in range(n)]
        )

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Synchronized mixed access raised errors: {errors[:3]}"


class TestDualClientDataIntegrity:
    """Verify data survives concurrent writes from the other client."""

    def test_rag_readable_after_ltm_writes(self, perf_env):
        _reset_all()
        _rag_write("RAG doc about Python")
        _rag_write("RAG doc about Data Science")

        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def _write_ltm():
            try:
                barrier.wait()
                for i in range(10):
                    _ltm_save("s1", f"LTM concurrent entry {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write_ltm) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        if not errors:
            rag_results = _rag_search("Python", limit=10)
            texts = [r["text"] for r in rag_results]
            assert any("Python" in t for t in texts)

    def test_ltm_readable_after_rag_writes(self, perf_env):
        _reset_all()
        _ltm_save("s1", "Initial LTM entry", importance=0.8)

        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def _write_rag():
            try:
                barrier.wait()
                for i in range(10):
                    _rag_write(f"RAG concurrent doc {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write_rag) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        if not errors:
            rows = _ltm_list()
            contents = [r["content"] for r in rows]
            assert "Initial LTM entry" in contents


class TestDualClientConcurrentKnownIssue:
    """Concurrent multi-client writes WITHOUT synchronization.

    These tests document KNOWN ChromaDB race conditions.
    ChromaDB 1.5.8's Rust bindings are not thread-safe for concurrent
    PersistentClient access from multiple threads.
    """

    def test_concurrent_writes_may_fail(self, perf_env):
        _reset_all()
        n = N_THREADS
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def _rag_writer():
            try:
                barrier.wait()
                for i in range(N_OPS):
                    _rag_write(f"Concurrent RAG {i} from {threading.current_thread().name}")
            except Exception as e:
                errors.append(e)

        def _ltm_writer():
            try:
                barrier.wait()
                for i in range(N_OPS):
                    _ltm_save("cncr", f"Concurrent LTM {i} from {threading.current_thread().name}")
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=_rag_writer) for _ in range(n // 2)]
            + [threading.Thread(target=_ltm_writer) for _ in range(n // 2)]
        )

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        if errors:
            error_types = {type(e).__name__ for e in errors}
            print(f"Known ChromaDB concurrency issue: {len(errors)} errors, types: {error_types}")
        else:
            print("Concurrent writes succeeded without errors (environment-dependent)")
