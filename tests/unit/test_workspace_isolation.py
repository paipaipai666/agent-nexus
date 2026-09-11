"""Workspace ContextVar 并发隔离测试。

current_workspace（agentnexus/tools/workspace.py）决定工具的文件落点。
ChatService 每个 run 开始 set、finally reset。串扰 = 会话 A 的文件写进
会话 B 的目录（功能+安全双重事故）。本文件覆盖三种执行模型的隔离契约：
独立线程、asyncio 任务、asyncio.to_thread（FastAPI 线程池路径）。
"""
import asyncio
import threading
import time
from pathlib import Path

from agentnexus.tools.workspace import current_workspace, get_effective_workspace


class TestThreadIsolation:
    def test_concurrent_threads_see_own_workspace(self, tmp_path):
        """8 线程对齐起跑 + 让出 GIL，各自必须看到自己的 workspace。"""
        barrier = threading.Barrier(8)
        errors: list[str] = []

        def worker(i: int):
            ws = (tmp_path / f"ws_{i}").resolve()
            token = current_workspace.set(str(ws))
            try:
                barrier.wait()
                time.sleep(0.01)
                seen = get_effective_workspace()
                if seen != ws:
                    errors.append(f"thread {i}: 期望 {ws}, 实际 {seen}")
            finally:
                current_workspace.reset(token)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_reset_restores_previous_value(self, tmp_path):
        outer = (tmp_path / "outer").resolve()
        inner = (tmp_path / "inner").resolve()
        t1 = current_workspace.set(str(outer))
        t2 = current_workspace.set(str(inner))
        current_workspace.reset(t2)
        assert get_effective_workspace() == outer
        current_workspace.reset(t1)


class TestAsyncioIsolation:
    def test_concurrent_tasks_see_own_workspace(self, tmp_path):
        """8 个 asyncio 任务交错 await，各自必须看到自己的 workspace。"""

        async def worker(i: int):
            ws = (tmp_path / f"ws_{i}").resolve()
            token = current_workspace.set(str(ws))
            try:
                await asyncio.sleep(0.01)
                return get_effective_workspace()
            finally:
                current_workspace.reset(token)

        async def main():
            return await asyncio.gather(*[worker(i) for i in range(8)])

        results = asyncio.run(main())
        for i, seen in enumerate(results):
            assert seen == (tmp_path / f"ws_{i}").resolve()

    def test_to_thread_propagates_workspace(self, tmp_path):
        """契约（docstring 声明）：ContextVar 穿过 asyncio.to_thread——
        FastAPI 在线程池跑 sync 路由/工具时仍能看到会话 workspace。"""

        async def main():
            ws = (tmp_path / "ws_async").resolve()
            token = current_workspace.set(str(ws))
            try:
                return await asyncio.to_thread(get_effective_workspace)
            finally:
                current_workspace.reset(token)

        assert asyncio.run(main()) == (tmp_path / "ws_async").resolve()


class TestPropagationBoundary:
    def test_bare_thread_does_not_inherit_workspace(self, tmp_path):
        """边界文档化：裸线程不继承调用方 ContextVar（回落 cwd）。

        这意味着工具必须在 run 线程内或经 asyncio.to_thread 执行；
        任何走裸 ThreadPoolExecutor 的执行路径都会丢失会话工作区。
        """
        ws = (tmp_path / "caller_ws").resolve()
        seen: list[Path] = []
        token = current_workspace.set(str(ws))
        try:
            t = threading.Thread(target=lambda: seen.append(get_effective_workspace()))
            t.start()
            t.join()
        finally:
            current_workspace.reset(token)
        assert seen[0] != ws, "裸线程不应继承调用方 workspace"
