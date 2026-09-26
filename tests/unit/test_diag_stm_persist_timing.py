"""STM 落盘时机诊断（只证明事实，不改产品）。

问题：agent 生成最终答案过程中后端崩溃，是否会丢 STM？
答案取决于「什么算落盘」以及「何时写」。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.services.turn import TurnRuntime


class TestStmDurabilityFacts:
    def test_production_memory_manager_stm_has_no_wal(self):
        """默认 MemoryManager 的 STM 不带 WAL → 进程崩溃 = 内存消息全丢。"""
        mm = MemoryManager(session_id="stm-wal", enable_long_term=False)
        assert mm.short_term._wal_path is None, (
            "生产路径未启用 STM WAL，崩溃无法从磁盘恢复"
        )

    def test_wal_only_flushes_every_five_appends_when_enabled(self, tmp_path: Path):
        """即使开了 WAL，也不是每条 append 都落盘（% 5）。"""
        wal = tmp_path / "stm.wal"
        stm = ShortTermMemory(wal_path=str(wal))
        stm.append("user", "m1")
        stm.append("assistant", "m2")
        assert not wal.exists(), "不足 5 次 append 时 WAL 尚未 flush"
        stm.append("tool", "m3")
        stm.append("system", "m4")
        stm.append("user", "m5")
        assert wal.exists(), "第 5 次 append 应触发 WAL flush"

    def test_durable_history_committed_at_turn_end_not_mid_answer(self):
        """version 落盘发生在 turn.finish/fail/cancel，不在流式回答过程中。"""
        memory = MagicMock()
        memory.short_term.get_all.return_value = [
            {"role": "user", "content": "介绍 AgentNexus"},
            {"role": "system", "content": "[最终答案] 前半段回答…"},
        ]
        version = MagicMock()
        turn = TurnRuntime(
            run_id="r1", session_id="s1", question="介绍 AgentNexus",
            memory_manager=memory, version_manager=version,
        )
        # 回答尚未结束：不应 commit
        assert version.commit_with_messages.call_count == 0
        turn.finish("完整答案")
        assert version.commit_with_messages.call_count == 1

    def test_crash_before_finish_leaves_no_answer_in_version_store(self):
        """模拟「答案写到一半进程没了」：没有 finish → version 从未收到本轮消息。"""
        memory = MagicMock()
        memory.short_term.get_all.return_value = [
            {"role": "user", "content": "介绍 AgentNexus"},
            {"role": "system", "content": "[最终答案] 半截…"},  # 只在内存 STM
        ]
        version = MagicMock()
        TurnRuntime(
            run_id="r1", session_id="s1", question="介绍 AgentNexus",
            memory_manager=memory, version_manager=version,
        )
        # 不调用 finish/fail/cancel —— 等价于崩溃
        assert version.commit_with_messages.call_count == 0
        # 进程死则 MemoryManager.short_term 一起消失；WAL 未开 → 无法恢复
        mm = MemoryManager(session_id="crash", enable_long_term=False)
        assert mm.short_term._wal_path is None
