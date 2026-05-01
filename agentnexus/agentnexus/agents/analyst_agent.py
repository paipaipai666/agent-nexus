from agentnexus.core.llm import AgentLLM


class AnalystAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, task: str, research: str, code: str) -> str:
        prompt = f"""综合以下信息，给用户一个完整、结构化的最终答案。

原始任务: {task}

研究结果: {research[:3000] or "无"}

代码执行结果: {code[:2000] or "无"}

请给出:
1. 核心结论
2. 详细说明
3. 数据支撑（如有）

答案:"""
        return self._llm.think([{"role": "user", "content": prompt}]) or ""
