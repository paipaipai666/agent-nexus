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
from agentnexus.tools import process_tracker

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
    """Execute a shell command through native/Docker/local fallback."""
    from agentnexus.core.hooks import HookType, get_hook_manager

    settings = get_settings()

    if not getattr(settings, "shell_enabled", True):
        return "错误: Shell 执行功能已在配置中禁用 (shell_enabled=false)"

    blocked = _check_blacklist(command)
    if blocked:
        return blocked

    hook_mgr = get_hook_manager()
    hook_ctx = hook_mgr.fire(
        HookType.BEFORE_SHELL_EXEC,
        {
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
        },
    )
    if hook_ctx.aborted:
        return f"[{hook_ctx.abort_code}] {hook_ctx.abort_reason}"

    from agentnexus.tools.file_ops import _resolve_safe

    work_dir = str(_resolve_safe(cwd)) if cwd else str(_resolve_safe("."))
    timeout_sec = timeout if timeout > 0 else getattr(settings, "shell_timeout", 30)
    backend = getattr(settings, "shell_execution_backend", "auto")

    result = ""
    try:
        if backend == "disabled":
            result = "[blocked] Shell execution is disabled by shell_execution_backend=disabled."
        elif backend == "auto":
            result = _execute_shell_auto(command, work_dir, settings, timeout_sec)
        elif backend == "native":
            result = _execute_shell_native(command, work_dir, timeout_sec)
        elif backend == "docker":
            result = _execute_shell_docker(command, work_dir, settings, timeout_sec)
        elif backend == "local_unsafe":
            result = _execute_shell_locally(command, work_dir, timeout_sec)
        else:
            result = _shell_unavailable_message([f"{backend}: unsupported backend"])
    except subprocess.TimeoutExpired:
        result = f"错误: 命令超时 (>{timeout_sec}秒): {command[:200]}"
    except FileNotFoundError:
        result = f"错误: 命令解释器未找到。当前系统: {_SYSTEM}。请检查命令是否正确。"
    except ShellSandboxUnavailable as e:
        result = _shell_unavailable_message([f"{backend}: {e}"])
    except Exception as e:
        result = f"错误: 命令执行失败: {e}"

    hook_mgr.fire(
        HookType.AFTER_SHELL_EXEC,
        {
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
            "result": result[:500],
            "backend": backend,
        },
    )
    return result


def _execute_shell_auto(command: str, work_dir: str, settings, timeout_sec: int) -> str:
    failures: list[str] = []

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


def _execute_shell_native(command: str, work_dir: str, timeout_sec: int) -> str:
    if _SYSTEM == "Linux":
        return _execute_shell_bubblewrap(command, work_dir, timeout_sec)
    if _SYSTEM == "Darwin":
        return _execute_shell_seatbelt(command, work_dir, timeout_sec)
    if _SYSTEM == "Windows":
        return _execute_windows_native(command, work_dir, timeout_sec)
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
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        work_dir,
        "/workspace",
        "--chdir",
        "/workspace",
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


# ---------------------------------------------------------------------------
# Windows native sandbox — restricted token + Low integrity level + job object
#
# Mirrors the approach of Gemini CLI's GeminiSandbox helper and Codex CLI's
# restricted-token backend, implemented with stdlib ctypes (no pywin32
# dependency). Works unelevated: CreateProcessAsUser accepts a restricted
# version of the caller's own primary token without SeAssignPrimaryTokenPrivilege
# (Vista+), and lowering the token integrity level to Low never requires a
# privilege.
#
# ponytail: filesystem/integrity isolation only — no network restriction on
# this backend (the docker backend uses --network none). No memory limits
# (docker uses --memory). Job assignment is best-effort: if the host process
# already runs inside a job, AssignProcessToJobObject may fail and we lose
# tree-kill, but token isolation still holds.
# ---------------------------------------------------------------------------

_WINDOWS_API: dict | None = None


