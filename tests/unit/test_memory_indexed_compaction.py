"""分段索引压缩的单元测试（STM 原语 + segment_summarize + 引擎端到端）。

核心契约：索引 append-only——第二轮压缩不得把第一轮的条目重新喂给 LLM
（杜绝"摘要的摘要"复利丢失，A/B 实验实测单摘要路径细节召回仅 14-30%）。
"""
from unittest.mock import MagicMock

from agentnexus.memory.circuit_breaker import CircuitBreaker
from agentnexus.memory.compaction_engine import CompactionEngine, segment_summarize
from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline
from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


def _msgs(n: int, prefix: str = "msg") -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{prefix}{i}"} for i in range(n)]


class TestCompactIndexedPrimitive:
    def test_entries_replace_raw_middle_tail_kept(self):
        stm = ShortTermMemory()
        for m in _msgs(20):
            stm.append(m["role"], m["content"])
        stm.compact_indexed([(0, 8, "段1摘要"), (8, 14, "段2摘要")], keep_recent=6)
        msgs = stm.get_all()
        assert len(msgs) == 2 + 6
        assert "段1摘要" in msgs[0]["content"]
        assert msgs[0]["metadata"]["memory_index"] is True
        assert msgs[0]["metadata"]["seg_start"] == 0
        # 尾部原文保留角色结构
        assert msgs[-1]["content"] == "msg19"
        assert msgs[-1]["role"] == "assistant"
        # 中间原文已移除
        joined = "\n".join(m["content"] for m in msgs)
        assert "msg3\n" not in joined

    def test_second_compaction_preserves_old_entries(self):
        stm = ShortTermMemory()
        for m in _msgs(20):
            stm.append(m["role"], m["content"])
        stm.compact_indexed([(0, 14, "第一轮摘要")], keep_recent=6)
        first_entry = stm.get_all()[0]
        # 追加 10 条新消息后再压一轮
        for m in _msgs(10, "new"):
            stm.append(m["role"], m["content"])
        stm.compact_indexed([(14, 26, "第二轮摘要")], keep_recent=6)
        msgs = stm.get_all()
        assert msgs[0] is first_entry, "旧索引条目必须原样保留"
        assert any("第二轮摘要" in m["content"] for m in msgs)
        assert len([m for m in msgs if m.get("metadata", {}).get("memory_index")]) == 2


class TestSegmentSummarize:
    def test_segments_cover_all_but_tail(self):
        llm = MagicMock()
        llm.think.side_effect = lambda msgs, silent=True: f"小结:{msgs[0]['content'][:20]}"
        result = segment_summarize(_msgs(26), llm, keep_recent=6, seg_size=10)
        assert result is not None
        assert [(s, e) for s, e, _ in result.entries] == [(0, 10), (10, 20)]
        # 尾部 6 条（20-25）不索引
        assert all("msg25" not in s for _, _, s in result.entries)
        # 归档清单覆盖全部 20 条被索引消息，全局序号从 0 起
        assert [i for i, _ in result.archived] == list(range(20))
        assert result.archived[0][1]["content"] == "msg0"

    def test_numbering_resumes_after_existing_index(self):
        llm = MagicMock()
        llm.think.return_value = "新段小结"
        existing = [{"role": "system", "content": "[历史索引 消息1-14] 旧",
                     "metadata": {"memory_index": True, "seg_start": 0, "seg_end": 14}}]
        result = segment_summarize(existing + _msgs(10), llm,
                                   keep_recent=2, seg_size=4)
        assert result is not None
        assert result.entries[0][0] == 14, "序号必须从既有索引续排"
        assert result.archived[0][0] == 14, "归档序号同样从既有索引续排"
        # LLM 输入不得包含旧索引文本（无复利的关键断言）
        for c in llm.think.call_args_list:
            assert "旧" not in c[0][0][0]["content"], "已有索引不得重新喂给 LLM"

    def test_segment_failure_falls_back_to_excerpt(self):
        llm = MagicMock()
        llm.think.side_effect = ["成功小结", ""]
        result = segment_summarize(_msgs(8), llm, keep_recent=0, seg_size=4)
        assert result is not None
        assert result.entries[0][2] == "成功小结"
        assert "[未压缩原文]" in result.entries[1][2], "失败段降级为截断原文，信息不得凭空消失"

    def test_all_segments_fail_returns_none(self):
        llm = MagicMock()
        llm.think.return_value = ""
        assert segment_summarize(_msgs(8), llm, keep_recent=0, seg_size=4) is None

    def test_llm_exception_treated_as_segment_failure(self):
        llm = MagicMock()
        llm.think.side_effect = [RuntimeError("boom"), "好"]
        result = segment_summarize(_msgs(8), llm, keep_recent=0, seg_size=4)
        assert result is not None and "[未压缩原文]" in result.entries[0][2]

    def test_custom_instructions_prepended(self):
        llm = MagicMock()
        llm.think.return_value = "ok"
        segment_summarize(_msgs(6), llm, keep_recent=0, seg_size=4,
                          custom_instructions="重点关注数据库")
        assert "重点关注数据库" in llm.think.call_args_list[0][0][0][0]["content"]

    def test_nothing_to_index_returns_empty(self):
        llm = MagicMock()
        result = segment_summarize(_msgs(5), llm, keep_recent=6, seg_size=4)
        assert result is not None and result.entries == [] and result.archived == []
        llm.think.assert_not_called()


