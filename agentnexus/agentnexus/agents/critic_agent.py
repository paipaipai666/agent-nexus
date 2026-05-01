from agentnexus.core.llm import AgentLLM


class CriticAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def evaluate(self, task: str, answer: str) -> tuple[float, str]:
        prompt = f"""评估以下答案的质量。

原始任务: {task}

待评答案: {answer[:3000]}

请严格按以下格式输出:
分数: X.X  (0-10分，10分为完美)
反馈: <具体的改进建议或通过理由>

评分标准:
- 完整性(40%): 是否覆盖了任务的所有要求
- 准确性(30%): 信息是否准确可靠
- 清晰度(20%): 表达是否清晰易懂
- 实用性(10%): 是否可直接使用

评分:"""
        response = self._llm.think([{"role": "user", "content": prompt}]) or "分数: 5.0\n反馈: 未能评估"
        try:
            score_line = [l for l in response.split("\n") if "分数" in l or "score" in l.lower()][0]
            score = float(score_line.split(":")[1].strip().split()[0])
            score = max(0.0, min(10.0, score))
        except Exception:
            score = 5.0

        try:
            fb_line = [l for l in response.split("\n") if "反馈" in l or "feedback" in l.lower()][0]
            feedback = fb_line.split(":", 1)[1].strip() if ":" in fb_line else fb_line
        except Exception:
            feedback = response[-200:]

        return score, feedback
