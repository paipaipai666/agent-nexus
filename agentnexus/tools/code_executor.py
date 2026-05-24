import ast
import os
import re
import subprocess
import sys

from agentnexus.core.config import get_settings

try:
    from e2b_code_interpreter import Sandbox
except Exception:
    Sandbox = None

_HAS_MAIN_RE = re.compile(r'^if\s+__name__\s*==\s*["\']__main__["\']', re.MULTILINE)


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
    code = _ensure_main_block(code)
    api_key = get_settings().e2b_api_key.get_secret_value()

    if not api_key:
        return _execute_locally(code)

    if Sandbox is None:
        return _execute_locally_with_warning(code, "E2B sandbox 不可用")

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
    except Exception as e:
        return _execute_locally_with_warning(code, f"E2B sandbox 失败: {e}")
    finally:
        if previous_api_key is None:
            os.environ.pop("E2B_API_KEY", None)
        else:
            os.environ["E2B_API_KEY"] = previous_api_key


def _execute_locally_with_warning(code: str, reason: str) -> str:
    local_result = _execute_locally(code)
    warning = f"[warning] {reason}; falling back to local execution."
    return f"{warning}\n{local_result}" if local_result else warning


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
