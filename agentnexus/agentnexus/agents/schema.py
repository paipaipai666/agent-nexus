"""Agent 输出 Schema、来源引用、错误分类 — 整个系统的硬约束基础。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceClaim(BaseModel):
    claim: str = Field(..., description="具体事实声明")
    source: str = Field(..., description="来源: URL / 文档路径 / 工具名称")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度 0-1")


class TaskOutput(BaseModel):
    reasoning: str = Field(..., description="推理过程 / 分析逻辑")
    code: Optional[str] = Field(default=None, description="可执行代码（仅 Coder 输出）")
    result: str = Field(..., description="最终结果文本")
    sources: list[SourceClaim] = Field(default_factory=list, description="引用来源列表")


class ResearchOutput(BaseModel):
    summary: str = Field(..., description="检索综合摘要")
    claims: list[SourceClaim] = Field(default_factory=list, description="每条声明带来源")
    gaps: str = Field(default="", description="信息缺口说明")


class CodeOutput(BaseModel):
    reasoning: str = Field(..., description="代码设计思路")
    code: str = Field(..., description="完整可执行 Python 代码")
    expected_output: str = Field(default="", description="预期的运行输出描述")


class ExecutionResult(BaseModel):
    success: bool = Field(..., description="代码是否成功运行")
    stdout: str = Field(default="", description="标准输出")
    stderr: str = Field(default="", description="标准错误")
    exception: str = Field(default="", description="异常信息（如有）")
    exit_code: int = Field(default=0, description="退出码")


class CriticVerdict(BaseModel):
    passed: bool = Field(..., description="是否通过")
    score: float = Field(default=0.0, ge=0.0, le=10.0, description="0-10 评分")
    feedback: str = Field(default="", description="改进建议或通过理由")
    fail_reason: str = Field(default="", description="如果未通过，硬规则失败原因")


class OutputDiff(BaseModel):
    matched: bool = Field(default=True, description="输出是否匹配预期")
    expected: str = Field(default="", description="预期输出（摘要）")
    actual: str = Field(default="", description="实际输出（摘要）")
    detail: str = Field(default="", description="详细差异说明")


class ErrorType(str, Enum):
    MISSING_CODE = "missing_code"
    RUNTIME_ERROR = "runtime_error"
    HALLUCINATION = "hallucination"
    TOOL_FAILURE = "tool_failure"
    SCHEMA_VIOLATION = "schema_violation"
    NO_OUTPUT = "no_output"
    EMPTY_RESULT = "empty_result"
    LOGIC_ERROR = "logic_error"
    TRUNCATION = "truncation"


RETRY_STRATEGIES: dict[ErrorType, dict] = {
    ErrorType.MISSING_CODE: {
        "strategy": "force_code_only",
        "max_retries": 2,
        "instruction": "必须只输出可执行 Python 代码，禁止任何解释文字。",
    },
    ErrorType.RUNTIME_ERROR: {
        "strategy": "feed_error_back",
        "max_retries": 3,
        "instruction": "代码运行出错，请根据错误信息修复代码。",
    },
    ErrorType.HALLUCINATION: {
        "strategy": "force_retrieval",
        "max_retries": 2,
        "instruction": "检测到编造数据。必须重新检索，每句话都要标注来源。禁止生成任何未在检索结果中出现的信息。",
    },
    ErrorType.TOOL_FAILURE: {
        "strategy": "fallback",
        "max_retries": 1,
        "instruction": "工具调用失败，使用 fallback 方案。",
    },
    ErrorType.SCHEMA_VIOLATION: {
        "strategy": "retry_with_schema",
        "max_retries": 2,
        "instruction": "输出格式不符合要求，请严格按照指定 Schema 重新输出。",
    },
    ErrorType.NO_OUTPUT: {
        "strategy": "force_execution",
        "max_retries": 2,
        "instruction": (
            "代码执行无任何输出。请确保: "
            "1) 所有函数在代码顶层被调用(加 `if __name__ == '__main__':` 块); "
            "2) print() 语句在模块层级执行而不仅仅定义在函数内。"
        ),
    },
    ErrorType.EMPTY_RESULT: {
        "strategy": "force_execution",
        "max_retries": 2,
        "instruction": "结果为空白，请生成有效内容。确保定义了测试数据并在顶层调用评估函数。",
    },
    ErrorType.LOGIC_ERROR: {
        "strategy": "fix_logic",
        "max_retries": 3,
        "instruction": "代码输出与预期不符。请根据预期输出和执行差异修复代码逻辑。",
    },
    ErrorType.TRUNCATION: {
        "strategy": "simplify",
        "max_retries": 2,
        "instruction": (
            "上一次输出因长度限制被截断。"
            "请将代码压缩到 800 字符以内：删除所有注释和类型标注、用缩写变量名、"
            "plt.show() 改为 plt.savefig('out.png')，删除不必要的格式化。"
        ),
    },
}
