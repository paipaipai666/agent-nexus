"""Hard pre-LLM critic rules — fail fast on clear violations before LLM scoring.

Usage:
    checker = HardRuleChecker()
    verdict = checker.run_hard_checks(
        task="写一个Python函数计算斐波那契数列",
        task_requires_code=True,
        output_code=None,
        exec_result=ExecutionResult(success=False, stderr="SyntaxError"),
    )
    if verdict is not None:
        # block LLM scoring, verdict.passed is False
"""

from typing import Optional

from agentnexus.agents.schema import (
    CriticVerdict,
    ExecutionResult,
    SourceClaim,
)

# ── Keyword sets for task-type heuristics ──────────────────────────────
_CODE_KEYWORDS = {
    "代码", "code", "python", "函数", "function",
    "编程", "写代码", "画图", "绘图", "图表", "chart", "plot", "matplotlib", "import", "实现",
}
_RESEARCH_KEYWORDS = {"搜索", "search", "查找", "find", "汇率", "数据", "research", "查"}


def _task_requires_code(task: str) -> bool:
    """Heuristic: does *task* sound like it needs code output?"""
    lower = task.lower()
    return any(kw in task or kw in lower for kw in _CODE_KEYWORDS)


def _task_requires_research(task: str) -> bool:
    """Heuristic: does *task* sound like it needs external research?"""
    lower = task.lower()
    return any(kw in task or kw in lower for kw in _RESEARCH_KEYWORDS)


class HardRuleChecker:
    """Deterministic rule checks that run *before* any LLM-based scoring.

    Each check returns ``None`` (pass) or a ``CriticVerdict`` with
    ``passed=False``, ``score=0.0``, and a clear ``fail_reason``.
    """

    # ── Individual rule checks ─────────────────────────────────────────

    @staticmethod
    def check_missing_code(
        task_requires_code: bool,
        output_code: Optional[str],
    ) -> Optional[CriticVerdict]:
        """Fail if the task likely needs code but none was produced."""
        if task_requires_code and (output_code is None or output_code.strip() == ""):
            return CriticVerdict(
                passed=False,
                score=0.0,
                fail_reason="任务需要代码输出但未提供代码",
                feedback="Task appears to require code output, yet no code was produced.",
            )
        return None

    @staticmethod
    def check_source_citation(
        task_requires_research: bool,
        sources: list[SourceClaim],
    ) -> Optional[CriticVerdict]:
        """Fail if a research-type task returned zero source citations."""
        if task_requires_research and len(sources) == 0:
            return CriticVerdict(
                passed=False,
                score=0.0,
                fail_reason="检索类任务缺少来源引用",
                feedback="The task required research/search, but no sources were cited.",
            )
        return None

    @staticmethod
    def check_runtime_error(
        exec_result: Optional[ExecutionResult],
    ) -> Optional[CriticVerdict]:
        """Fail if the execution result signals a runtime exception."""
        if exec_result is not None and not exec_result.success and exec_result.exception:
            return CriticVerdict(
                passed=False,
                score=0.0,
                fail_reason=f"代码运行时发生异常: {exec_result.exception}",
                feedback=(
                    f"Execution failed with exception:\n{exec_result.exception}"
                ),
            )
        return None

    @staticmethod
    def check_no_output(
        exec_result: Optional[ExecutionResult],
    ) -> Optional[CriticVerdict]:
        """Fail if code ran but produced neither stdout nor stderr."""
        if exec_result is not None and exec_result.stdout == "" and exec_result.stderr == "":
            return CriticVerdict(
                passed=False,
                score=0.0,
                fail_reason="代码执行无任何输出",
                feedback="Code executed but produced no output — missing print() or return value.",
            )
        return None

    @staticmethod
    def check_empty_result(output_result: str) -> Optional[CriticVerdict]:
        """Fail if the final result string is blank."""
        if output_result.strip() == "":
            return CriticVerdict(
                passed=False,
                score=0.0,
                fail_reason="结果为空",
                feedback="The output result is empty or contains only whitespace.",
            )
        return None

    def run_hard_checks(
        self,
        task: str = "",
        *,
        task_requires_code: Optional[bool] = None,
        task_requires_research: Optional[bool] = None,
        output_code: Optional[str] = None,
        output_result: str = "",
        sources: Optional[list[SourceClaim]] = None,
        exec_result: Optional[ExecutionResult] = None,
    ) -> Optional[CriticVerdict]:
        """Run all applicable hard checks in priority order.

        Parameters
        ----------
        task : str
            Original task prompt (used for keyword auto-detection when the
            boolean flags are left as ``None``).
        task_requires_code : bool or None
            Explicit override for code requirement.  ``None`` = auto-detect
            from *task* keyword heuristics.
        task_requires_research : bool or None
            Explicit override for research requirement.  ``None`` = auto-detect
            from *task* keyword heuristics.
        output_code : str or None
            The code produced by the agent (``None`` = no code).
        output_result : str
            The final text result.
        sources : list[SourceClaim] or None
            Source citations collected by the agent.
        exec_result : ExecutionResult or None
            Structured execution result from the executor.

        Returns
        -------
        CriticVerdict or None
            The **first** failing verdict, or ``None`` when all checks pass.
        """
        # Resolve auto-detect flags (only if the explicit value is None)
        if task_requires_code is None:
            task_requires_code = _task_requires_code(task)
        if task_requires_research is None:
            task_requires_research = _task_requires_research(task)

        safe_sources: list[SourceClaim] = sources if sources is not None else []

        # ── Priority order ─────────────────────────────────────────
        #  1. Code requirements
        #  2. Source citations (research integrity)
        #  3. Runtime errors
        #  4. Silent execution
        #  5. Empty final result

        verdict: Optional[CriticVerdict]

        verdict = self.check_missing_code(task_requires_code, output_code)
        if verdict is not None:
            return verdict

        verdict = self.check_source_citation(task_requires_research, safe_sources)
        if verdict is not None:
            return verdict

        verdict = self.check_runtime_error(exec_result)
        if verdict is not None:
            return verdict

        verdict = self.check_no_output(exec_result)
        if verdict is not None:
            return verdict

        verdict = self.check_empty_result(output_result)
        if verdict is not None:
            return verdict

        return None  # all passed
