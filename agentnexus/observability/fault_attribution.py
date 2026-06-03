"""工具调用故障归因 — 结构化定位工具调用失败的根因

基于文章《Harness的可观测性》第六章提出的五类故障：
1. 工具选择错误 — 模型选错了工具
2. 参数生成错误 — 参数格式/值不正确
3. 权限边界错误 — 调用了不该调用的操作
4. 工具服务异常 — 工具本身超时/500/依赖挂了
5. 结果理解错误 — 工具返回正确但模型理解错了
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FaultType(str, Enum):
    TOOL_SELECTION = "tool_selection"          # 选错工具
    PARAM_GENERATION = "param_generation"      # 参数错误
    PERMISSION_BOUNDARY = "permission"         # 权限越界
    TOOL_SERVICE = "tool_service"              # 工具服务异常
    RESULT_UNDERSTANDING = "result_understanding"  # 结果理解错误


class FaultSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class FaultRecord:
    """一条工具调用故障记录"""
    fault_type: FaultType
    tool_name: str
    detail: str
    severity: FaultSeverity = FaultSeverity.MEDIUM
    evidence: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    step_index: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class FaultAttributionReport:
    """一次任务的故障归因报告"""
    trace_id: str
    faults: list[FaultRecord] = field(default_factory=list)
    total_tool_calls: int = 0
    fault_count: int = 0

    @property
    def fault_rate(self) -> float:
        return self.fault_count / max(1, self.total_tool_calls)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == FaultSeverity.CRITICAL for f in self.faults)

    @property
    def by_type(self) -> dict[str, int]:
        """按故障类型统计"""
        counts: dict[str, int] = {}
        for f in self.faults:
            counts[f.fault_type.value] = counts.get(f.fault_type.value, 0) + 1
        return counts

    @property
    def passed(self) -> bool:
        return not self.has_critical and self.fault_rate < 0.3


def classify_tool_fault(
    tool_name: str,
    error_message: str | None,
    params: dict | None = None,
    available_tools: list[str] | None = None,
    result_summary: str | None = None,
    caller_intent: str | None = None,
) -> FaultRecord | None:
    """根据工具调用的上下文信息，分类故障类型。

    Args:
        tool_name: 被调用的工具名
        error_message: 错误信息（如果有）
        params: 调用参数
        available_tools: 可用工具列表
        result_summary: 工具返回摘要
        caller_intent: 调用者意图描述

    Returns:
        分类后的 FaultRecord，如果无法分类则返回 None
    """
    if error_message is None:
        return None

    error_lower = error_message.lower()

    # 1. 权限边界错误
    if any(kw in error_lower for kw in ("permission", "blocked", "not allowed", "rbac", "hitl")):
        return FaultRecord(
            fault_type=FaultType.PERMISSION_BOUNDARY,
            tool_name=tool_name,
            detail=error_message[:500],
            severity=FaultSeverity.HIGH,
            evidence={"error": error_message[:200]},
        )

    # 2. 参数生成错误
    if any(kw in error_lower for kw in ("validation", "schema", "invalid param", "missing param",
                                         "required", "type error", "json")):
        return FaultRecord(
            fault_type=FaultType.PARAM_GENERATION,
            tool_name=tool_name,
            detail=error_message[:500],
            severity=FaultSeverity.MEDIUM,
            evidence={"error": error_message[:200], "params": str(params)[:200] if params else ""},
        )

    # 3. 工具服务异常
    if any(kw in error_lower for kw in ("timeout", "timed out", "connection", "500",
                                         "server error", "unavailable", "refused")):
        return FaultRecord(
            fault_type=FaultType.TOOL_SERVICE,
            tool_name=tool_name,
            detail=error_message[:500],
            severity=FaultSeverity.MEDIUM,
            evidence={"error": error_message[:200]},
        )

    # 4. 工具选择错误（如果调用者意图和工具不匹配）
    if caller_intent and available_tools:
        # 简单的关键词匹配检查
        intent_lower = caller_intent.lower()
        tool_lower = tool_name.lower()
        # 如果意图中提到的关键词和工具名完全不相关
        intent_tokens = set(intent_lower.split())
        tool_tokens = set(tool_lower.replace("_", " ").split())
        overlap = intent_tokens & tool_tokens
        if not overlap and error_message:
            return FaultRecord(
                fault_type=FaultType.TOOL_SELECTION,
                tool_name=tool_name,
                detail=f"工具 '{tool_name}' 可能不是处理 '{caller_intent[:50]}' 的最佳选择",
                severity=FaultSeverity.LOW,
                evidence={
                    "intent": caller_intent[:200],
                    "tool": tool_name,
                    "available_tools": (available_tools or [])[:10],
                },
            )

    # 5. 默认归类为工具服务异常
    return FaultRecord(
        fault_type=FaultType.TOOL_SERVICE,
        tool_name=tool_name,
        detail=error_message[:500],
        severity=FaultSeverity.MEDIUM,
        evidence={"error": error_message[:200]},
    )