def _windows_api() -> dict:
    """Lazily bind the Win32 functions needed by the native sandbox."""
    global _WINDOWS_API
    if _WINDOWS_API is not None:
        return _WINDOWS_API
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        HANDLE = wintypes.HANDLE
        DWORD = wintypes.DWORD
        BOOL = wintypes.BOOL

        class _STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", DWORD),
                ("lpReserved", wintypes.LPCWSTR),
                ("lpDesktop", wintypes.LPCWSTR),
                ("lpTitle", wintypes.LPCWSTR),
                ("dwX", DWORD),
                ("dwY", DWORD),
                ("dwXSize", DWORD),
                ("dwYSize", DWORD),
                ("dwXCountChars", DWORD),
                ("dwYCountChars", DWORD),
                ("dwFillAttribute", DWORD),
                ("dwFlags", DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", HANDLE),
                ("hStdOutput", HANDLE),
                ("hStdError", HANDLE),
            ]

        class _PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", HANDLE),
                ("hThread", HANDLE),
                ("dwProcessId", DWORD),
                ("dwThreadId", DWORD),
            ]

        class _SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength", DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", BOOL),
            ]

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", DWORD),
                ("SchedulingClass", DWORD),
            ]

        def _proto(dll, name, restype, argtypes):
            fn = getattr(dll, name)
            fn.restype = restype
            fn.argtypes = argtypes
            return fn

        api = {
            "ctypes": ctypes,
            "HANDLE": HANDLE,
            "DWORD": DWORD,
            "OpenProcessToken": _proto(advapi32, "OpenProcessToken", BOOL, [HANDLE, DWORD, ctypes.POINTER(HANDLE)]),
            "CreateRestrictedToken": _proto(
                advapi32,
                "CreateRestrictedToken",
                BOOL,
                [
                    HANDLE,
                    DWORD,
                    DWORD,
                    ctypes.c_void_p,
                    DWORD,
                    ctypes.c_void_p,
                    DWORD,
                    ctypes.c_void_p,
                    ctypes.POINTER(HANDLE),
                ],
            ),
            "ConvertStringSidToSidW": _proto(
                advapi32, "ConvertStringSidToSidW", BOOL, [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
            ),
            "SetTokenInformation": _proto(
                advapi32, "SetTokenInformation", BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]
            ),
            "CreateProcessAsUserW": _proto(
                advapi32,
                "CreateProcessAsUserW",
                BOOL,
                [
                    HANDLE,
                    wintypes.LPCWSTR,
                    wintypes.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    BOOL,
                    DWORD,
                    ctypes.c_void_p,
                    wintypes.LPCWSTR,
                    ctypes.POINTER(_STARTUPINFOW),
                    ctypes.POINTER(_PROCESS_INFORMATION),
                ],
            ),
            "CreateJobObjectW": _proto(kernel32, "CreateJobObjectW", HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
            "SetInformationJobObject": _proto(
                kernel32, "SetInformationJobObject", BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]
            ),
            "AssignProcessToJobObject": _proto(kernel32, "AssignProcessToJobObject", BOOL, [HANDLE, HANDLE]),
            "TerminateJobObject": _proto(kernel32, "TerminateJobObject", BOOL, [HANDLE, ctypes.c_uint]),
            "ResumeThread": _proto(kernel32, "ResumeThread", DWORD, [HANDLE]),
            "CreatePipe": _proto(
                kernel32,
                "CreatePipe",
                BOOL,
                [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), ctypes.POINTER(_SECURITY_ATTRIBUTES), DWORD],
            ),
            "SetHandleInformation": _proto(kernel32, "SetHandleInformation", BOOL, [HANDLE, DWORD, DWORD]),
            "ReadFile": _proto(
                kernel32, "ReadFile", BOOL, [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), ctypes.c_void_p]
            ),
            "WaitForSingleObject": _proto(kernel32, "WaitForSingleObject", DWORD, [HANDLE, DWORD]),
            "GetExitCodeProcess": _proto(kernel32, "GetExitCodeProcess", BOOL, [HANDLE, ctypes.POINTER(DWORD)]),
            "TerminateProcess": _proto(kernel32, "TerminateProcess", BOOL, [HANDLE, ctypes.c_uint]),
            "CloseHandle": _proto(kernel32, "CloseHandle", BOOL, [HANDLE]),
            "GetCurrentProcess": _proto(kernel32, "GetCurrentProcess", HANDLE, []),
            "STARTUPINFOW": _STARTUPINFOW,
            "PROCESS_INFORMATION": _PROCESS_INFORMATION,
            "SECURITY_ATTRIBUTES": _SECURITY_ATTRIBUTES,
            "JOBOBJECT_BASIC_LIMIT_INFORMATION": _JOBOBJECT_BASIC_LIMIT_INFORMATION,
        }
    except (AttributeError, OSError) as exc:
        raise ShellSandboxUnavailable(f"Win32 API binding failed: {exc}") from exc
    _WINDOWS_API = api
    return api