class TestEngineIndexedCompaction:
    def _make_mgr(self, llm):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._engine = CompactionEngine(mgr)
        mgr._pipeline = MemoryExtractionPipeline(mgr)
        mgr.short_term = ShortTermMemory()
        mgr._llm = llm
        mgr._embed_model = None
        mgr.long_term = None
        mgr._settings = MagicMock()
        mgr._settings.snip_enabled = False
        mgr._settings.time_microcompact_interval = 0
        mgr._settings.transcript_enabled = False
        mgr._settings.memory_index_segment_size = 4
        mgr._settings.post_compact_max_files = 0
        mgr._settings.post_compact_token_per_file = 0
        mgr._settings.post_compact_token_budget = 0
        mgr._settings.offload_enabled = False
        mgr._engine.ctx_max = 128000
        mgr._engine.compact_threshold = 120000
        mgr._engine.circuit = CircuitBreaker(failure_threshold=3, exponential_backoff=True)
        mgr._engine.microcompacts_since_open = 0
        mgr._engine.compacting = False
        mgr._engine.snip_freed_tokens = 0
        mgr._engine.recent_reads = []
        mgr._engine.last_api_call_ts = 0.0
        mgr._engine.on_compact = None
        mgr._engine.on_after_compact = None
        mgr._engine.transcript_dir = "/tmp"
        mgr.session_id = "test"
        return mgr

    def test_compact_produces_index_entries_and_tail(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000)
        llm = MagicMock()
        llm.think.side_effect = lambda msgs, silent=True: "段小结:" + msgs[0]["content"][:30]
        mgr = self._make_mgr(llm)
        for i in range(20):
            mgr.short_term.append("user", f"消息{i}")
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        msgs = mgr.short_term.get_all()
        index_entries = [m for m in msgs if m.get("metadata", {}).get("memory_index")]
        # 20 条 - 6 尾 = 14 条待索引，seg_size=4 → 4 段
        assert len(index_entries) == 4
        assert msgs[-1]["content"] == "消息19"
        assert llm.think.call_count == 4

    def test_second_compaction_does_not_refeed_old_entries(self, monkeypatch):
        """无复利的关键端到端断言：第二轮 LLM 输入不得含第一轮条目文本。"""
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000)
        llm = MagicMock()
        llm.think.side_effect = lambda msgs, silent=True: "第一轮产出物"
        mgr = self._make_mgr(llm)
        for i in range(20):
            mgr.short_term.append("user", f"消息{i}")
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        # 第二轮：换一批消息，捕获 prompt
        llm.think.reset_mock()
        llm.think.side_effect = lambda msgs, silent=True: "第二轮产出物"
        for i in range(20, 36):
            mgr.short_term.append("user", f"消息{i}")
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        for c in llm.think.call_args_list:
            assert "第一轮产出物" not in c[0][0][0]["content"]
        # 旧条目仍在且未被改写
        entries = [m for m in mgr.short_term.get_all()
                   if m.get("metadata", {}).get("memory_index")]
        assert any("第一轮产出物" in m["content"] for m in entries)
        assert any("第二轮产出物" in m["content"] for m in entries)

    def test_all_segments_fail_aborts_without_destruction(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000)
        llm = MagicMock()
        llm.think.return_value = ""
        mgr = self._make_mgr(llm)
        for i in range(20):
            mgr.short_term.append("user", f"消息{i}")
        mgr.short_term._token_count = 0
        result = mgr.maybe_compact()
        assert result == 0
        assert len(mgr.short_term.get_all()) == 20, "全部失败时上下文不得被破坏"
        assert mgr._engine.circuit.failure_count == 1

    def test_archive_written_and_index_folded(self, monkeypatch, tmp_path):
        """归档先行：折叠后原文仍在 history 文件里，且序号与索引区间一致。"""
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000)
        llm = MagicMock()
        llm.think.side_effect = lambda msgs, silent=True: "段小结内容" * 30  # 长条目逼出折叠
        mgr = self._make_mgr(llm)
        mgr._engine.history_dir = str(tmp_path)
        mgr._settings.memory_index_max_tokens = 60  # 极小预算 → 折叠
        for i in range(20):
            mgr.short_term.append("user", f"重要消息{i} " + "内容" * 20)
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        msgs = mgr.short_term.get_all()
        dirs = [m for m in msgs if m.get("metadata", {}).get("archive_dir")]
        assert dirs, "超预算必须产生归档目录"
        assert "history_search" in dirs[0]["content"]
        # 归档文件存在且行数=被索引的消息数（20-6=14），序号 0-13
        hfile = tmp_path / "test.jsonl"
        assert hfile.exists()
        import json as _json
        lines = [_json.loads(line) for line in hfile.read_text(encoding="utf-8").splitlines()]
        assert [r["i"] for r in lines] == list(range(14))
        assert "重要消息0" in lines[0]["content"]

    def test_numbering_continues_after_fold(self, monkeypatch, tmp_path):
        """折叠后再次压缩：序号从保留条目的最大 seg_end 续排，不回退。"""
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000)
        llm = MagicMock()
        llm.think.side_effect = lambda msgs, silent=True: "段小结" + "字" * 40
        mgr = self._make_mgr(llm)
        mgr._engine.history_dir = str(tmp_path)
        mgr._settings.memory_index_max_tokens = 80
        for i in range(20):
            mgr.short_term.append("user", f"第一批{i} " + "内容" * 20)
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        first_max_end = max(
            int(m["metadata"]["seg_end"])
            for m in mgr.short_term.get_all()
            if m.get("metadata", {}).get("memory_index"))
        for i in range(20, 40):
            mgr.short_term.append("user", f"第二批{i} " + "内容" * 20)
        mgr.short_term._token_count = 0
        mgr.maybe_compact()
        hfile = tmp_path / "test.jsonl"
        import json as _json
        lines = [_json.loads(line) for line in hfile.read_text(encoding="utf-8").splitlines()]
        indices = [r["i"] for r in lines]
        assert indices == sorted(indices), "归档序号必须全局单调"
        assert len(set(indices)) == len(indices), "归档序号不得重复"
        assert max(indices) >= first_max_end, "续排不得回退"


