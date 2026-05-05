"""Agent 输出 Schema、来源引用、错误分类 — 整个系统的硬约束基础。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceClaim(BaseModel):
    claim: str = Field(..., description="具体事实声明")
    source: str = Field(..., description="来源标识: 'web' / 'local' / 文档名")
    url: str = Field(default="", description="来源 URL（web 搜索时有值）")
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


# RETRY_STRATEGIES removed — escalation logic migrated to orchestrator._get_escalated_instruction().
# ErrorType enum kept: still used by ExecutorAgent, CoderAgent, and orchestrator route logic.
