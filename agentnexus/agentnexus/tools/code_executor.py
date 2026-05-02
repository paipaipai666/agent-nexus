import os

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


def _execute_locally(code: str) -> str:
    import io
    import sys
    stdout = io.StringIO()
    stderr = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    namespace = {}
    try:
        sys.stdout = stdout
        sys.stderr = stderr
        exec(code, namespace)
    except Exception as e:
        return f"代码执行错误: {e}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    out = stdout.getvalue()
    err = stderr.getvalue()
    parts = []
    if out:
        parts.append(f"[stdout]\n{out}")
    if err:
        parts.append(f"[stderr]\n{err}")
    return "\n".join(parts) if parts else "[execution completed with no output]"
