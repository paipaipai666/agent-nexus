"""Tests for immediate tool-process kill on cancellation.

产品决策：断连或手动取消必须立刻停止所有行为。工具执行不能再等
进程自己跑完（旧行为：cancel 后 ThreadPoolExecutor 的 with 块会等满
整个工具时长）。这里验证 cancel 触发后进程树被直杀、execute_tool 迅速
返回 CANCELLED。
"""

import subprocess
import sys
import threading
import time

import pytest

from agentnexus.agents.tool_runner import execute_tool
from agentnexus.tools import process_tracker
from agentnexus.tools.errors import ToolError, ToolErrorCode


class _SleepyExecutor:
    """Fake tool executor that runs a long sleep via the tracked-process helper."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def invoke(self, *, name, params, caller, hitl_approver, tool_policy=None):
        self.proc = process_tracker.run_tracked(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            capture_output=True,
            text=True,
        )
        return "slept"


def test_cancel_kills_tool_process_tree_immediately():
    """cancel 触发后，正在跑的工具进程被杀死，execute_tool 秒级返回。"""
    executor = _SleepyExecutor()
    cancel = threading.Event()

    def trigger_cancel():
        time.sleep(0.5)
        cancel.set()

    threading.Thread(target=trigger_cancel, daemon=True).start()

    result_box: list = []

    def run():
        result_box.append(
            execute_tool(
                tool_executor=executor,
                name="shell",
                arguments={},
                caller="test",
                hitl_approver=lambda _summary: True,
                cancel_checker=cancel.is_set,
            )
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=10)

    assert not t.is_alive(), "cancel 后 execute_tool 仍在等待工具跑完——进程未被杀死"
    assert len(result_box) == 1
    result = result_box[0]
    assert isinstance(result, ToolError)
    assert result.error_code == ToolErrorCode.CANCELLED
    # run_tracked 返回 CompletedProcess —— communicate() 已返回本身即证明
    # 进程已退出（未被杀时 sleep(30) 不可能结束）。
    assert executor.proc is not None
    assert executor.proc.returncode is not None


def test_run_tracked_timeout_kills_tree():
    """run_tracked 超时后必须把进程树一起杀掉，而不是只杀直接子进程。"""
    error_box: list = []

    def run():
        try:
            process_tracker.run_tracked(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=1,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            error_box.append(exc)

    t = threading.Thread(target=run, daemon=True)
    start = time.monotonic()
    t.start()
    t.join(timeout=10)
    elapsed = time.monotonic() - start

    assert not t.is_alive(), "run_tracked 超时后仍在等进程退出——进程树未被杀死"
    assert elapsed < 10, f"timeout kill took {elapsed:.1f}s"
    assert len(error_box) == 1
    assert isinstance(error_box[0], subprocess.TimeoutExpired)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_kill_process_tree_kills_descendants_posix():
    """POSIX：子进程自成进程组，killpg 能带走孙进程。"""
    import os

    # sh 派生一个 sleep 孙进程；只杀直接子进程的话 sleep 会变孤儿。
    proc = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30"],
        start_new_session=True,
    )
    try:
        process_tracker.kill_process_tree(proc.pid)
        proc.wait(timeout=5)
        # 进程组应已不存在
        with pytest.raises(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), 0)
    finally:
        process_tracker.kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