def _windows_low_il_env(work_dir: str) -> tuple[str, str]:
    """Prepare the sandbox cwd: label it Low integrity (persistent, like
    Gemini CLI's approach) and return a Low-integrity temp dir for TMP/TEMP."""
    icacls = shutil.which("icacls")
    if icacls:
        # Best-effort: failure leaves the sandbox read-only outside Low-IL dirs,
        # never blocks execution.
        subprocess.run(
            [icacls, work_dir, "/setintegritylevel", "Low"],
            capture_output=True,
            timeout=15,
        )
    tmp_dir = os.path.join(work_dir, ".agentnexus-sandbox-tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    return work_dir, tmp_dir


def _execute_windows_native(command: str, work_dir: str, timeout_sec: int) -> str:
    """Run `command` on Windows at Low integrity level.

    Isolation: restricted token (all privileges removed) + Low mandatory
    integrity level (blocks writes to Medium/High-IL objects such as the user
    profile, registry HKLM, and system directories) + job object with
    KILL_ON_JOB_CLOSE (cleans up the whole child tree).
    """
    if _SYSTEM != "Windows":
        raise ShellSandboxUnavailable(f"unsupported OS: {_SYSTEM}")

    api = _windows_api()
    ctypes = api["ctypes"]
    HANDLE = api["HANDLE"]
    DWORD = api["DWORD"]

    TOKEN_ALL_ACCESS = 0xF01FF
    DISABLE_MAX_PRIVILEGE = 0x1
    TOKEN_INTEGRITY_LEVEL = 25
    SE_GROUP_INTEGRITY = 0x20
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_BASIC_LIMIT_INFORMATION = 2
    STARTF_USESTDHANDLES = 0x100
    CREATE_SUSPENDED = 0x4
    CREATE_UNICODE_ENVIRONMENT = 0x400
    HANDLE_FLAG_INHERIT = 0x1
    WAIT_TIMEOUT = 0x102
    ERROR_BROKEN_PIPE = 109

    cwd, tmp_dir = _windows_low_il_env(work_dir)

    # Anonymous pipes for stdout/stderr; both inheritable so the child can
    # write them, read ends made non-inheritable so the child can't keep them.
    sa = api["SECURITY_ATTRIBUTES"]()
    sa.nLength = ctypes.sizeof(sa)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = True
    h_out_read = HANDLE()
    h_out_write = HANDLE()
    h_err_read = HANDLE()
    h_err_write = HANDLE()
    if not api["CreatePipe"](ctypes.byref(h_out_read), ctypes.byref(h_out_write), ctypes.byref(sa), 0):
        raise ShellSandboxUnavailable(f"CreatePipe(stdout) failed: {ctypes.get_last_error()}")
    if not api["CreatePipe"](ctypes.byref(h_err_read), ctypes.byref(h_err_write), ctypes.byref(sa), 0):
        raise ShellSandboxUnavailable(f"CreatePipe(stderr) failed: {ctypes.get_last_error()}")
    for rh in (h_out_read, h_err_read):
        api["SetHandleInformation"](rh, HANDLE_FLAG_INHERIT, 0)

    h_token = h_restricted = h_job = None
    pi = api["PROCESS_INFORMATION"]()
    threads = []
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    try:
        # 1. Restricted, Low-IL primary token.
        h_token = HANDLE()
        if not api["OpenProcessToken"](api["GetCurrentProcess"](), TOKEN_ALL_ACCESS, ctypes.byref(h_token)):
            raise ShellSandboxUnavailable(f"OpenProcessToken failed: {ctypes.get_last_error()}")
        h_restricted = HANDLE()
        if not api["CreateRestrictedToken"](
            h_token, DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None, ctypes.byref(h_restricted)
        ):
            raise ShellSandboxUnavailable(f"CreateRestrictedToken failed: {ctypes.get_last_error()}")
        low_sid = ctypes.c_void_p()
        if not api["ConvertStringSidToSidW"]("S-1-16-4096", ctypes.byref(low_sid)):
            raise ShellSandboxUnavailable(f"ConvertStringSidToSid failed: {ctypes.get_last_error()}")

        # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { Sid, Attributes } }
        class _SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", DWORD)]

        class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
            _fields_ = [("Label", _SID_AND_ATTRIBUTES)]

        tml = _TOKEN_MANDATORY_LABEL()
        tml.Label.Sid = low_sid
        tml.Label.Attributes = SE_GROUP_INTEGRITY
        if not api["SetTokenInformation"](h_restricted, TOKEN_INTEGRITY_LEVEL, ctypes.byref(tml), ctypes.sizeof(tml)):
            raise ShellSandboxUnavailable(f"SetTokenInformation(IL) failed: {ctypes.get_last_error()}")

        # 2. Environment: redirect TMP/TEMP into the Low-IL workspace temp dir.
        env = dict(os.environ)
        env["TMP"] = tmp_dir
        env["TEMP"] = tmp_dir
        env["AGENTNEXUS_SANDBOX"] = "windows-low-il"
        env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"

        si = api["STARTUPINFOW"]()
        si.cb = ctypes.sizeof(si)
        si.lpReserved = si.lpDesktop = si.lpTitle = None
        si.dwFlags = STARTF_USESTDHANDLES
        si.hStdInput = None
        si.hStdOutput = h_out_write
        si.hStdError = h_err_write

        # 3. Launch suspended so the job object catches every child process.
        cmdline = f"cmd.exe /d /c {command}"
        if not api["CreateProcessAsUserW"](
            h_restricted,
            None,
            cmdline,
            None,
            None,
            True,
            CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
            env_block,
            cwd,
            ctypes.byref(si),
            ctypes.byref(pi),
        ):
            raise ShellSandboxUnavailable(f"CreateProcessAsUser failed: {ctypes.get_last_error()}")

        api["CloseHandle"](h_out_write)
        h_out_write = None
        api["CloseHandle"](h_err_write)
        h_err_write = None

        # 4. Job object: kill the whole tree when the job handle closes.
        h_job = api["CreateJobObjectW"](None, None)
        if h_job:
            limits = api["JOBOBJECT_BASIC_LIMIT_INFORMATION"]()
            limits.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            api["SetInformationJobObject"](
                h_job, JOB_OBJECT_BASIC_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            if not api["AssignProcessToJobObject"](h_job, pi.hProcess):
                api["CloseHandle"](h_job)
                h_job = None  # already in a job: lose tree-kill only

        api["ResumeThread"](pi.hThread)
        api["CloseHandle"](pi.hThread)

        # 5. Drain pipes on threads (avoids pipe-buffer deadlock), wait w/ timeout.
        def _drain(handle, chunks):
            buf = ctypes.create_string_buffer(65536)
            read = DWORD()
            while True:
                if not api["ReadFile"](handle, buf, 65536, ctypes.byref(read), None):
                    if ctypes.get_last_error() == ERROR_BROKEN_PIPE:
                        break
                    break
                if read.value == 0:
                    break
                chunks.append(buf.raw[: read.value])

        import threading

        for handle, chunks in ((h_out_read, out_chunks), (h_err_read, err_chunks)):
            t = threading.Thread(target=_drain, args=(handle, chunks), daemon=True)
            t.start()
            threads.append(t)

        wait_ms = int(timeout_sec * 1000)
        if api["WaitForSingleObject"](pi.hProcess, wait_ms) == WAIT_TIMEOUT:
            if h_job:
                api["TerminateJobObject"](h_job, 1)
            else:
                api["TerminateProcess"](pi.hProcess, 1)
            api["WaitForSingleObject"](pi.hProcess, 5000)
            return f"错误: 命令超时 (>{timeout_sec}秒): {command[:200]}"

        exit_code = DWORD()
        api["GetExitCodeProcess"](pi.hProcess, ctypes.byref(exit_code))
        for t in threads:
            t.join(timeout=5)
        stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        return _format_shell_result(subprocess.CompletedProcess(cmdline, exit_code.value, stdout, stderr))
    finally:
        for t in threads:
            t.join(timeout=1)
        for h in (h_out_read, h_err_read, h_out_write, h_err_write, h_token, h_restricted, h_job):
            if h:
                api["CloseHandle"](h)
        if pi.hProcess:
            api["CloseHandle"](pi.hProcess)


def _docker_daemon_available(docker: str) -> tuple[bool, str]:
    """Probe the docker daemon. Returns (ok, error detail).

    Without this probe, a stopped daemon surfaces as a `docker run` exit-code-1
    result string, which the auto chain mistakes for a successful backend run
    and stops degrading (host never falls back to local execution).
    """
    try:
        probe = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # timeout, spawn failure, ...
        return False, str(exc)
    if probe.returncode == 0:
        return True, ""
    return False, (probe.stderr or probe.stdout or "docker info failed").strip()


def _execute_shell_docker(command: str, work_dir: str, settings, timeout_sec: int) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise ShellSandboxUnavailable("Docker CLI is not installed or not on PATH")
    daemon_ok, daemon_err = _docker_daemon_available(docker)
    if not daemon_ok:
        raise ShellSandboxUnavailable(f"docker daemon unavailable: {daemon_err}")

    image = getattr(settings, "shell_execution_docker_image", "python:3.11-slim")
    memory_mb = getattr(settings, "shell_execution_memory_mb", 256)
    cmd = [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "1",
        "--memory",
        f"{memory_mb}m",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    if _SYSTEM != "Windows":
        cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    cmd.extend(
        [
            "-v",
            f"{work_dir}:/workspace",
            "-w",
            "/workspace",
            image,
            "sh",
            "-lc",
            command,
        ]
    )
    return _run_shell_command(cmd, timeout=timeout_sec)


def _execute_shell_locally(command: str, work_dir: str, timeout_sec: int) -> str:
    if _SYSTEM == "Windows":
        cmd = ["cmd", "/c", command]
    else:
        shell = shutil.which("sh") or "/bin/sh"
        cmd = [shell, "-lc", command]
    result = process_tracker.run_tracked(
        cmd,
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
    result = process_tracker.run_tracked(
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
