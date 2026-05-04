"""Executor Agent — execution sandbox with auto-dependency installation.

Independent of tools/code_executor.py. Captures stdout/stderr/exceptions.
When ModuleNotFoundError occurs, attempts pip install then re-executes.
"""

from __future__ import annotations

import builtins
import io
import subprocess
import sys
import traceback as _traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional

from agentnexus.agents.schema import ErrorType, ExecutionResult


class ExecutorAgent:
    def execute(self, code: str, timeout: int = 30) -> ExecutionResult:
        result = _run_code(code, timeout)
        if not result.success and "ModuleNotFoundError" in (result.exception or ""):
            module = _extract_missing_module(result.exception)
            if module:
                installed = _pip_install(module)
                if installed:
                    result = _run_code(code, timeout)
                    if result.success:
                        result.stderr = (
                            f"[auto-installed {module}]\n" + (result.stderr or "")
                        )
        return result

    def validate(self, result: ExecutionResult) -> Optional[ErrorType]:
        if not result.success and result.exception:
            return _classify_exception(result.exception)
        if not result.stdout and not result.stderr:
            return ErrorType.NO_OUTPUT
        return None


def _run_code(code: str, timeout: int) -> ExecutionResult:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    namespace: dict[str, object] = {"__builtins__": builtins}

    def _run() -> None:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            exec(code, namespace)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            future.result(timeout=timeout)
    except FutureTimeoutError:
        return ExecutionResult(
            success=False,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            exception=f"Execution timed out after {timeout}s",
            exit_code=1,
        )
    except Exception:
        return ExecutionResult(
            success=False,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            exception=_extract_error(_traceback.format_exc()),
            exit_code=1,
        )

    stdout = stdout_buf.getvalue()
    stderr = stderr_buf.getvalue()

    if not stdout and not stderr:
        return ExecutionResult(
            success=False, stdout="", stderr="",
            exception="NO_OUTPUT: code executed without error but produced no stdout or stderr",
            exit_code=1,
        )

    return ExecutionResult(
        success=True, stdout=stdout, stderr=stderr, exception="", exit_code=0
    )


def _pip_install(module: str) -> bool:
    """Try to pip install a module. Returns True if successful."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", module, "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def _extract_missing_module(error_text: str) -> Optional[str]:
    import re
    match = re.search(r"No module named '(\w+)'", error_text)
    if match:
        return match.group(1)
    return None


def _extract_error(traceback_text: str) -> str:
    lines = traceback_text.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if any(err in stripped for err in ("Error:", "Error ", "Exception:", "Exception ")):
            return stripped
    return lines[-1] if lines else ""


def _classify_exception(exception: str) -> ErrorType:
    if "ModuleNotFoundError" in exception or "ImportError" in exception:
        return ErrorType.TOOL_FAILURE
    if "SyntaxError" in exception:
        return ErrorType.RUNTIME_ERROR
    return ErrorType.RUNTIME_ERROR
