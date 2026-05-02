from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt


ANALYST_PROMPT = load_prompt("analyst")


class AnalystAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, task: str, research: str, code: str) -> str:
        prompt = ANALYST_PROMPT.format(task=task, research=research[:3000] or "无",
                                        code=code[:2000] or "无")
        return self._llm.think([{"role": "user", "content": prompt}]) or ""
