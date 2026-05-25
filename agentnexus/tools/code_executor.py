import ast
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agentnexus.core.config import get_settings

try:
    from e2b_code_interpreter import Sandbox
except Exception:
    Sandbox = None

_HAS_MAIN_RE = re.compile(r'^if\s+__name__\s*==\s*["\']__main__["\']', re.MULTILINE)
_SYSTEM = platform.system()


class SandboxUnavailable(RuntimeError):
    """Raised when a requested code execution sandbox is not available."""


def _ensure_main_block(code: str) -> str:
    """If code has no `if __name__ == '__main__':` block, auto-append module-level calls."""
    if _HAS_MAIN_RE.search(code):
        return code

    try:
        tree = ast.parse(code)
        funcs = [
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith('_')
            and not node.args.args
        ]
        if not funcs:
            funcs = [
                node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and not node.name.startswith('_')
            ]
        if funcs:
            main_block = '\n\n# Auto-appended entry point\n'
            for name in funcs[:10]:
                main_block += f'print(f"\\n=== {name} ====")\n'
                main_block += f'{name}()\n'
            return code + main_block
    except SyntaxError:
        pass

    return code + '\n\nprint("Auto-executed")\n'


def python_execute(code: str) -> str:
    settings = get_settings()
    code = _ensure_main_block(code)
    backend = getattr(settings, "code_execution_backend", "auto")
    timeout = getattr(settings, "code_execution_timeout", getattr(settings, "shell_timeout", 30))

    try:
        if backend == "disabled":
            return _disabled_message()
        if backend == "auto":
            return _execute_auto(code, settings, timeout)
        if backend == "e2b":
            return _execute_e2b(code, settings)
        if backend == "native":
            return _execute_native_sandbox(code, timeout)
        if backend == "docker":
            return _execute_docker(code, settings, timeout)
        if backend == "local_unsafe":
            if not getattr(settings, "code_execution_allow_unsafe_local", False):
                return (
                    "[blocked] local_unsafe backend requires "
                    "code_execution_allow_unsafe_local=true."
                )
            return _execute_locally(code, timeout)
    except SandboxUnavailable as e:
        return _unavailable_message([f"{backend}: {e}"])
    except subprocess.TimeoutExpired:
        return f"错误: Python 代码执行超时 (>{timeout}秒)"
    except Exception as e:
        return f"错误: Python 代码执行失败: {e}"

    return _unavailable_message([f"{backend}: unsupported backend"])


def _execute_auto(code: str, settings, timeout: int) -> str:
    failures: list[str] = []

    if _has_e2b_key(settings):
        try:
            return _execute_e2b(code, settings)
        except subprocess.TimeoutExpired:
            raise
        except SandboxUnavailable as e:
            failures.append(f"e2b: {e}")
        except Exception as e:
            failures.append(f"e2b: {e}")
    else:
        failures.append("e2b: AGENTNEXUS_E2B_API_KEY not configured")

    try:
        return _execute_native_sandbox(code, timeout)
    except subprocess.TimeoutExpired:
        raise
    except SandboxUnavailable as e:
        failures.append(f"native: {e}")
    except Exception as e:
        failures.append(f"native: {e}")

    try:
        return _execute_docker(code, settings, timeout)
    except subprocess.TimeoutExpired:
        raise
    except SandboxUnavailable as e:
        failures.append(f"docker: {e}")
    except Exception as e:
        failures.append(f"docker: {e}")

    return _execute_locally_with_warning(code, timeout, failures)


def _has_e2b_key(settings) -> bool:
    api_key = getattr(settings, "e2b_api_key", None)
    if api_key is None:
        return False
    return bool(api_key.get_secret_value())


def _execute_e2b(code: str, settings) -> str:
    api_key = settings.e2b_api_key.get_secret_value()
    if not api_key:
        raise SandboxUnavailable("AGENTNEXUS_E2B_API_KEY not configured")
    if Sandbox is None:
        raise SandboxUnavailable("e2b-code-interpreter package is not available")

    previous_api_key = os.environ.get("E2B_API_KEY")
    try:
        os.environ["E2B_API_KEY"] = api_key
        with Sandbox() as sandbox:
            execution = sandbox.run_code(code)

        parts = []
        if execution.logs.stdout:
            parts.append(f"[stdout]\n{execution.logs.stdout}")
        if execution.logs.stderr:
            parts.append(f"[stderr]\n{execution.logs.stderr}")
        for res in execution.results:
            if res.text:
                parts.append(f"[result]\n{res.text}")
            elif res.png:
                parts.append("[result] <image output>")
            elif res.json:
                parts.append(f"[result]\n{res.json}")

        return "\n\n".join(parts) if parts else "[execution completed with no output]"
    finally:
        if previous_api_key is None:
            os.environ.pop("E2B_API_KEY", None)
        else:
            os.environ["E2B_API_KEY"] = previous_api_key


