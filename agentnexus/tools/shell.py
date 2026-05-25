"""Shell execution tool with sandbox fallback and workspace cwd checks."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from agentnexus.core.config import get_settings

_SYSTEM = platform.system()


class ShellSandboxUnavailable(RuntimeError):
    """Raised when a requested shell sandbox backend is unavailable."""


_WIN_BLACKLIST = [
    r"format\s+[A-Za-z]:",
    r"del\s+/[fqs]\s+[A-Za-z]:",
    r"rmdir\s+/s\s+[A-Za-z]:",
    r"diskpart",
    r"bcdedit",
    r"reg\s+(add|delete)\s+/f",
    r"icacls\s+[A-Za-z]:\\",
    r"takeown\s+/f\s+[A-Za-z]:\\",
    r"wmic\s+path\s+Win32_Product\s+where.*call\s+uninstall",
]

_UNIX_BLACKLIST = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=",
    r">>\s*/dev/sd",
    r">\s*/dev/sd",
    r"chmod\s+777\s+/",
    r":\s*\(\s*\)\s*\{\s*:\s*\|:",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    r"ssh\s+.*root@",
]

_COMMON_BLACKLIST = [
    r"shutdown\s+(-s|-h|-r\s+now)",
    r"reboot",
    r"logoff",
    r"rm\s+-rf\s+/",
    r"(?:powershell(?:\.exe)?|pwsh)\s+.*(?:-|/)(?:e|enc|encodedcommand)\b",
]


def _check_blacklist(command: str) -> str | None:
    """Check command against safety blacklist. Returns blocked message or None."""
    settings = get_settings()
    custom_blacklist = getattr(settings, "shell_blacklist", [])

    all_patterns = list(_COMMON_BLACKLIST) + list(custom_blacklist)
    if _SYSTEM == "Windows":
        all_patterns.extend(_WIN_BLACKLIST)
    else:
        all_patterns.extend(_UNIX_BLACKLIST)

    normalized_cmd = unicodedata.normalize("NFKC", command).lower()
    for pattern in all_patterns:
        if re.search(pattern, normalized_cmd):
            return f"[blocked] 命令已被安全策略拦截: 匹配危险模式 '{pattern}'"
    return None


def _apply_timeout(command: str, timeout: int) -> str:
    """Wrap command with timeout mechanism appropriate for the OS."""
    if _SYSTEM == "Windows":
        return command
    return f"timeout {timeout} {command}"


def shell_exec(command: str, cwd: str | None = None, timeout: int = 30) -> str:
    """Execute a shell command through E2B/native/Docker/local fallback."""
    settings = get_settings()

    if not getattr(settings, "shell_enabled", True):
        return "错误: Shell 执行功能已在配置中禁用 (shell_enabled=false)"

    blocked = _check_blacklist(command)
    if blocked:
        return blocked

    from agentnexus.tools.file_ops import _resolve_safe

    work_dir = str(_resolve_safe(cwd)) if cwd else str(_resolve_safe("."))
    timeout_sec = timeout if timeout > 0 else getattr(settings, "shell_timeout", 30)
    backend = getattr(settings, "shell_execution_backend", "auto")

    try:
        if backend == "disabled":
            return "[blocked] Shell execution is disabled by shell_execution_backend=disabled."
        if backend == "auto":
            return _execute_shell_auto(command, work_dir, settings, timeout_sec)
        if backend == "e2b":
            return _execute_shell_e2b(command, work_dir, settings, timeout_sec)
        if backend == "native":
            return _execute_shell_native(command, work_dir, timeout_sec)
        if backend == "docker":
            return _execute_shell_docker(command, work_dir, settings, timeout_sec)
        if backend == "local_unsafe":
            return _execute_shell_locally(command, work_dir, timeout_sec)
    except subprocess.TimeoutExpired:
        return f"错误: 命令超时 (>{timeout_sec}秒): {command[:200]}"
    except FileNotFoundError:
        return f"错误: 命令解释器未找到。当前系统: {_SYSTEM}。请检查命令是否正确。"
    except ShellSandboxUnavailable as e:
        return _shell_unavailable_message([f"{backend}: {e}"])
    except Exception as e:
        return f"错误: 命令执行失败: {e}"

    return _shell_unavailable_message([f"{backend}: unsupported backend"])


def _execute_shell_auto(command: str, work_dir: str, settings, timeout_sec: int) -> str:
    failures: list[str] = []

    try:
        return _execute_shell_e2b(command, work_dir, settings, timeout_sec)
    except subprocess.TimeoutExpired:
        raise
    except ShellSandboxUnavailable as e:
        failures.append(f"e2b: {e}")
    except Exception as e:
        failures.append(f"e2b: {e}")

    try:
        return _execute_shell_native(command, work_dir, timeout_sec)
    except subprocess.TimeoutExpired:
        raise
    except ShellSandboxUnavailable as e:
        failures.append(f"native: {e}")
    except Exception as e:
        failures.append(f"native: {e}")

    try:
        return _execute_shell_docker(command, work_dir, settings, timeout_sec)
    except subprocess.TimeoutExpired:
        raise
    except ShellSandboxUnavailable as e:
        failures.append(f"docker: {e}")
    except Exception as e:
        failures.append(f"docker: {e}")

    return _execute_shell_locally_with_warning(command, work_dir, timeout_sec, failures)


def _execute_shell_e2b(command: str, work_dir: str, settings, timeout_sec: int) -> str:
    raise ShellSandboxUnavailable("E2B shell backend is not implemented for arbitrary shell commands")


def _execute_shell_native(command: str, work_dir: str, timeout_sec: int) -> str:
    if _SYSTEM == "Linux":
        return _execute_shell_bubblewrap(command, work_dir, timeout_sec)
    if _SYSTEM == "Darwin":
        return _execute_shell_seatbelt(command, work_dir, timeout_sec)
    if _SYSTEM == "Windows":
        return _execute_shell_windows_native(command, work_dir, timeout_sec)
    raise ShellSandboxUnavailable(f"unsupported OS: {_SYSTEM}")


def _execute_shell_bubblewrap(command: str, work_dir: str, timeout_sec: int) -> str:
    bwrap = shutil.which("bwrap") or shutil.which("bubblewrap")
    if not bwrap:
        raise ShellSandboxUnavailable("bubblewrap is not installed")

    shell = shutil.which("sh") or "/bin/sh"
    cmd = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", work_dir, "/workspace",
        "--chdir", "/workspace",
        shell,
        "-lc",
        command,
    ]
    return _run_shell_command(cmd, timeout=timeout_sec)


def _execute_shell_seatbelt(command: str, work_dir: str, timeout_sec: int) -> str:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise ShellSandboxUnavailable("macOS sandbox-exec/Seatbelt is not available")

    with tempfile.TemporaryDirectory(prefix="agentnexus-shell-") as tmp:
        profile = Path(tmp) / "sandbox.sb"
        profile.write_text(
            """
