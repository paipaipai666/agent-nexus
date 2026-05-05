from agentnexus.core.llm import get_default_llm
from agentnexus.prompts import load_prompt

ANALYST_PROMPT = load_prompt("analyst")


class AnalystAgent:
    def __init__(self):
        self._llm = get_default_llm()

    def run(self, task: str, research: str, code: str) -> str:
        prompt = ANALYST_PROMPT.format(task=task, research=research or "无",
                                        code=code or "无")
        try:
            return self._llm.think([{"role": "user", "content": prompt}]) or ""
        except Exception as e:
            return f"分析出错: {e}"