class TestFoldIndex:
    def _stm_with_entries(self, sizes: list[int]) -> ShortTermMemory:
        stm = ShortTermMemory()
        for i, size in enumerate(sizes):
            stm.append("system", f"[历史索引 消息{i * 10 + 1}-{i * 10 + 10}] " + "摘" * size,
                       metadata={"memory_index": True,
                                 "seg_start": i * 10, "seg_end": i * 10 + 10})
        stm.append("user", "最近的问题")
        return stm

    def test_under_budget_noop(self):
        stm = self._stm_with_entries([10, 10])
        assert stm.fold_index(100000) == 0
        assert len(stm.get_all()) == 3

    def test_folds_oldest_keeps_newest(self):
        stm = self._stm_with_entries([200, 200, 10])
        folded = stm.fold_index(60)  # 预算只够最后一条小条目
        assert folded == 2
        msgs = stm.get_all()
        dirs = [m for m in msgs if m.get("metadata", {}).get("archive_dir")]
        assert len(dirs) == 1
        assert "消息1-20" in dirs[0]["content"]
        assert "history_search" in dirs[0]["content"]
        # 最新条目和原文尾部保留
        assert any("摘摘" in m["content"] for m in msgs if not m.get("metadata", {}).get("archive_dir"))
        assert msgs[-1]["content"] == "最近的问题"

    def test_directory_not_refolded(self):
        stm = self._stm_with_entries([200, 200, 10])
        stm.fold_index(60)
        # 再来一轮：目录必须存活，不得被二次折叠
        stm.append("system", "[历史索引 消息31-40] " + "新" * 200,
                   metadata={"memory_index": True, "seg_start": 30, "seg_end": 40})
        stm.fold_index(60)
        dirs = [m for m in stm.get_all() if m.get("metadata", {}).get("archive_dir")]
        assert len(dirs) == 1, "目录合并而非叠加"

    def test_all_folded_when_budget_tiny(self):
        stm = self._stm_with_entries([200, 200])
        stm.fold_index(1)
        msgs = stm.get_all()
        dirs = [m for m in msgs if m.get("metadata", {}).get("archive_dir")]
        assert len(dirs) == 1
        assert "消息1-20" in dirs[0]["content"]
        assert msgs[-1]["content"] == "最近的问题"