(version 1)
(deny default)
(allow process*)
(allow file-read* (literal "/bin") (literal "/usr") (literal "/System") (literal "/Library"))
(allow file-read* (subpath "/bin") (subpath "/usr") (subpath "/System") (subpath "/Library"))
(allow file-read* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom"))
(allow file-read* (subpath "%s"))
(allow file-write* (subpath "%s"))
(allow file-write* (subpath "/private/tmp"))
"""
            % (work_dir, work_dir),
            encoding="utf-8",
        )
        cmd = [sandbox_exec, "-f", str(profile), "/bin/sh", "-lc", command]
        return _run_shell_command(cmd, timeout=timeout_sec, cwd=work_dir)


def _execute_shell_windows_native(command: str, work_dir: str, timeout_sec: int) -> str:
    raise ShellSandboxUnavailable("Windows native shell sandbox runner is not available; falling back to Docker")


def _execute_shell_docker(command: str, work_dir: str, settings, timeout_sec: int) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise ShellSandboxUnavailable("Docker CLI is not installed or not on PATH")

    image = getattr(settings, "shell_execution_docker_image", "python:3.11-slim")
    memory_mb = getattr(settings, "shell_execution_memory_mb", 256)
    cmd = [
        docker,
        "run",
        "--rm",
        "--network", "none",
        "--cpus", "1",
        "--memory", f"{memory_mb}m",
        "--pids-limit", "64",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
    ]
    if _SYSTEM != "Windows":
        cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    cmd.extend([
        "-v", f"{work_dir}:/workspace",
        "-w", "/workspace",
        image,
        "sh",
        "-lc",
        command,
    ])
    return _run_shell_command(cmd, timeout=timeout_sec)


def _execute_shell_locally(command: str, work_dir: str, timeout_sec: int) -> str:
    if _SYSTEM == "Windows":
        result = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    else:
        shell = shutil.which("sh") or "/bin/sh"
        result = subprocess.run(
            [shell, "-lc", command],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    return _format_shell_result(result)


def _execute_shell_locally_with_warning(
    command: str,
    work_dir: str,
    timeout_sec: int,
    failures: list[str],
) -> str:
    detail = "\n".join(f"- {item}" for item in failures)
    warning = (
        "[warning] Safe shell execution sandboxes are unavailable; "
        "falling back to unsafe local shell execution.\n"
        f"{detail}\n"
        "Only run commands you trust in this mode."
    )
    local_result = _execute_shell_locally(command, work_dir, timeout_sec)
    return f"{warning}\n{local_result}" if local_result else warning


def _run_shell_command(cmd: list[str], timeout: int, cwd: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return _format_shell_result(result)


def _format_shell_result(result: subprocess.CompletedProcess) -> str:
    parts = []
    if result.stdout:
        parts.append(f"[stdout]\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    if not parts:
        parts.append("[执行完成，无输出]")
    parts.append(f"exit_code: {result.returncode}")
    return "\n".join(parts)


def _shell_unavailable_message(failures: list[str]) -> str:
    detail = "\n".join(f"- {item}" for item in failures)
    return (
        "[blocked] No safe shell execution sandbox is available.\n"
        f"{detail}\n"
        "Use shell_execution_backend=auto for warned local fallback, "
        "or install an OS sandbox/Docker."
    )


def get_os_info() -> str:
    """Return OS info string for tool descriptions and prompts."""
    return f"{_SYSTEM} ({platform.release()})"