def _execute_native_sandbox(code: str, timeout: int) -> str:
    if _SYSTEM == "Linux":
        return _execute_bubblewrap(code, timeout)
    if _SYSTEM == "Darwin":
        return _execute_seatbelt(code, timeout)
    if _SYSTEM == "Windows":
        return _execute_windows_native(code, timeout)
    raise SandboxUnavailable(f"unsupported OS: {_SYSTEM}")


def _execute_bubblewrap(code: str, timeout: int) -> str:
    bwrap = shutil.which("bwrap") or shutil.which("bubblewrap")
    if not bwrap:
        raise SandboxUnavailable("bubblewrap is not installed")

    with tempfile.TemporaryDirectory(prefix="agentnexus-code-") as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(code, encoding="utf-8")
        cmd = [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind", sys.executable, sys.executable,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--dir", "/workspace",
            "--ro-bind", str(script), "/workspace/main.py",
            "--chdir", "/workspace",
            "--setenv", "PYTHONNOUSERSITE", "1",
            sys.executable,
            "/workspace/main.py",
        ]
        return _run_command(cmd, timeout=timeout)


def _execute_seatbelt(code: str, timeout: int) -> str:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise SandboxUnavailable("macOS sandbox-exec/Seatbelt is not available")

    with tempfile.TemporaryDirectory(prefix="agentnexus-code-") as tmp:
        tmp_path = Path(tmp)
        script = tmp_path / "main.py"
        profile = tmp_path / "sandbox.sb"
        script.write_text(code, encoding="utf-8")
        profile.write_text(
            """
(version 1)
(deny default)
(allow process*)
(allow file-read* (literal "/usr") (literal "/System") (literal "/Library"))
(allow file-read* (subpath "/usr") (subpath "/System") (subpath "/Library"))
(allow file-read* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom"))
(allow file-read* (subpath "%s"))
(allow file-write* (subpath "/private/tmp"))
"""
            % tmp,
            encoding="utf-8",
        )
        cmd = [sandbox_exec, "-f", str(profile), sys.executable, str(script)]
        return _run_command(cmd, timeout=timeout, cwd=tmp)


def _execute_windows_native(code: str, timeout: int) -> str:
    # Windows Sandbox is VM-oriented and Windows Pro/Enterprise only; it is not
    # a reliable synchronous Python runner. AppContainer requires a packaged
    # process token launcher that is not available from the stdlib.
    raise SandboxUnavailable("Windows native sandbox runner is not available; falling back to Docker")


def _execute_docker(code: str, settings, timeout: int) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise SandboxUnavailable("Docker CLI is not installed or not on PATH")

    image = getattr(settings, "code_execution_docker_image", "python:3.11-slim")
    memory_mb = getattr(settings, "code_execution_memory_mb", 256)
    with tempfile.TemporaryDirectory(prefix="agentnexus-code-") as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(code, encoding="utf-8")
        mount = f"{tmp}:/workspace:ro"
        cmd = [
            docker,
            "run",
            "--rm",
            "--network", "none",
            "--cpus", "1",
            "--memory", f"{memory_mb}m",
            "--pids-limit", "64",
            "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "-v", mount,
            "-w", "/workspace",
            image,
            "python",
            "/workspace/main.py",
        ]
        return _run_command(cmd, timeout=timeout)


def _run_command(cmd: list[str], timeout: int, cwd: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return _format_completed_process(result)


def _format_completed_process(result: subprocess.CompletedProcess) -> str:
    if result.returncode != 0 and not result.stdout and not result.stderr:
        return f"代码执行错误: exit_code={result.returncode}"

    parts = []
    if result.stdout:
        parts.append(f"[stdout]\n{result.stdout}")
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr}")
    if result.returncode != 0:
        parts.append(f"exit_code: {result.returncode}")
    return "\n".join(parts) if parts else "[execution completed with no output]"


def _unavailable_message(failures: list[str]) -> str:
    detail = "\n".join(f"- {item}" for item in failures)
    return (
        "[blocked] No safe Python execution sandbox is available.\n"
        f"{detail}\n"
        "Configure AGENTNEXUS_E2B_API_KEY, install an OS sandbox/Docker, "
        "or explicitly set code_execution_backend=local_unsafe and "
        "code_execution_allow_unsafe_local=true for trusted code only."
    )


def _execute_locally_with_warning(code: str, timeout: int, failures: list[str]) -> str:
    detail = "\n".join(f"- {item}" for item in failures)
    warning = (
        "[warning] Safe Python execution sandboxes are unavailable; "
        "falling back to unsafe local subprocess execution.\n"
        f"{detail}\n"
        "Only run code you trust in this mode."
    )
    local_result = _execute_locally(code, timeout)
    return f"{warning}\n{local_result}" if local_result else warning


def _disabled_message() -> str:
    return "[blocked] Python code execution is disabled by code_execution_backend=disabled."


def _execute_locally(code: str, timeout: int = 30) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return _format_completed_process(result)
