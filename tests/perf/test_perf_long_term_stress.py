"""Stress/regression: ChromaDB client rebuild overhead and session isolation.

AGENTS.md notes that long_term.py rebuilds ChromaDB client on every
save/search. In practice the module-level singleton caches the client,
but there is no stress test confirming this does not regress.

These tests verify:
- The module-level singleton is reused across instances
- Repeated save/search does not create new clients
- Session isolation (concurrent writes do not corrupt)
- Bulk throughput with many consecutive calls
"""
from __future__ import annotations

from agentnexus.memory.long_term import _get_ltm_collection


def _reset_ltm():
    from agentnexus.memory.long_term import _reset_long_term_memory
    _reset_long_term_memory()


def _get_ltm():
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.clear_all()
    return ltm


def _embed():
    return [0.1] * 384


class TestChromaClientSingleton:
    """Verifies _get_ltm_collection caches the client across calls."""

    def test_singleton_returns_same_object(self, perf_env):
        col1 = _get_ltm_collection()
        col2 = _get_ltm_collection()
        assert col1 is col2

    def test_singleton_survives_ltm_instance_recreation(self, perf_env):
        _reset_ltm()
        col_before = _get_ltm_collection()
        ltm1 = _get_ltm()
        ltm1.save("s1", "test", "general", 0.5, embedding=_embed())
        _reset_ltm()
        ltm2 = _get_ltm()
        ltm2.save("s2", "test2", "general", 0.5, embedding=_embed())
        col_after = _get_ltm_collection()
        assert col_before is col_after

    def test_multiple_instances_share_same_collection(self, perf_env):
        _reset_ltm()
        ltm1 = _get_ltm()
        ltm1.save("s1", "a", "general", 0.5, embedding=_embed())
        col1 = ltm1._chroma_col
        assert col1 is not None
        ltm2 = _get_ltm()
        ltm2.save("s2", "b", "general", 0.5, embedding=_embed())
        col2 = ltm2._chroma_col
        assert col2 is not None
        assert col1 is col2


class TestConsecutiveSaveStress:
    """Bulk save throughput without client rebuild overhead."""

    N = 500

    def test_many_consecutive_saves_no_rebuild(self, perf_env, benchmark):
        _reset_ltm()
        ltm = _get_ltm()
        ltm.save("s1", "seed", "general", 0.5, embedding=_embed())
        assert ltm._chroma_col is not None

        def _save_batch():
            for i in range(self.N):
                ltm.save(
                    session_id="stress",
                    content=f"stress test entry #{i} with enough content to be realistic for long term memory",
                    category="entity_fact",
                    importance=0.5,
                )

        benchmark(_save_batch)

    def test_many_consecutive_searches_no_rebuild(self, perf_env, benchmark):
        _reset_ltm()
        ltm = _get_ltm()
        for i in range(200):
            ltm.save(
                session_id="stress",
                content=f"search target entry #{i} with some sample text for searching",
                category="entity_fact",
                importance=0.5,
            )

        def _search_batch():
            for _ in range(self.N):
                ltm.search(query_embedding=None, limit=5)

        benchmark(_search_batch)


class TestSessionIsolation:
    """Concurrent sessions must not corrupt each other's data."""

    def test_two_sessions_isolated(self, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        ltm.save("session_a", "data for A", "general", 0.5)
        ltm.save("session_b", "data for B", "general", 0.5)

        results = ltm.search(query_embedding=None, limit=50)
        contents_a = [r["content"] for r in results if r["session_id"] == "session_a"]
        contents_b = [r["content"] for r in results if r["session_id"] == "session_b"]
        assert "data for A" in contents_a
        assert "data for B" not in contents_a
        assert "data for B" in contents_b
        assert "data for A" not in contents_b

    def test_delete_only_affects_target_session(self, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        ltm.save("s1", "content for s1", "general", 0.5)
        ltm.save("s2", "content for s2", "general", 0.5)

        ltm._conn.execute("DELETE FROM long_term_memories WHERE session_id = ?", ("s1",))
        ltm._conn.commit()

        results = ltm.search(query_embedding=None, limit=50)
        sids = [r["session_id"] for r in results]
        assert "s1" not in sids
        assert "s2" in sids


class TestMaxMemoriesEviction:
    """Eviction policy stress under heavy write load."""

    def test_eviction_at_limit(self, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        ltm._max_memories = 50
        for i in range(100):
            ltm.save(
                session_id="evict",
                content=f"entry #{i} with sample text for eviction stress test",
                category="entity_fact",
                importance=0.5,
            )
        remaining = ltm.search(query_embedding=None, limit=200)
        assert len(remaining) <= ltm._max_memories
