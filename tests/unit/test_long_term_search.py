import pytest

import agentnexus.memory.long_term as ltm_module
from agentnexus.memory.long_term import (
    _get_ltm_collection,
    _reset_long_term_memory,
    get_long_term_memory,
)
from agentnexus.rag.chroma_client import _reset_chroma_client


def _fresh_ltm():
    ltm_module._ltm_collection = None
    _reset_chroma_client()
    _reset_long_term_memory()
    return get_long_term_memory()


@pytest.fixture
def ltm(temp_agentnexus_home):
    yield _fresh_ltm()


class TestSearch:
    """Tests for LongTermMemory.search()."""

    def test_search_without_embedding(self, ltm):
        ltm.save("sess-1", "记忆A", category="cat_a")
        ltm.save("sess-1", "记忆B", category="cat_b")
        results = ltm.search(category="cat_a", limit=3)
        assert len(results) >= 1
        assert any(r["content"] == "记忆A" for r in results)

    def test_search_with_embedding(self, ltm):
        ltm.save("sess-1", "python语言", category="knowledge", embedding=[0.1, 0.2, 0.3])
        ltm.save("sess-1", "java语言", category="knowledge", embedding=[0.5, 0.6, 0.7])
        results = ltm.search(query_embedding=[0.1, 0.2, 0.3], limit=3)
        assert len(results) >= 1

    def test_search_respects_limit(self, ltm):
        for i in range(10):
            v = 0.1 + i * 0.01
            ltm.save("sess-1", f"记忆{i}", category="test", embedding=[v, v, v])
        results = ltm.search(query_embedding=[0.1, 0.1, 0.1], limit=3)
        assert len(results) <= 3

    def test_search_respects_min_similarity(self, ltm):
        ltm.save("sess-1", "完全不相关", category="knowledge", embedding=[1.0, 0.0, 0.0])
        results = ltm.search(query_embedding=[0.0, 1.0, 0.0], min_similarity=0.99)
        assert len(results) == 0

    def test_search_with_category_filter(self, ltm):
        ltm.save("sess-1", "python知识", category="knowledge", embedding=[0.1, 0.2, 0.3])
        ltm.save("sess-1", "用户偏好", category="preference", embedding=[0.5, 0.6, 0.7])
        results = ltm.search(query_embedding=[0.1, 0.2, 0.3], category="knowledge", limit=5)
        assert len(results) >= 1
        assert all(r["category"] == "knowledge" for r in results)

    def test_search_results_have_score_field(self, ltm):
        ltm.save("sess-1", "测试内容", category="test", embedding=[0.1, 0.2, 0.3])
        results = ltm.search(query_embedding=[0.1, 0.2, 0.3], limit=5)
        assert len(results) >= 1
        assert all("_score" in r for r in results)

    def test_search_results_are_sorted_by_score_descending(self, ltm):
        ltm.save("sess-1", "非常相关", category="test", embedding=[0.1, 0.1, 0.1])
        ltm.save("sess-1", "不太相关", category="test", embedding=[0.9, 0.9, 0.9])
        results = ltm.search(query_embedding=[0.1, 0.1, 0.1], limit=5)
        scores = [r["_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_without_query_embedding_and_category(self, ltm):
        ltm.save("sess-1", "记忆A", category="cat_a")
        ltm.save("sess-1", "记忆B", category="cat_b")
        results = ltm.search(limit=5)
        assert len(results) >= 2


class TestSearchFallback:
    """Tests for the fallback path in search() when ChromaDB query fails or returns empty."""

    def test_search_fallback_when_chromadb_query_fails(self, ltm, monkeypatch):
        ltm.save("sess-1", "python语言", category="knowledge", embedding=[0.2, 0.3, 0.1])
        ltm.save("sess-1", "非常相似的内容", category="knowledge", embedding=[0.21, 0.31, 0.11])
        monkeypatch.setattr(ltm._chroma_col, "query", lambda **kw: (_ for _ in ()).throw(RuntimeError("fail")))
        results = ltm.search(query_embedding=[0.2, 0.3, 0.1], limit=5, min_similarity=0.0)
        assert len(results) >= 1

    def test_search_fallback_when_chromadb_returns_empty(self, ltm, monkeypatch):
        ltm.save("sess-1", "测试内容", category="test", embedding=[0.1, 0.2, 0.3])
        monkeypatch.setattr(ltm._chroma_col, "query", lambda **kw: {"ids": [[]], "distances": [[]]})
        results = ltm.search(query_embedding=[0.1, 0.2, 0.3], limit=5, min_similarity=0.0)
        assert len(results) >= 1


class TestFallbackCosineSearch:
    """Tests for LongTermMemory._fallback_cosine_search()."""

    def test_fallback_returns_results(self, ltm):
        ltm.save("sess-1", "内容A", category="test", embedding=[0.1, 0.2, 0.3])
        ltm.save("sess-1", "内容B", category="test", embedding=[0.15, 0.25, 0.35])
        results = ltm._fallback_cosine_search([0.1, 0.2, 0.3], None, limit=5, min_similarity=0.0)
        assert len(results) >= 1

    def test_fallback_returns_empty_when_no_entries(self, ltm):
        results = ltm._fallback_cosine_search([0.1, 0.2, 0.3], None, limit=5, min_similarity=0.0)
        assert results == []

    def test_fallback_returns_empty_when_no_embeddings_in_chromadb(self, ltm):
        ltm.save("sess-1", "无嵌入内容", category="test")
        ltm._ensure_chroma()
        results = ltm._fallback_cosine_search([0.1, 0.2, 0.3], None, limit=5, min_similarity=0.0)
        assert results == []

    def test_fallback_respects_limit(self, ltm):
        for i in range(5):
            v = 0.1 + i * 0.01
            ltm.save("sess-1", f"内容{i}", category="test", embedding=[v, v, v])
        results = ltm._fallback_cosine_search([0.1, 0.1, 0.1], None, limit=2, min_similarity=0.0)
        assert len(results) == 2

    def test_fallback_respects_min_similarity(self, ltm):
        ltm.save("sess-1", "完全相同", category="test", embedding=[1.0, 0.0, 0.0, 0.0])
        ltm.save("sess-1", "完全不同", category="test", embedding=[0.0, 1.0, 0.0, 0.0])
        ltm._ensure_chroma()
        results = ltm._fallback_cosine_search(
            [1.0, 0.0, 0.0, 0.0], None, limit=5, min_similarity=0.99
        )
        assert len(results) == 1
        assert results[0]["content"] == "完全相同"

    def test_fallback_sorts_by_score_descending(self, ltm):
        ltm.save("sess-1", "非常相似", category="test", embedding=[0.1, 0.1, 0.1])
        ltm.save("sess-1", "不太相似", category="test", embedding=[0.9, 0.9, 0.9])
        results = ltm._fallback_cosine_search([0.1, 0.1, 0.1], None, limit=5, min_similarity=0.0)
        scores = [r["_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_fallback_results_have_score_field(self, ltm):
        ltm.save("sess-1", "测试内容", category="test", embedding=[0.1, 0.2, 0.3])
        results = ltm._fallback_cosine_search([0.1, 0.2, 0.3], None, limit=5, min_similarity=0.0)
        assert len(results) >= 1
        assert all("_score" in r for r in results)

    def test_fallback_with_category_filter(self, ltm):
        ltm.save("sess-1", "相关知识", category="knowledge", embedding=[0.1, 0.2, 0.3])
        ltm.save("sess-1", "偏好信息", category="preference", embedding=[0.1, 0.2, 0.3])
        results = ltm._fallback_cosine_search(
            [0.1, 0.2, 0.3], category="knowledge", limit=5, min_similarity=0.0
        )
        assert len(results) >= 1
        assert all(r["category"] == "knowledge" for r in results)

    def test_fallback_handles_zero_norm_query(self, ltm):
        ltm.save("sess-1", "测试内容", category="test", embedding=[0.1, 0.2, 0.3])
        results = ltm._fallback_cosine_search([0.0, 0.0, 0.0], None, limit=5, min_similarity=0.0)
        assert results == []


class TestEviction:
    """Tests for LongTermMemory._evict_if_needed()."""

    def test_eviction_triggers_when_over_limit(self, ltm):
        ltm._max_memories = 3
        ltm.save("sess-1", "记忆1", category="a", importance=0.1)
        ltm.save("sess-1", "记忆2", category="b", importance=0.2)
        ltm.save("sess-1", "记忆3", category="c", importance=0.9)
        assert ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0] == 3
        ltm.save("sess-1", "记忆4", category="d", importance=0.8)
        assert ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0] == 3

    def test_eviction_removes_lowest_importance_first(self, ltm):
        ltm._max_memories = 3
        ltm.save("sess-1", "低重要性", category="a", importance=0.1)
        ltm.save("sess-1", "中重要性", category="b", importance=0.5)
        ltm.save("sess-1", "高重要性", category="c", importance=0.9)
        ltm.save("sess-1", "新高重要性", category="d", importance=0.8)
        remaining = ltm.list_recent(limit=10)
        contents = [r["content"] for r in remaining]
        assert "低重要性" not in contents
        assert "中重要性" in contents
        assert "高重要性" in contents
        assert "新高重要性" in contents

    def test_no_eviction_when_under_limit(self, ltm):
        ltm._max_memories = 10
        ltm.save("sess-1", "记忆1", category="a", importance=0.1)
        ltm.save("sess-1", "记忆2", category="b", importance=0.2)
        ltm.save("sess-1", "记忆3", category="c", importance=0.3)
        assert ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0] == 3

    def test_eviction_does_not_error_on_empty(self, ltm):
        ltm._max_memories = 0
        ltm._evict_if_needed()


class TestDelete:
    """Tests for LongTermMemory.delete()."""

    def test_delete_removes_entry(self, ltm):
        ltm.save("sess-1", "待删除内容", category="test")
        mem_id = ltm.list_recent(limit=1)[0]["id"]
        ltm.delete(mem_id)
        results = ltm.list_recent(limit=10)
        assert all(r["id"] != mem_id for r in results)

    def test_delete_also_removes_from_chromadb(self, ltm, monkeypatch):
        ltm.save("sess-1", "待删除内容", category="test", embedding=[0.1, 0.2, 0.3])
        mem_id = ltm.list_recent(limit=1)[0]["id"]
        chroma_deleted = []
        monkeypatch.setattr(ltm._chroma_col, "delete", lambda ids=None, **kw: chroma_deleted.extend(ids or []))
        ltm.delete(mem_id)
        assert len(chroma_deleted) >= 1

    def test_delete_nonexistent_id(self, ltm):
        ltm.delete(99999)

    def test_delete_entry_without_chromaid(self, ltm):
        ltm.save("sess-1", "无嵌入内容", category="test")
        mem_id = ltm.list_recent(limit=1)[0]["id"]
        ltm.delete(mem_id)
        results = ltm.list_recent(limit=10)
        assert all(r["id"] != mem_id for r in results)


class TestClearAll:
    """Tests for LongTermMemory.clear_all()."""

    def test_clear_all_removes_all_entries(self, ltm):
        ltm.save("sess-1", "记忆1", category="a")
        ltm.save("sess-1", "记忆2", category="b")
        ltm.save("sess-1", "记忆3", category="c")
        ltm.clear_all()
        count = ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0]
        assert count == 0

    def test_clear_all_resets_write_counter(self, ltm):
        ltm.save("sess-1", "记忆1", category="a")
        ltm.save("sess-1", "记忆2", category="b")
        ltm.clear_all()
        assert ltm._write_counter == 0

    def test_clear_all_empty_database(self, ltm):
        ltm.clear_all()
        count = ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0]
        assert count == 0


