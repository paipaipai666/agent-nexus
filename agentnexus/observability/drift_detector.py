"""漂移检测器 — 实时检测 Agent 长链路任务中的目标偏离

基于文章《Harness的可观测性》第五章提出的五个漂移信号：
1. 当前动作与原始目标相关度下降
2. 子任务耗时超过预算
3. 重复步骤增多
4. 目标表述发生变化
5. 关键证据未被使用
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DriftSignalType(str, Enum):
    GOAL_DRIFT = "goal_drift"              # 当前动作与原始目标相关度下降
    SUBTASK_OVERRUN = "subtask_overrun"    # 子任务耗时超过预算
    REPEATED_STEPS = "repeated_steps"      # 重复步骤增多
    GOAL_REWRITE = "goal_rewrite"          # 目标表述发生变化
    UNUSED_EVIDENCE = "unused_evidence"    # 关键证据未被使用


class DriftSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DriftSignal:
    """一个漂移检测信号"""
    signal_type: DriftSignalType
    severity: DriftSeverity
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    step_index: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class DriftReport:
    """一次任务的漂移检测报告"""
    trace_id: str
    signals: list[DriftSignal] = field(default_factory=list)
    goal_relevance_scores: list[float] = field(default_factory=list)
    repeated_step_count: int = 0
    subtask_overrun_count: int = 0
    checked_at_steps: list[int] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """无 critical 信号视为通过"""
        return not any(s.severity == DriftSeverity.CRITICAL for s in self.signals)

    @property
    def warning_count(self) -> int:
        return sum(1 for s in self.signals if s.severity == DriftSeverity.WARNING)

    @property
    def critical_count(self) -> int:
        return sum(1 for s in self.signals if s.severity == DriftSeverity.CRITICAL)


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """计算两段文本的关键词重叠率（Jaccard 相似度）"""
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    tokens_a -= {"的", "了", "是", "在", "和", "有", "为", "与", "a", "the", "is", "of", "to", "and"}
    tokens_b -= {"的", "了", "是", "在", "和", "有", "为", "与", "a", "the", "is", "of", "to", "and"}
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


class DriftDetector:
    """轻量级漂移检测器，在 Agent 执行过程中实时调用。

    使用方式：
        detector = DriftDetector(original_goal="分析订单异常原因")

        # 每 N 步调用一次
        signals = detector.check(step_index=5, current_goal="...", tool_calls=[...])
        for signal in signals:
            if signal.severity == "critical":
                # 触发 re-planning 或降级
    """

    # 阈值配置
    RELEVANCE_THRESHOLD = 0.15       # 目标相关度低于此值触发 warning
    RELEVANCE_CRITICAL = 0.05        # 目标相关度低于此值触发 critical
    REPEATED_STEP_THRESHOLD = 3      # 连续相同工具调用次数阈值
    SUBTASK_OVERRUN_RATIO = 0.5      # 子任务步骤占总步骤的比例阈值
    GOAL_REWRITE_THRESHOLD = 0.3     # 目标改写相似度低于此值触发 warning

    def __init__(self, original_goal: str, max_steps: int = 10):
        self.original_goal = original_goal
        self.max_steps = max_steps
        self._tool_call_history: list[tuple[str, str]] = []  # (tool_name, params_hash)
        self._goal_history: list[str] = [original_goal]
        self._step_tool_map: dict[int, str] = {}  # step_index -> tool_name
        self._tool_results: list[tuple[str, str]] = []  # (tool_name, result_summary)
        self._drift_report: DriftReport | None = None

    @property
    def report(self) -> DriftReport:
        return self._drift_report or DriftReport(trace_id="")

    def record_step(self, step_index: int, tool_name: str | None = None,
                    params_hash: str = "", goal: str | None = None,
                    tool_result: str | None = None):
        """记录一步执行信息，用于后续检测。"""
        if tool_name:
            self._tool_call_history.append((tool_name, params_hash))
            self._step_tool_map[step_index] = tool_name
        if goal and goal != self._goal_history[-1]:
            self._goal_history.append(goal)
        if tool_result and tool_name:
            self._tool_results.append((tool_name, tool_result[:500]))

    def check(self, step_index: int, current_goal: str = "",
              tool_calls: list[dict] | None = None) -> list[DriftSignal]:
        """在检查点调用，返回漂移信号列表。

        Args:
            step_index: 当前步骤索引
            current_goal: 当前子目标描述
            tool_calls: 最近的工具调用列表

        Returns:
            检测到的漂移信号列表
        """
        signals: list[DriftSignal] = []

        # 1. 目标相关度检查
        signals.extend(self._check_goal_relevance(step_index, current_goal))

        # 2. 重复步骤检查
        signals.extend(self._check_repeated_steps(step_index))

        # 3. 子任务超支检查
        signals.extend(self._check_subtask_overrun(step_index))

        # 4. 目标改写检查
        signals.extend(self._check_goal_rewrite(step_index, current_goal))

        # 5. 未使用证据检查
        signals.extend(self._check_unused_evidence(step_index))

        return signals

    def _check_goal_relevance(self, step_index: int,
                              current_goal: str) -> list[DriftSignal]:
        """检查当前动作与原始目标的相关度"""
        if not current_goal:
            return []

        relevance = _keyword_overlap(self.original_goal, current_goal)
        signals = []

        if relevance < self.RELEVANCE_CRITICAL:
            signals.append(DriftSignal(
                signal_type=DriftSignalType.GOAL_DRIFT,
                severity=DriftSeverity.CRITICAL,
                detail=f"当前目标与原始目标相关度极低 ({relevance:.2f})",
                evidence={
                    "original_goal": self.original_goal[:200],
                    "current_goal": current_goal[:200],
                    "relevance_score": relevance,
                },
                step_index=step_index,
            ))
        elif relevance < self.RELEVANCE_THRESHOLD:
            signals.append(DriftSignal(
                signal_type=DriftSignalType.GOAL_DRIFT,
                severity=DriftSeverity.WARNING,
                detail=f"当前目标与原始目标相关度较低 ({relevance:.2f})",
                evidence={
                    "original_goal": self.original_goal[:200],
                    "current_goal": current_goal[:200],
                    "relevance_score": relevance,
                },
                step_index=step_index,
            ))

        return signals

    def _check_repeated_steps(self, step_index: int) -> list[DriftSignal]:
        """检查连续相同工具调用"""
        if len(self._tool_call_history) < self.REPEATED_STEP_THRESHOLD:
            return []

        # 检查最近 N 次调用是否完全相同
        recent = self._tool_call_history[-self.REPEATED_STEP_THRESHOLD:]
        if all(call == recent[0] for call in recent):
            return [DriftSignal(
                signal_type=DriftSignalType.REPEATED_STEPS,
                severity=DriftSeverity.WARNING,
                detail=f"连续 {self.REPEATED_STEP_THRESHOLD} 次调用相同工具 '{recent[0][0]}'",
                evidence={
                    "tool_name": recent[0][0],
                    "repeat_count": self.REPEATED_STEP_THRESHOLD,
                },
                step_index=step_index,
            )]

        # 检查工具调用频率（同一工具占比过高）
        tool_names = [t[0] for t in self._tool_call_history[-10:]]
        if tool_names:
            counter = Counter(tool_names)
            most_common, count = counter.most_common(1)[0]
            if count >= 6 and len(tool_names) >= 8:
                return [DriftSignal(
                    signal_type=DriftSignalType.REPEATED_STEPS,
                    severity=DriftSeverity.WARNING,
                    detail=f"最近 {len(tool_names)} 步中 '{most_common}' 被调用 {count} 次",
                    evidence={
                        "tool_name": most_common,
                        "count": count,
                        "window_size": len(tool_names),
                    },
                    step_index=step_index,
                )]

        return []

    def _check_subtask_overrun(self, step_index: int) -> list[DriftSignal]:
        """检查子任务步骤是否超过预算"""
        if self.max_steps <= 0:
            return []

        ratio = step_index / self.max_steps
        if ratio > self.SUBTASK_OVERRUN_RATIO and step_index > 3:
            return [DriftSignal(
                signal_type=DriftSignalType.SUBTASK_OVERRUN,
                severity=DriftSeverity.WARNING,
                detail=f"已用步骤 {step_index}/{self.max_steps} ({ratio:.0%})，超过预算阈值",
                evidence={
                    "step_index": step_index,
                    "max_steps": self.max_steps,
                    "ratio": ratio,
                },
                step_index=step_index,
            )]
        return []

    def _check_goal_rewrite(self, step_index: int,
                            current_goal: str) -> list[DriftSignal]:
        """检查目标是否被改写"""
        if not current_goal or len(self._goal_history) < 2:
            return []

        original = self._goal_history[0]
        similarity = _keyword_overlap(original, current_goal)

        if similarity < self.GOAL_REWRITE_THRESHOLD:
            return [DriftSignal(
                signal_type=DriftSignalType.GOAL_REWRITE,
                severity=DriftSeverity.WARNING,
                detail=f"目标已被改写，与原始目标相似度仅 {similarity:.2f}",
                evidence={
                    "original_goal": original[:200],
                    "current_goal": current_goal[:200],
                    "similarity": similarity,
                    "rewrite_count": len(self._goal_history) - 1,
                },
                step_index=step_index,
            )]
        return []

    def _check_unused_evidence(self, step_index: int) -> list[DriftSignal]:
        """检查工具返回的关键信息是否未被使用

        简单实现：检查最近的工具返回中是否包含异常关键词，
        但后续步骤没有引用这些关键词。
        """
        if len(self._tool_results) < 2:
            return []

        # 检查最近的工具返回
        recent_results = self._tool_results[-3:]
        anomaly_keywords = {"异常", "错误", "失败", "超时", "error", "timeout", "failure", "异常升高"}

        for tool_name, result in recent_results:
            found_keywords = [kw for kw in anomaly_keywords if kw in result.lower()]
            if found_keywords:
                # 检查后续是否有引用（简单检查：后续工具调用的参数中是否包含这些关键词）
                recent_params = [t[1] for t in self._tool_call_history[-2:]]
                recent_params_text = " ".join(recent_params).lower()
                if not any(kw in recent_params_text for kw in found_keywords):
                    return [DriftSignal(
                        signal_type=DriftSignalType.UNUSED_EVIDENCE,
                        severity=DriftSeverity.WARNING,
                        detail=f"工具 '{tool_name}' 返回包含异常信号，但后续步骤未引用",
                        evidence={
                            "tool_name": tool_name,
                            "anomaly_keywords": found_keywords,
                            "result_preview": result[:200],
                        },
                        step_index=step_index,
                    )]

        return []
