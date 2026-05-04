import os
import subprocess
import sys

from agentnexus.core.config import get_settings


def python_execute(code: str) -> str:
    api_key = get_settings().e2b_api_key.get_secret_value()

    if not api_key:
        return _execute_locally(code)

    try:
        os.environ["E2B_API_KEY"] = api_key
        from e2b_code_interpreter import Sandbox
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

    except Exception:
        return _execute_locally(code)


def _execute_locally(code: str, timeout: int = 30) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 and not result.stdout and not result.stderr:
        return f"代码执行错误: exit_code={result.returncode}"

    parts = []
    if result.stdout:
        parts.append(f"[stdout]\n{result.stdout}")
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr}")
    return "\n".join(parts) if parts else "[execution completed with no output]"