class TestCleanupExpired:
    """Tests for LongTermMemory._cleanup_expired()."""

    def test_cleanup_expired_removes_old_entries(self, ltm):
        ltm.save("sess-1", "旧记忆", category="test", importance=0.5)
        ltm._conn.execute(
            "UPDATE long_term_memories SET created_at = datetime('now', '-100 days') WHERE content = ?",
            ("旧记忆",)
        )
        ltm._conn.commit()
        ltm.save("sess-1", "新记忆", category="test", importance=0.5)
        ltm._ttl_days = 30
        ltm._cleanup_expired()
        count = ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0]
        assert count == 1
        remaining = ltm.list_recent(limit=10)
        assert remaining[0]["content"] == "新记忆"

    def test_cleanup_expired_preserves_fresh_entries(self, ltm):
        ltm.save("sess-1", "新记忆", category="test", importance=0.5)
        ltm._ttl_days = 90
        ltm._cleanup_expired()
        count = ltm._conn.execute("SELECT COUNT(*) FROM long_term_memories").fetchone()[0]
        assert count >= 1

    def test_cleanup_expired_handles_empty_database(self, ltm):
        ltm._cleanup_expired()


class TestEnsureChroma:
    """Tests for LongTermMemory._ensure_chroma()."""

    def test_ensure_chroma_initializes_collection(self, ltm):
        assert ltm._chroma_col is None
        ltm._ensure_chroma()
        assert ltm._chroma_col is not None

    def test_ensure_chroma_idempotent(self, ltm):
        ltm._ensure_chroma()
        col1 = ltm._chroma_col
        ltm._ensure_chroma()
        assert ltm._chroma_col is col1


