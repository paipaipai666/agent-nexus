from agentnexus.core.llm import AgentLLM
from agentnexus.tools.code_executor import python_execute
from agentnexus.prompts import load_prompt


CODER_PROMPT = load_prompt("coder")


class CoderAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, spec: str) -> str:
        try:
            prompt = CODER_PROMPT.format(spec=spec)
            code = self._llm.think([{"role": "user", "content": prompt}]) or ""
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0]
            result = python_execute(code)
            return str(result)
        except Exception as e:
            return f"代码执行出错: {e}"
