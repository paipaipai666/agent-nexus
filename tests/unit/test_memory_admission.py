"""Admission-control tests: whitelist gate, ADD-only extraction, curator quarantine."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_mgr(memory_llm_gate: bool = False, llm=None):
    from agentnexus.memory.compaction_engine import CompactionEngine
    from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline
    from agentnexus.memory.manager import MemoryManager

    mgr = MemoryManager.__new__(MemoryManager)
    mgr._engine = CompactionEngine(mgr)
    mgr._pipeline = MemoryExtractionPipeline(mgr)
    mgr.session_id = "test"
    mgr._settings = SimpleNamespace(memory_llm_gate=memory_llm_gate)
    mgr._llm = llm or MagicMock()
    return mgr


class TestWhitelistGateRules:
    @pytest.mark.parametrize("q", [
        "记住我不吃香菜",
        "我叫张伟",
        "我是一个后端工程师",
        "我喜欢简洁的回答",
        "我讨厌无意义的道歉",
        "以后回答简短一点",
        "别再用 npm 了",
        "我总是先写测试",
        "我习惯用 Docker 部署",
        "我偏好函数式编程",
    ])
    def test_strong_signals_pass(self, q):
        mgr = _make_mgr()
        assert mgr._should_extract_rules(q, "好的") == "yes"

    @pytest.mark.parametrize("q", [
        "这个怎么用？",
        "Python 列表排序怎么做?",
        "它能处理并发吗",
        "需要我确认呢",
    ])
    def test_questions_rejected(self, q):
        mgr = _make_mgr()
        assert mgr._should_extract_rules(q, "这是一个足够长的回答内容") == "no"

    def test_transactional_short_answer_rejected(self):
        mgr = _make_mgr()
        assert mgr._should_extract_rules("帮我移一下文件", "已移动") == "no"

    def test_uncertain_survives_rules(self):
        mgr = _make_mgr()
        assert mgr._should_extract_rules("部署这个服务", "已完成部署，共 3 个实例") == "uncertain"


class TestDefaultDeny:
    def test_uncertain_denied_without_llm_call(self):
        llm = MagicMock()
        mgr = _make_mgr(memory_llm_gate=False, llm=llm)
        pipeline = mgr._pipeline
        assert pipeline.should_extract("部署这个服务", "已完成部署，共 3 个实例") is False
        llm.think.assert_not_called()

    def test_llm_gate_opt_in(self):
        llm = MagicMock()
        llm.think.return_value = "yes"
        mgr = _make_mgr(memory_llm_gate=True, llm=llm)
        pipeline = mgr._pipeline
        assert pipeline.should_extract("部署这个服务", "已完成部署，共 3 个实例") is True
        llm.think.assert_called_once()

    def test_strong_signal_never_touches_gate(self):
        llm = MagicMock()
        mgr = _make_mgr(memory_llm_gate=False, llm=llm)
        pipeline = mgr._pipeline
        assert pipeline.should_extract("我喜欢简洁的回答", "好的") is True
        llm.think.assert_not_called()


class TestAddOnlyExtraction:
    def test_no_conflict_llm_call_no_supersede(self):
        from agentnexus.memory.extraction import extract_and_save_memories

        llm = MagicMock()
        llm.think.return_value = '{"preference": [{"content": "用户喜欢简洁的回答风格"}]}'
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1] * 8
        long_term = MagicMock()
        # A near-duplicate exists in the former conflict band (0.70-0.90);
        # ADD-only must neither judge nor supersede it.
        long_term.search.return_value = []
        long_term.save.return_value = 42

        extract_and_save_memories(
            llm=llm, embed_model=embed_model, long_term=long_term,
            session_id="t", question="回答风格讨论一下", answer="好的，明白了",
        )
        assert llm.think.call_count == 1  # extraction only, no conflict check
        long_term.mark_superseded.assert_not_called()
        long_term.save.assert_called_once()

    def test_dedup_still_active(self):
        from agentnexus.memory.extraction import extract_and_save_memories

        llm = MagicMock()
        llm.think.return_value = '{"fact": [{"content": "用户使用 pnpm 作为包管理器"}]}'
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1] * 8
        long_term = MagicMock()
        long_term.search.return_value = [{"id": 1, "content": "dup", "_score": 0.95}]

        extract_and_save_memories(
            llm=llm, embed_model=embed_model, long_term=long_term,
            session_id="t", question="包管理器讨论", answer="pnpm 很好",
        )
        long_term.save.assert_not_called()


class TestPendingQuarantine:
    def test_add_and_list_pending(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        pid = ltm.add_pending("用户偏好简洁回答", scope="user", category="preference")
        assert pid is not None
        rows = ltm.list_pending()
        assert len(rows) == 1
        assert rows[0]["content"] == "用户偏好简洁回答"
        assert rows[0]["status"] == "pending"

    def test_add_pending_dedup(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        first = ltm.add_pending("同样的内容", scope="user")
        second = ltm.add_pending("同样的内容", scope="user")
        assert first is not None
        assert second is None
        assert len(ltm.list_pending()) == 1

    def test_approve_user_scope_saves_and_supersedes(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        old_id = ltm.save(session_id="s", content="旧的零散笔记", category="note", importance=0.5)
        pid = ltm.add_pending("归纳后的用户偏好", scope="user", category="preference",
                              importance=0.85, source_ids=[old_id])
        result = ltm.approve_pending(pid)
        assert result["status"] == "approved"
        new_id = result["memory_id"]
        row = ltm._conn.execute(
            "SELECT superseded_by FROM long_term_memories WHERE id = ?", (old_id,)
        ).fetchone()
        assert row["superseded_by"] == new_id
        assert ltm.get_pending(pid)["status"] == "approved"
        # The approved memory is now in LTM
        saved = ltm._conn.execute(
            "SELECT content, category FROM long_term_memories WHERE id = ?", (new_id,)
        ).fetchone()
        assert saved["content"] == "归纳后的用户偏好"
        assert saved["category"] == "preference"

    def test_approve_project_scope_writes_agentnexus(self, temp_agentnexus_home, tmp_path):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        pid = ltm.add_pending("构建命令是 just build", scope="project", kind="memo",
                              workspace_path=str(tmp_path))
        result = ltm.approve_pending(pid)
        assert result["status"] == "approved"
        assert result["scope"] == "project"
        memo = (tmp_path / ".agentnexus" / "memo.md").read_text(encoding="utf-8")
        assert "just build" in memo

    def test_approve_twice_rejected(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        pid = ltm.add_pending("某条偏好", scope="user")
        assert ltm.approve_pending(pid)["status"] == "approved"
        assert ltm.approve_pending(pid)["status"] == "error"

    def test_approve_missing_id(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        assert ltm.approve_pending(99999)["status"] == "error"

    def test_stats_counts(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        ltm.save(session_id="s", content="事实一", category="fact")
        ltm.save(session_id="s", content="偏好一", category="preference")
        ltm.add_pending("待确认一", scope="user")
        s = ltm.stats()
        assert s["total"] == 2
        assert s["by_category"] == {"fact": 1, "preference": 1}
        assert s["never_accessed"] == 2
        assert s["pending"] == 1


class TestCurator:
    def test_proposes_to_pending_not_ltm(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory
        from agentnexus.memory.reflection import run_reflection

        ltm = get_long_term_memory()
        for i in range(6):
            ltm.save(session_id="s1", content=f"任务进展记录{i}：完成了模块{i}的联调",
                     category="note", importance=0.6)

        llm = MagicMock()
        llm.think.return_value = json.dumps({"patterns": [
            {"content": "用户持续推进模块化重构工作", "scope": "user",
             "category": "fact", "importance": 0.8},
            {"content": "该项目采用模块化架构组织代码", "scope": "project",
             "kind": "memo", "importance": 0.8},
        ]})
        embed_model = MagicMock()
        embed_model.encode.side_effect = Exception("no embed in test")

        result = run_reflection(llm=llm, embed_model=embed_model, long_term=ltm, days=30)
        assert result["patterns_proposed"] == 2
        assert result["patterns_saved"] == 0

        pending = ltm.list_pending()
        assert len(pending) == 2
        # No conversation_sessions rows here → project scope falls back to user
        assert all(p["scope"] == "user" for p in pending)
        # Source notes untouched until approval
        s = ltm.stats()
        assert s["total"] == 6
        assert s["superseded"] == 0
        assert s["pending"] == 2

    def test_not_enough_notes_skips_llm(self, temp_agentnexus_home):
        from agentnexus.memory.long_term import get_long_term_memory
        from agentnexus.memory.reflection import run_reflection

        ltm = get_long_term_memory()
        ltm.save(session_id="s1", content="唯一一条笔记内容", category="note")
        llm = MagicMock()
        result = run_reflection(llm=llm, embed_model=MagicMock(), long_term=ltm, days=30)
        assert result["patterns_found"] == 0
        llm.think.assert_not_called()
