from agentnexus.core.llm import AgentLLM
from agentnexus.tools.code_executor import python_execute


class CoderAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, spec: str) -> str:
        prompt = f"""根据以下需求编写可执行的 Python 代码。只输出代码，不要解释。

需求: {spec}

```python
"""
        code = self._llm.think([{"role": "user", "content": prompt}]) or ""
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        result = python_execute(code)
        return str(result)
