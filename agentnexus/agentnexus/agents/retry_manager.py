"""Error classification + differentiated retry with same-error escalation.

Tracks repeated error types and escalates strategy when the same failure
persists across retries (e.g. ModuleNotFoundError → suggest fallback,
missing code 2x → force code-only with stripped context).
"""

from __future__ import annotations

import re
from typing import Optional

from agentnexus.agents.schema import (
    RETRY_STRATEGIES,
    CriticVerdict,
    ErrorType,
    ExecutionResult,
)


class RetryManager:
    """Classify errors + produce escalating retry instructions."""

    _SCHEMA_HINTS = frozenset({"格式", "format", "schema", "json"})

    def __init__(self):
        self._error_history: list[ErrorType] = []

    def classify_error(
        self,
        critic_verdict: CriticVerdict,
        exec_result: Optional[ExecutionResult] = None,
        has_code: bool = False,
        has_sources: bool = False,
    ) -> ErrorType:
        if exec_result is not None and exec_result.exception:
            return _error_from_exception(exec_result.exception)
        if exec_result is not None and not exec_result.stdout and not exec_result.stderr:
            return ErrorType.NO_OUTPUT
        if not has_code:
            return ErrorType.MISSING_CODE
        if not has_sources:
            return ErrorType.HALLUCINATION
        reason = (critic_verdict.fail_reason or "").lower()
        if any(hint in reason for hint in RetryManager._SCHEMA_HINTS):
            return ErrorType.SCHEMA_VIOLATION
        return _infer_from_fail_reason(critic_verdict)

    def build_retry_instruction(self, error_type: ErrorType, last_error: str = "") -> str:
        count = sum(1 for e in self._error_history if e == error_type)
        strategy = RETRY_STRATEGIES[error_type]
        instruction = strategy["instruction"]

        if count >= 1:
            instruction = _escalate(error_type, count + 1, last_error)
        if last_error:
            instruction += f"\n上次错误: {last_error[:300]}"
        return instruction

    def record_error(self, error_type: ErrorType) -> None:
        self._error_history.append(error_type)

    def should_retry(self, error_type: ErrorType, attempt_count: int) -> bool:
        return attempt_count < RETRY_STRATEGIES[error_type]["max_retries"]

    def get_strategy(self, error_type: ErrorType) -> dict:
        return RETRY_STRATEGIES[error_type]


def _error_from_exception(exception: str) -> ErrorType:
    if "ModuleNotFoundError" in exception or "ImportError" in exception:
        return ErrorType.TOOL_FAILURE
    return ErrorType.RUNTIME_ERROR


def _escalate(error_type: ErrorType, occurrences: int, last_error: str) -> str:
    """Escalate strategy when same error repeats."""
    if error_type == ErrorType.TOOL_FAILURE:
        module = _extract_missing_module(last_error)
        if module:
            return (
                f"上次代码因缺少 {module} 库而失败（已发生 {occurrences} 次）。"
                f"不要使用 {module}，改用纯 Python 标准库实现。"
                f"例如：用 print/列表/表格 代替 图表，用 collections.Counter 代替 nltk。"
            )
        return (
            f"代码因缺少外部依赖而失败（已发生 {occurrences} 次）。"
            f"使用纯 Python 标准库重写，不要依赖任何 pip 安装的第三方库。"
        )

    if error_type == ErrorType.RUNTIME_ERROR:
        return (
            f"代码运行时错误已重复 {occurrences} 次。请生成一个最小可运行版本："
            f"去掉所有不必要的功能，只保留核心逻辑。优先输出 print() 结果而非图形。"
        )

    if error_type == ErrorType.LOGIC_ERROR:
        return (
            f"代码逻辑错误已重复 {occurrences} 次。"
            f"请重新审视任务需求，确保：\n"
            f"1. 代码输出的数据是否与搜索结果或任务要求一致\n"
            f"2. 变量命名是否正确，没有 self.x = value 应为 self.x[key] = value 等低级错误\n"
            f"3. print() 语句是否输出了完整结果（不要省略）\n"
            f"上次错误详情: {last_error[:300]}"
        )

    if error_type == ErrorType.MISSING_CODE:
        return (
            f"已连续 {occurrences} 次未生成代码。强制要求：只输出 Python 代码，"
            f"不要任何解释文字。代码必须包含 print() 语句输出结果。"
        )

    if error_type == ErrorType.HALLUCINATION:
        return (
            f"数据错误已发生 {occurrences} 次。禁止生成任何未在检索结果中出现的数字或事实。"
            f"只使用搜索返回的确切数据，不要自己估算。"
        )

    strategy = RETRY_STRATEGIES[error_type]
    return f"{strategy['instruction']}（已重复 {occurrences} 次）"


def _extract_missing_module(error_text: str) -> Optional[str]:
    match = re.search(r"No module named '(\w+)'", error_text)
    if match:
        return match.group(1)
    match = re.search(r"cannot import name|ImportError.*?['\"](\w+)['\"]", error_text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _infer_from_fail_reason(critic_verdict: CriticVerdict) -> ErrorType:
    reason = ((critic_verdict.fail_reason or "") + " " + (critic_verdict.feedback or "")).casefold()
    if any(k in reason for k in ("missing", "缺失", "缺少", "未提供", "无代码", "没有代码")):
        return ErrorType.MISSING_CODE
    if any(k in reason for k in (
        "runtime", "exception", "traceback", "syntaxerror",
        "异常", "报错", "运行失败", "执行失败",
    )):
        return ErrorType.RUNTIME_ERROR
    if any(k in reason for k in ("schema", "format", "格式", "json", "结构不符合")):
        return ErrorType.SCHEMA_VIOLATION
    if any(k in reason for k in ("输出与预期不符", "output mismatch", "logic error", "逻辑错误")):
        return ErrorType.LOGIC_ERROR
    if any(k in reason for k in (
        "hallucinat", "source", "citation",
        "编造", "虚构", "不符合", "不实", "捏造", "不准确", "准确性", "错误", "数据错误",
    )):
        return ErrorType.HALLUCINATION
    if any(k in reason for k in ("tool", "timeout", "超时")):
        return ErrorType.TOOL_FAILURE
    if any(k in reason for k in (
        "no output", "empty", "blank",
        "无输出", "无任何", "没有任何", "没有输出", "空白", "空结果",
    )):
        return ErrorType.NO_OUTPUT
    return ErrorType.EMPTY_RESULT


def classify_and_decide(
    critic_verdict: CriticVerdict,
    exec_result: Optional[ExecutionResult] = None,
    has_code: bool = False,
    has_sources: bool = False,
    attempt_count: int = 0,
) -> tuple[ErrorType, bool, str]:
    mgr = RetryManager()
    error_type = mgr.classify_error(critic_verdict, exec_result, has_code, has_sources)
    can_retry = mgr.should_retry(error_type, attempt_count)
    instruction = mgr.build_retry_instruction(error_type) if can_retry else ""
    return error_type, can_retry, instruction
