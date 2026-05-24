import pytest

from agentnexus.memory.long_term import get_long_term_memory


@pytest.fixture
def ltm(temp_agentnexus_home):
    yield get_long_term_memory()


class TestLongTermMemory:
    def test_save_and_list_recent(self, ltm):
        ltm.save("sess-1", "用户喜欢简洁回答", category="user_preference", importance=0.9)
        ltm.save("sess-1", "用户是后端开发者", category="user_profile", importance=0.7)
        recent = ltm.list_recent(limit=5)
        assert len(recent) >= 2
        contents = [r["content"] for r in recent]
        assert any("简洁" in c for c in contents)
        assert any("后端" in c for c in contents)

    def test_save_duplicate_updates_importance(self, ltm):
        ltm.save("sess-1", "重复保存的测试", category="test", importance=0.3)
        ltm.save("sess-1", "重复保存的测试", category="test", importance=0.9)
        recent = ltm.list_recent(limit=10)
        matching = [r for r in recent if r["content"].startswith("重复保存")]
        assert len(matching) == 1
        assert matching[0]["importance"] >= 0.9

    def test_search_without_embedding(self, ltm):
        ltm.save("sess-1", "记忆A", category="cat_a")
        ltm.save("sess-1", "记忆B", category="cat_b")
        results = ltm.search(category="cat_a", limit=3)
        assert len(results) >= 1
        assert any(r["content"] == "记忆A" for r in results)

    def test_search_with_embedding(self, ltm):
        ltm.save(
            "sess-1", "python是很好的语言",
            category="knowledge",
            embedding=[0.1, 0.2, 0.3],
        )
        ltm.save(
            "sess-1", "java也是常用语言",
            category="knowledge",
            embedding=[0.5, 0.6, 0.7],
        )
        # search with embedding close to first
        results = ltm.search(
            query_embedding=[0.1, 0.2, 0.3],
            limit=3,
        )
        assert len(results) >= 1

    def test_search_does_not_return_results_below_threshold(self, ltm):
        ltm.save(
            "sess-1", "完全不相关的记忆",
            category="knowledge",
            embedding=[1.0, 0.0, 0.0],
        )
        # query embedding is orthogonal -> cosine similarity ≈ 0
        results = ltm.search(
            query_embedding=[0.0, 1.0, 0.0],
            min_similarity=0.99,
        )
        assert len(results) == 0

    def test_delete(self, ltm):
        ltm.save("sess-1", "待删除记忆", category="test")
        recent = ltm.list_recent(limit=1)
        assert len(recent) >= 1
        mem_id = recent[0]["id"]
        ltm.delete(mem_id)
        recent_after = ltm.list_recent(limit=10)
        assert all(r["id"] != mem_id for r in recent_after)

    def test_schema_table_created(self, ltm):
        cursor = ltm._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='long_term_memories'"
        )
        assert cursor.fetchone() is not None

    def test_list_recent_preserves_full_content(self, ltm):
        long_content = "A" * 200
        ltm.save("sess-1", long_content)
        recent = ltm.list_recent(limit=1)
        assert len(recent) == 1
        assert len(recent[0]["content"]) == 200

    def test_save_does_not_trigger_eviction_below_limit(self, ltm, monkeypatch):
        calls = []

        def fake_evict():
            calls.append(True)

        monkeypatch.setattr(ltm, "_evict_if_needed", fake_evict)
        ltm.save("sess-1", "普通记忆", category="test")
        assert calls == []