class TestHistorySearch:
    def _home_with_history(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path))
        import agentnexus.core.config as cfg
        cfg._settings_cache = None
        hdir = tmp_path / "history"
        hdir.mkdir(parents=True)
        import json as _json
        records = [
            {"i": 0, "role": "user", "content": "错误码是 F_IC_SERVICE_QUERY_020，sellerId 为空"},
            {"i": 1, "role": "assistant", "content": "这是 sellerId 缺失导致的空指针"},
            {"i": 2, "role": "user", "content": "堆栈指向 ProductServiceImpl.java 第 147 行"},
            {"i": 3, "role": "assistant", "content": "147 行缺少非空校验"},
            {"i": 4, "role": "user", "content": "今天天气不错"},
        ]
        (hdir / "s1.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8")
        return hdir

    def test_keyword_hit_with_neighbors(self, tmp_path, monkeypatch):
        self._home_with_history(tmp_path, monkeypatch)
        from agentnexus.tools.history_search import history_search
        out = history_search("F_IC_SERVICE_QUERY_020")
        assert "F_IC_SERVICE_QUERY_020" in out
        assert "消息0" in out
        assert "消息1" in out, "命中应带相邻上下文"
        assert "今天天气不错" not in out

    def test_multi_term_or_matching(self, tmp_path, monkeypatch):
        self._home_with_history(tmp_path, monkeypatch)
        from agentnexus.tools.history_search import history_search
        out = history_search("空指针 147")
        assert "147" in out

    def test_no_hit_message(self, tmp_path, monkeypatch):
        self._home_with_history(tmp_path, monkeypatch)
        from agentnexus.tools.history_search import history_search
        assert "未找到" in history_search("不存在的关键词xyz")

    def test_empty_query_and_no_dir(self, tmp_path, monkeypatch):
        self._home_with_history(tmp_path, monkeypatch)
        from agentnexus.tools.history_search import history_search
        assert "请提供搜索关键词" in history_search("")
        (tmp_path / "history" / "s1.jsonl").unlink()
        (tmp_path / "history").rmdir()
        assert "暂无归档历史" in history_search("任何词")

    def test_output_capped(self, tmp_path, monkeypatch):
        hdir = self._home_with_history(tmp_path, monkeypatch)
        import json as _json
        big = [{"i": i, "role": "user", "content": f"关键词 {i} " + "长" * 300}
               for i in range(20)]
        (hdir / "big.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in big),
            encoding="utf-8")
        from agentnexus.tools.history_search import history_search
        out = history_search("关键词", max_results=20)
        assert len(out) <= 2200
        assert "截断" in out
