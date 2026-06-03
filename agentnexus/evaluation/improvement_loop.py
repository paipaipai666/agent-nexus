"""改进闭环管道 — 失败样本 → 归因 → 规则/评测集/监控

基于文章《Harness的可观测性》第八章：线上失败样本怎么沉淀成 Harness 的长期改进。
核心精神：不要只修这一次错误，要把错误修进环境里。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentnexus.evaluation.failure_attribution import FailureAttribution, FailureAttributionEvaluator
from agentnexus.evaluation.replay import ReplayEngine

logger = logging.getLogger(__name__)


@dataclass
class EvalSample:
    """从失败任务中提取的评测样本"""
    trace_id: str
    task: str
    expected_behavior: str
    failure_type: str
    created_at: float = field(default_factory=time.time)


@dataclass
class ImprovementAction:
    """一个改进建议"""
    action_type: str  # "rule" | "eval_sample" | "monitoring" | "config"
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    applied: bool = False


@dataclass
class ImprovementReport:
    """一次改进管道的执行报告"""
    trace_id: str
    attribution: FailureAttribution | None = None
    eval_sample: EvalSample | None = None
    actions: list[ImprovementAction] = field(default_factory=list)
    replay_result: Any = None

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def applied_count(self) -> int:
        return sum(1 for a in self.actions if a.applied)


class ImprovementPipeline:
    """改进闭环管道。

    处理流程：
    1. 失败归因 — 定位根因
    2. 生成评测样本 — 固定测试用例
    3. 提取改进规则 — 可编码的 Harness 规则
    4. 更新监控指标 — 早期信号变告警
    """

    def __init__(self, traces_dir: str):
        self.traces_dir = traces_dir
        self._attribution_eval = FailureAttributionEvaluator()
        self._replay_engine = ReplayEngine()
        self._eval_samples_dir = Path(traces_dir) / "eval_samples"
        self._improvements_dir = Path(traces_dir) / "improvements"

    def process_failure(self, trace_id: str) -> ImprovementReport:
        """处理一个失败任务，生成改进建议。"""
        report = ImprovementReport(trace_id=trace_id)

        # 1. 失败归因
        attribution = self._attribution_eval.attribute(trace_id, self.traces_dir)
        report.attribution = attribution

        if not attribution or not attribution.is_attributed:
            report.actions.append(ImprovementAction(
                action_type="manual",
                description="自动归因失败，需要人工分析",
            ))
            return report

        # 2. 生成评测样本
        eval_sample = self._create_eval_sample(trace_id, attribution)
        report.eval_sample = eval_sample
        if eval_sample:
            report.actions.append(ImprovementAction(
                action_type="eval_sample",
                description=f"生成评测样本: {eval_sample.task[:50]}",
                details={"sample_id": trace_id},
            ))

        # 3. 提取改进规则
        rules = self._extract_rules(attribution)
        report.actions.extend(rules)

        # 4. 更新监控建议
        monitoring_actions = self._suggest_monitoring(attribution)
        report.actions.extend(monitoring_actions)

        return report

    def _create_eval_sample(self, trace_id: str,
                            attribution: FailureAttribution) -> EvalSample | None:
        """从失败任务中提取评测样本。"""
        try:
            replay_data = self._replay_engine.extract_replay_data(
                trace_id, self.traces_dir
            )
            if not replay_data:
                return None

            sample = EvalSample(
                trace_id=trace_id,
                task=replay_data.get("task", "")[:500],
                expected_behavior=f"应该成功完成，但因 {attribution.root_cause} 失败",
                failure_type=attribution.root_cause,
            )

            # 持久化评测样本
            self._save_eval_sample(sample)
            return sample
        except Exception as e:
            logger.debug("Failed to create eval sample: %s", e)
            return None

    def _save_eval_sample(self, sample: EvalSample) -> None:
        """保存评测样本到磁盘。"""
        try:
            self._eval_samples_dir.mkdir(parents=True, exist_ok=True)
            path = self._eval_samples_dir / f"{sample.trace_id}.json"
            data = {
                "trace_id": sample.trace_id,
                "task": sample.task,
                "expected_behavior": sample.expected_behavior,
                "failure_type": sample.failure_type,
                "created_at": sample.created_at,
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.debug("Failed to save eval sample: %s", e)

    def _extract_rules(self, attribution: FailureAttribution) -> list[ImprovementAction]:
        """从归因中提取可编码的 Harness 规则。"""
        actions: list[ImprovementAction] = []
        root = attribution.root_cause

        if root == "permission":
            actions.append(ImprovementAction(
                action_type="rule",
                description="添加权限边界检查 hook",
                details={
                    "hook_type": "BEFORE_TOOL_CALL",
                    "rule": "高风险工具需要 HITL 确认",
                },
            ))

        elif root == "tool_param":
            actions.append(ImprovementAction(
                action_type="rule",
                description="加强参数校验",
                details={
                    "rule": "工具调用前进行 schema 校验",
                    "layer": "ToolRegistry.invoke",
                },
            ))

        elif root == "tool_selection":
            actions.append(ImprovementAction(
                action_type="config",
                description="优化工具描述",
                details={
                    "rule": "改进工具 description 以提高选择准确率",
                },
            ))

        elif root == "cost":
            actions.append(ImprovementAction(
                action_type="config",
                description="启用预算限制",
                details={
                    "config_key": "budget_exceed_strategy",
                    "suggested_value": "compress",
                },
            ))

        elif root == "goal_understanding":
            actions.append(ImprovementAction(
                action_type="rule",
                description="添加目标校验检查点",
                details={
                    "rule": "每 N 步检查当前目标与原始目标的一致性",
                },
            ))

        return actions

    def _suggest_monitoring(self, attribution: FailureAttribution) -> list[ImprovementAction]:
        """根据归因建议监控指标。"""
        actions: list[ImprovementAction] = []
        root = attribution.root_cause

        if root in ("tool_selection", "tool_param", "permission"):
            actions.append(ImprovementAction(
                action_type="monitoring",
                description=f"监控 {root} 类型故障的频率",
                details={
                    "metric": f"tool_fault_{root}_rate",
                    "alert_threshold": "连续 3 次同类故障",
                },
            ))

        if root == "cost":
            actions.append(ImprovementAction(
                action_type="monitoring",
                description="监控 Token 消耗趋势",
                details={
                    "metric": "total_cost_cny",
                    "alert_threshold": "超过日均 2 倍",
                },
            ))

        return actions