class TestMigrate:
    """Tests for LongTermMemory._migrate()."""

    def test_migrate_adds_missing_columns(self, ltm):
        ltm._conn.execute("DROP TABLE IF EXISTS long_term_memories")
        ltm._conn.execute("""
            CREATE TABLE long_term_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                content TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        ltm._conn.commit()
        ltm._migrate()
        cur = ltm._conn.execute("PRAGMA table_info(long_term_memories)")
        cols = {r["name"] for r in cur.fetchall()}
        assert "chroma_id" in cols
        assert "metadata_json" in cols

    def test_migrate_idempotent(self, ltm):
        ltm._migrate()
        cur = ltm._conn.execute("PRAGMA table_info(long_term_memories)")
        cols = {r["name"] for r in cur.fetchall()}
        assert "chroma_id" in cols
        assert "metadata_json" in cols


class TestLtmCollection:
    """Tests for _get_ltm_collection() singleton."""

    def test_get_ltm_collection_singleton(self, temp_agentnexus_home):
        _reset_chroma_client()
        ltm_module._ltm_collection = None
        col1 = _get_ltm_collection()
        col2 = _get_ltm_collection()
        assert col1 is col2

    def test_get_ltm_collection_name(self, temp_agentnexus_home):
        _reset_chroma_client()
        ltm_module._ltm_collection = None
        col = _get_ltm_collection()
        assert col.name == "long_term_memories"
