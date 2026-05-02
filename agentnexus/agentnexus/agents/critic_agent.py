from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt


CRITIC_PROMPT = load_prompt("critic")


class CriticAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def evaluate(self, task: str, answer: str) -> tuple[float, str]:
        try:
            prompt = CRITIC_PROMPT.format(task=task, answer=answer[:3000])
            response = self._llm.think([{"role": "user", "content": prompt}]) or "分数: 5.0\n反馈: 未能评估"
            try:
                score_line = [l for l in response.split("\n") if "分数" in l or "score" in l.lower()]
                if score_line:
                    score_line = score_line[0]
                    if ":" in score_line:
                        score = float(score_line.split(":", 1)[1].strip().split()[0])
                    else:
                        score = 5.0
                else:
                    score = 5.0
            except Exception:
                score = 5.0

            try:
                fb_line = [l for l in response.split("\n") if "反馈" in l or "feedback" in l.lower()]
                if fb_line:
                    fb_line = fb_line[0]
                    feedback = fb_line.split(":", 1)[1].strip() if ":" in fb_line else fb_line
                else:
                    feedback = response[-200:]
            except Exception:
                feedback = response[-200:]

            return score, feedback
        except Exception as e:
            return 5.0, f"评估出错: {e}"
