from e2b_code_interpreter import Sandbox

from agentnexus.core.config import get_settings


def python_execute(code: str) -> str:
    api_key = get_settings().e2b_api_key.get_secret_value()

    try:
        with Sandbox(api_key=api_key) as sandbox:
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
        return f"代码执行错误: {e}"