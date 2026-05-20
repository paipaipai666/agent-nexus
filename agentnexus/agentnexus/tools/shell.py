"""Shell execution tool — OS-aware, sandboxed command execution."""

from __future__ import annotations

import platform
import re
import subprocess

from agentnexus.core.config import get_settings

_SYSTEM = platform.system()

# Windows-specific dangerous command patterns
_WIN_BLACKLIST = [
    r"format\s+[A-Za-z]:",       # format C:
    r"del\s+/[fqs]\s+[A-Za-z]:", # del /f /s C:\
    r"rmdir\s+/s\s+[A-Za-z]:",   # rmdir /s C:\
    r"diskpart",
    r"bcdedit",
    r"reg\s+(add|delete)\s+/f",  # registry modification
    r"icacls\s+[A-Za-z]:\\",     # permission changes on system root
    r"takeown\s+/f\s+[A-Za-z]:\\",
    r"wmic\s+path\s+Win32_Product\s+where.*call\s+uninstall",
]

# Linux/macOS dangerous command patterns
_UNIX_BLACKLIST = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"chmod\s+777\s+/",
    r":\s*\(\s*\)\s*\{\s*:\s*\|:",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    r"ssh\s+.*root@",
]

# Combined patterns that are dangerous on any platform
_COMMON_BLACKLIST = [
    r"shutdown\s+(-s|-h|-r\s+now)",
    r"reboot",
    r"logoff",
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

    lower_cmd = command.lower()
    for pattern in all_patterns:
        if re.search(pattern, lower_cmd):
            return f"[blocked] 命令已被安全策略拦截: 匹配危险模式 '{pattern}'"
    return None


def _apply_timeout(command: str, timeout: int) -> str:
    """Wrap command with timeout mechanism appropriate for the OS."""
    if _SYSTEM == "Windows":
        # Windows: no timeout command, subprocess handles it
        return command
    else:
        # Linux/macOS: prepend timeout if available
        return f"timeout {timeout} {command}"


def shell_exec(command: str, cwd: str | None = None, timeout: int = 30) -> str:
    """Execute a shell command in the workspace sandbox.

    Args:
        command: Shell command to execute.
        cwd: Working directory for the command (relative to workspace, defaults to workspace root).
        timeout: Max execution time in seconds (default: 30).

    Returns formatted stdout + stderr + exit code.

    Security:
    - Command blacklist check before execution
    - cwd is restricted to workspace directory
    - Timeout enforced by subprocess
    - Shell=True is safe because cwd is sandboxed and dangerous commands are blocked
    """
    settings = get_settings()

    if not getattr(settings, "shell_enabled", True):
        return "错误: Shell 执行功能已在配置中禁用 (shell_enabled=false)"

    # Blacklist check
    blocked = _check_blacklist(command)
    if blocked:
        return blocked

    # Resolve cwd — must stay in workspace
    from agentnexus.tools.file_ops import _resolve_safe
    if cwd:
        work_dir = str(_resolve_safe(cwd))
    else:
        work_dir = str(_resolve_safe("."))

    timeout_sec = timeout if timeout > 0 else getattr(settings, "shell_timeout", 30)

    try:
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
        parts = []
        if result.stdout:
            parts.append(f"[stdout]\n{result.stdout.rstrip()}")
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if not parts:
            parts.append("[执行完成，无输出]")
        parts.append(f"exit_code: {result.returncode}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"错误: 命令超时 (>{timeout_sec}秒): {command[:200]}"
    except FileNotFoundError:
        return f"错误: 命令解释器未找到。当前系统: {_SYSTEM}。请检查命令是否正确。"
    except Exception as e:
        return f"错误: 命令执行失败: {e}"


def get_os_info() -> str:
    """Return OS info string for tool descriptions and prompts."""
    return f"{_SYSTEM} ({platform.release()})"
