"""多 MemoryManager 并发写共享存储的正确性测试。

每个会话一个 MemoryManager，但底层共享同一 memory db（SQLite WAL）和
同一 Chroma 目录（LTM 单例）。跨实例无应用层协调，全靠存储层锁。
本文件验证 N 个实例并发 append + LTM save 不腐化、不丢数据。
"""
import concurrent.futures
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def shared_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path))
    import agentnexus.core.config as cfg
    cfg._settings_cache = None
    from agentnexus.memory.long_term import _reset_long_term_memory
    from agentnexus.storage.chroma import reset_storage_client
    _reset_long_term_memory()
    reset_storage_client()
    yield tmp_path


def _make_manager(session_id: str, workspace):
    from agentnexus.memory.manager import MemoryManager
    llm = MagicMock()
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    return MemoryManager(session_id, llm=llm, workspace_path=str(workspace))


class TestConcurrentMemoryManagers:
    def test_concurrent_append_and_ltm_save(self, shared_home):
        """4 个 MemoryManager 并发写：无异常、STM 各自计数正确、LTM 全量落库。"""
        n_managers, n_appends, n_ltm = 4, 50, 10
        managers = [
            _make_manager(f"conc_s{i}", shared_home / f"ws{i}")
            for i in range(n_managers)
        ]
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(idx: int, mgr):
            try:
                for j in range(n_appends):
                    mgr.append("user", f"s{idx} 消息 {j}")
                for j in range(n_ltm):
                    mgr.long_term.save(
                        session_id=f"conc_s{idx}",
                        content=f"s{idx} 的长期事实 {j}",
                        category="fact",
                        importance=0.8,
                        embedding=None,
                    )
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_managers) as pool:
            futures = [pool.submit(worker, i, m) for i, m in enumerate(managers)]
            concurrent.futures.wait(futures)

        assert not errors, f"并发写异常: {errors}"
        for i, mgr in enumerate(managers):
            stm_contents = [m["content"] for m in mgr.short_term.get_all()]
            assert sum(1 for c in stm_contents if c.startswith(f"s{i} 消息")) == n_appends, (
                f"s{i} STM 计数错误（可能被串写或丢失）"
            )
            assert not any(c.startswith("s") and f"s{i} 消息" not in c and "消息" in c
                           for c in stm_contents), f"s{i} STM 混入了别的会话消息"

        from agentnexus.memory.long_term import get_long_term_memory
        ltm = get_long_term_memory()
        n_rows = ltm._conn.execute(
            "SELECT COUNT(*) AS c FROM long_term_memories WHERE session_id LIKE 'conc_s%'"
        ).fetchone()["c"]
        assert n_rows == n_managers * n_ltm, (
            f"LTM 落库 {n_rows}, 期望 {n_managers * n_ltm}（并发写丢失）"
        )

    def test_concurrent_save_with_embeddings(self, shared_home):
        """带向量的并发 LTM 写：Chroma 侧也不出错、不串。"""
        n_managers, n_saves = 4, 8
        managers = [_make_manager(f"emb_s{i}", shared_home / f"ws{i}") for i in range(n_managers)]
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(idx: int, mgr):
            try:
                for j in range(n_saves):
                    vec = [float(idx), float(j)] + [0.0] * 6
                    mgr.long_term.save(
                        session_id=f"emb_s{idx}",
                        content=f"emb s{idx} fact {j}",
                        category="note",
                        importance=0.7,
                        embedding=vec,
                    )
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_managers) as pool:
            futures = [pool.submit(worker, i, m) for i, m in enumerate(managers)]
            concurrent.futures.wait(futures)

        assert not errors, f"并发向量写异常: {errors}"
        from agentnexus.memory.long_term import get_long_term_memory
        n_rows = get_long_term_memory()._conn.execute(
            "SELECT COUNT(*) AS c FROM long_term_memories WHERE session_id LIKE 'emb_s%'"
        ).fetchone()["c"]
        assert n_rows == n_managers * n_saves

    def test_concurrent_search_during_writes(self, shared_home):
        """边写边搜：搜索不得抛错或返回损坏行。"""
        managers = [_make_manager(f"rw_s{i}", shared_home / f"ws{i}") for i in range(2)]
        stop = threading.Event()
        errors: list[Exception] = []
        lock = threading.Lock()

        def writer(mgr, tag):
            try:
                for j in range(30):
                    mgr.long_term.save(session_id=tag, content=f"{tag} 事实 {j}",
                                       category="fact", importance=0.6, embedding=None)
            except Exception as e:
                with lock:
                    errors.append(e)
            finally:
                stop.set()

        def reader():
            from agentnexus.memory.long_term import get_long_term_memory
            ltm = get_long_term_memory()
            while not stop.is_set():
                try:
                    rows = ltm.list_recent(limit=10)
                    for r in rows:
                        assert r["content"], "搜索返回了损坏行"
                except Exception as e:
                    with lock:
                        errors.append(e)
                    return

        threads = (
            [threading.Thread(target=writer, args=(managers[0], "rw_s0"))]
            + [threading.Thread(target=writer, args=(managers[1], "rw_s1"))]
            + [threading.Thread(target=reader) for _ in range(2)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"读写并发异常: {errors}"
