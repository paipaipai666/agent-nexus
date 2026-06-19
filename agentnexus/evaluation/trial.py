"""Trial 运行模型 — 单次任务执行及其评分结果。

对应 Anthropic 方法论中的 "trial"（一次任务尝试）和 "grader"（评分逻辑）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Grader Score
# ---------------------------------------------------------------------------

@dataclass
class GraderScore:
    """单个评分器的结果。

    Attributes:
        name: 评分器名称
        grader_type: 评分器类型 (deterministic / model_based / human)
        score: 分数 (0.0-1.0 或原始分)
        passed: 是否通过
        details: 人类可读说明
        weight: 该评分器的权重
        raw_output: 原始输出 (调试用)
        duration_ms: 评分耗时
    """

    name: str
    grader_type: str
    score: float = 0.0
    passed: bool = False
    details: str = ""
    weight: float = 1.0
    raw_output: dict[str, Any] | None = None
    duration_ms: float = 0.0

    @classmethod
    def from_result(
        cls,
        grader_name: str,
        grader_type: str,
        score: float,
        passed: bool,
        details: str,
        weight: float,
        start_time: float,
    ) -> "GraderScore":
        """Factory to reduce boilerplate in grader.grade() methods."""
        return cls(
            name=grader_name,
            grader_type=grader_type,
            score=score,
            passed=passed,
            details=details,
            weight=weight,
            duration_ms=(time.monotonic() - start_time) * 1000,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "grader_type": self.grader_type,
            "score": self.score,
            "passed": self.passed,
            "weight": self.weight,
        }
        if self.details:
            result["details"] = self.details
        if self.duration_ms > 0:
            result["duration_ms"] = self.duration_ms
        return result


# ---------------------------------------------------------------------------
# Trial Result
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    """单次 trial 的完整结果。

    Attributes:
        task_id: 任务 ID
        trial_index: 试验序号 (0-based)
        transcript: 完整 span 序列
        outcome: 最终环境状态
        grader_scores: 各评分器分数 {grader_name: GraderScore}
        final_score: 加权合成分数
        passed: 是否通过 (所有 required grader 通过 + 加权分数达标)
        duration_ms: 总耗时
        metadata: 元数据 (tokens, cost, model, strategy 等)
        error: 错误信息 (如果 trial 异常)
    """

    task_id: str
    trial_index: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    grader_scores: dict[str, GraderScore] = field(default_factory=dict)
    final_score: float = 0.0
    passed: bool = False
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trial_index": self.trial_index,
            "final_score": self.final_score,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "grader_scores": {k: v.to_dict() for k, v in self.grader_scores.items()},
            "metadata": self.metadata,
            "error": self.error,
            "transcript_length": len(self.transcript),
        }

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """从 transcript 提取工具调用列表。"""
        return [
            span for span in self.transcript
            if span.get("name") == "tool"
        ]

    @property
    def llm_calls(self) -> list[dict[str, Any]]:
        """从 transcript 提取 LLM 调用列表。"""
        return [
            span for span in self.transcript
            if span.get("name") == "llm"
        ]

    @property
    def final_answer(self) -> str | None:
        """从 transcript 提取最终答案。"""
        for span in reversed(self.transcript):
            if span.get("name") == "final_answer":
                return span.get("output", {}).get("answer")
        return None


# ---------------------------------------------------------------------------
# Task Report (单个任务的多次 trial 聚合)
# ---------------------------------------------------------------------------

@dataclass
class TaskReport:
    """单个任务的多次 trial 聚合报告。

    Attributes:
        task_id: 任务 ID
        task_description: 任务描述
        trials: 所有 trial 结果
        pass_at_k: pass@k 指标 {k: value}
        pass_hat_k: pass^k 指标 {k: value}
        avg_score: 平均分数
        score_std: 分数标准差 (一致性)
        passed: 整体是否通过
    """

    task_id: str
    task_description: str = ""
    trials: list[TrialResult] = field(default_factory=list)
    pass_at_k: dict[int, float] = field(default_factory=dict)
    pass_hat_k: dict[int, float] = field(default_factory=dict)
    avg_score: float = 0.0
    score_std: float = 0.0
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_description": self.task_description,
            "n_trials": len(self.trials),
            "pass_at_k": {str(k): v for k, v in self.pass_at_k.items()},
            "pass_hat_k": {str(k): v for k, v in self.pass_hat_k.items()},
            "avg_score": self.avg_score,
            "score_std": self.score_std,
            "passed": self.passed,
            "trials": [t.to_dict() for t in self.trials],
        }

    @property
    def n_success(self) -> int:
        """成功 trial 数。"""
        return sum(1 for t in self.trials if t.passed)

    @property
    def n_total(self) -> int:
        """总 trial 数。"""
        return len(self.trials)

    @property
    def success_rate(self) -> float:
        """单次成功率 (等价于 pass@1)。"""
        if not self.trials:
            return 0.0
        return self.n_success / self.n_total

    @property
    def best_trial(self) -> TrialResult | None:
        """最佳 trial。"""
        if not self.trials:
            return None
        return max(self.trials, key=lambda t: t.final_score)

    @property
    def worst_trial(self) -> TrialResult | None:
        """最差 trial。"""
        if not self.trials:
            return None
        return min(self.trials, key=lambda t: t.final_score)


# ---------------------------------------------------------------------------
# Aggregate Metrics (套件级别)
# ---------------------------------------------------------------------------

@dataclass
class AggregateMetrics:
    """套件级别的聚合指标。

    Attributes:
        total_tasks: 总任务数
        passed_tasks: 通过任务数
        pass_rate: 总体通过率
        avg_score: 平均分数
        score_distribution: 分数分布 {range: count}
        by_category: 按类别统计 {category: {pass_rate, avg_score, count}}
        by_difficulty: 按难度统计
        total_trials: 总 trial 数
        total_duration_ms: 总耗时
        total_tokens: 总 token 数
        total_cost: 总成本
    """

    total_tasks: int = 0
    passed_tasks: int = 0
    pass_rate: float = 0.0
    avg_score: float = 0.0
    score_distribution: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_difficulty: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_trials: int = 0
    total_duration_ms: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "passed_tasks": self.passed_tasks,
            "pass_rate": self.pass_rate,
            "avg_score": self.avg_score,
            "score_distribution": self.score_distribution,
            "by_category": self.by_category,
            "by_difficulty": self.by_difficulty,
            "total_trials": self.total_trials,
            "total_duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
        }
