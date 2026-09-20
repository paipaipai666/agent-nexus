"""Track in-flight tool subprocesses so cancellation can kill them immediately.

产品决策：取消/断连必须立刻停止所有行为。旧实现里 cancel 只是置标志位，
ThreadPoolExecutor 的 with 块会等工具进程自己跑完才返回。这里借鉴
pi（earendil-works/pi）的 killProcessTree 与 codex 的 process_group
做法：

- 工具 spawn 进程时通过 :func:`run_tracked` 或 :func:`track` 登记；
- cancel 方按**工具工作线程 tid** 找到该工具 spawn 的所有进程并整树直杀
  （Windows: taskkill /F /T；POSIX: 进程组 SIGKILL）；
- 无法追踪的工具（MCP 远程调用等）行为不变：cancel 停止等待，底层
  调用由它自己的超时兜底。

shell 工具以 ``start_new_session=True`` 建独立进程组，保证 killpg
能带走孙进程（pi 的 detached 同款语义）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any

_lock = threading.Lock()
# tid -> set of live Popen registered by that thread
_tracked: dict[int, set[subprocess.Popen]] = {}


def track(proc: subprocess.Popen) -> None:
    """Register a Popen owned by the calling thread."""
    tid = threading.get_ident()
    with _lock:
        _tracked.setdefault(tid, set()).add(proc)


def untrack(proc: subprocess.Popen) -> None:
    tid = threading.get_ident()
    with _lock:
        procs = _tracked.get(tid)
        if procs is not None:
            procs.discard(proc)
            if not procs:
                _tracked.pop(tid, None)


def kill_processes_for_thread(tid: int) -> int:
    """Kill every process tracked by the given thread; returns kill attempt count.

    Safe to call from a different thread than the one that spawned them
    (the cancel caller vs. the tool worker).
    """
    with _lock:
        procs = list(_tracked.get(tid, ()))
    killed = 0
    for proc in procs:
        if proc.poll() is None:
            kill_process_tree(proc.pid)
            killed += 1
    return killed


def kill_process_tree(pid: int) -> None:
    """Kill a pid and all its descendants, cross-platform.

    Windows: taskkill /F /T from the System32 absolute path (PATH 无关，
    与 pi 一致); POSIX: 进程组 SIGKILL，组杀失败回退单 PID。
    """
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = os.path.join(system_root, "System32", "taskkill.exe")
        try:
            subprocess.run(
                [taskkill, "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            # taskkill 失败（权限/竞态）时进程可能已退出；忽略。
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass


def run_tracked(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: str | None = None,
    capture_output: bool = False,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    env: dict | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """subprocess.run 的替代品：登记进程 + 超时时整树直杀。

    语义与 subprocess.run 对齐（返回 CompletedProcess，超时抛
    subprocess.TimeoutExpired），但超时/取消路径会把进程树一起杀掉，
    而不是像 subprocess.run 那样只杀直接子进程留下孤儿。
    """
    kwargs: dict[str, Any] = dict(
        cwd=cwd,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        encoding=encoding,
        errors=errors,
        env=env,
    )
    if sys.platform != "win32":
        # 独立进程组，保证 kill_process_tree 的 killpg 覆盖孙进程。
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    track(proc)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(
            args=cmd, returncode=proc.returncode, stdout=out, stderr=err,
        )
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc.pid)
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=timeout, output=exc.output if exc.output is not None else out,
            stderr=exc.stderr if exc.stderr is not None else err,
        )
    finally:
        untrack(proc)
