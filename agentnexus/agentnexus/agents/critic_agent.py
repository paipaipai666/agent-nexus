"""Critic Agent — 硬规则先行 + LLM 质量评分。

新流程:
1. HardRuleChecker 运行硬规则检查
2. 硬规则不通过 → 直接返回 FAIL 判定
3. 硬规则通过 → LLM 只负责质量和细节评分

LLM 只负责"质量"，不负责"生死"。
"""

from __future__ import annotations

from typing import Optional

from agentnexus.agents.critic_rules import HardRuleChecker
from agentnexus.agents.schema import (
    CriticVerdict,
    ExecutionResult,
    SourceClaim,
)
from agentnexus.core.llm import get_default_llm
from agentnexus.prompts import get_current_date, load_prompt

CRITIC_PROMPT = load_prompt("critic")

PASS_THRESHOLD = 7.0


class CriticAgent:
    def __init__(self):
        self._llm = get_default_llm()
        self._hard_checker = HardRuleChecker()
        self.last_verdict: Optional[CriticVerdict] = None
        self.last_score: float = 0.0
        self.last_feedback: str = ""

    def evaluate(
        self,
        task: str,
        answer: str,
        *,
        output_code: Optional[str] = None,
        sources: Optional[list[SourceClaim]] = None,
        exec_result: Optional[ExecutionResult] = None,
        task_requires_code: Optional[bool] = None,
        task_requires_research: Optional[bool] = None,
    ) -> tuple[float, str, Optional[CriticVerdict], str]:
        """评估答案质量。

        步骤:
        1. 硬规则检查（代码缺失 / 无来源 / 运行时错误 / 无输出 / 空结果）
        2. 硬规则不通过 → 直接返回 FAIL
        3. 硬规则通过 → LLM 质量评分

        Returns:
            (score, feedback, hard_verdict, fail_type)
            fail_type: "code_error" | "info_insufficient" | "analysis_incomplete" | "replan"
        """
        # Step 1: 硬规则检查
        hard_verdict = self._hard_checker.run_hard_checks(
            task=task,
            task_requires_code=task_requires_code,
            task_requires_research=task_requires_research,
            output_code=output_code,
            output_result=answer,
            sources=sources or [],
            exec_result=exec_result,
        )

        if hard_verdict is not None:
            self.last_verdict = hard_verdict
            self.last_score = 0.0
            self.last_feedback = hard_verdict.fail_reason
            return 0.0, hard_verdict.fail_reason, hard_verdict, "replan"

        # Step 2: LLM 质量评分（只评价质量，不决定生死）
        score, feedback, fail_type = self._llm_score(
            task, answer, exec_result=exec_result, output_code=output_code,
        )
        passed = score >= PASS_THRESHOLD

        self.last_verdict = CriticVerdict(
            passed=passed,
            score=score,
            feedback=feedback,
        )
        self.last_score = score
        self.last_feedback = feedback

        return score, feedback, None, fail_type

    def _llm_score(
        self, task: str, answer: str, *,
        exec_result: Optional[ExecutionResult] = None,
        output_code: Optional[str] = None,
    ) -> tuple[float, str, str]:
        try:
            if exec_result is not None:
                status = "成功" if exec_result.success else "失败"
                exec_summary = f"执行状态: {status}\n"
                if exec_result.stdout:
                    exec_summary += f"实际 stdout:\n```\n{exec_result.stdout[:4000]}\n```\n"
                if exec_result.stderr:
                    exec_summary += f"实际 stderr:\n```\n{exec_result.stderr[:500]}\n```\n"
                if exec_result.exception:
                    exec_summary += f"异常: {exec_result.exception[:500]}\n"
            else:
                exec_summary = "（无代码执行）"

            # Only pass a tiny code preview for context — the execution output is the truth
            code_preview = (output_code or "（无代码）")[:300]

            prompt = CRITIC_PROMPT.format(
                task=task, answer=answer[:3000], date=get_current_date(),
                exec_result_summary=exec_summary,
                output_code_preview=code_preview,
            )
            response = (
                self._llm.think([{"role": "user", "content": prompt}], silent=True)
                or '{"score": 5.0, "feedback": "未能评估", "fail_type": "replan"}'
            )

            best_score = 5.0
            best_feedback = "未能解析评估结果"
            best_fail_type = "replan"

            # Primary: JSON parsing
            import json as _json
            import re as _re
            json_text = None
            match = _re.search(r"```json\s*\n?(.*?)```", response, _re.DOTALL)
            if match:
                json_text = match.group(1).strip()
            elif response.strip().startswith("{"):
                json_text = response.strip()

            if json_text:
                try:
                    data = _json.loads(json_text)
                    best_score = float(data.get("score", 5.0))
                    best_feedback = data.get("feedback") or data.get("reasoning") or ""
                    best_fail_type = data.get("fail_type", "replan")
                    if best_fail_type not in ("code_error", "info_insufficient", "analysis_incomplete", "replan"):
                        best_fail_type = "replan"
                    if best_feedback:
                        return min(max(best_score, 0.0), 10.0), best_feedback, best_fail_type
                    best_feedback = "评估完成但未提供详细反馈"
                    return min(max(best_score, 0.0), 10.0), best_feedback, best_fail_type
                except (_json.JSONDecodeError, TypeError, ValueError):
                    pass

            for line in response.split("\n"):
                stripped = line.strip()
                if "分数" in stripped or "score" in stripped.lower():
                    if ":" in stripped:
                        try:
                            best_score = float(stripped.split(":", 1)[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass
                elif "反馈" in stripped or "feedback" in stripped.lower():
                    if ":" in stripped:
                        best_feedback = stripped.split(":", 1)[1].strip()

            return min(max(best_score, 0.0), 10.0), best_feedback, best_fail_type

        except Exception as exc:
            return 5.0, f"评估出错: {exc}", "replan"
