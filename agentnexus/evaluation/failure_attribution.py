"""失败归因评估器 — 从 trace 中自动归因失败任务

基于文章《Harness的可观测性》第八章提出的七层归因：
目标理解、上下文、工具选择、工具参数、权限、状态、成本、评估
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from agentnexus.evaluation.utils import load_all_traces

logger = logging.getLogger(__name__)


class AttributionLayer(str, Enum):
    GOAL_UNDERSTANDING = "goal_understanding"
    CONTEXT = "context"
    TOOL_SELECTION = "tool_selection"
    TOOL_PARAM = "tool_param"
    PERMISSION = "permission"
    STATE = "state"
    COST = "cost"
    EVAL = "eval"


@dataclass
class FailureAttribution:
    """一次失败任务的归因结果"""
    trace_id: str
    layers: dict[str, str] = field(default_factory=dict)  # layer -> description
    root_cause: str = ""
    detail: str = ""
    suggested_fix: str = ""
    confidence: float = 0.0

    @property
    def is_attributed(self) -> bool:
        return bool(self.root_cause)


class FailureAttributionEvaluator:
    """从 trace 中自动归因失败任务。"""

    def attribute(self, trace_id: str, traces_dir: str) -> FailureAttribution | None:
        """归因单个失败任务。"""
        traces = load_all_traces(traces_dir)
        spans = traces.get(trace_id)
        if spans is None:
            return None
        return self._attribute_trace(trace_id, spans)

    def attribute_all(self, traces_dir: str) -> list[FailureAttribution]:
        """归因所有失败任务。"""
        traces = load_all_traces(traces_dir)
        results = []
        for trace_id, spans in traces.items():
            # 只归因失败任务（没有 final_answer 或有 error）
            has_answer = any(s.get("name") == "final_answer" for s in spans)
            has_error = any(s.get("metadata", {}).get("status") == "error" for s in spans)
            if not has_answer or has_error:
                attr = self._attribute_trace(trace_id, spans)
                if attr.is_attributed:
                    results.append(attr)
        return results

    def _attribute_trace(self, trace_id: str, spans: list[dict]) -> FailureAttribution:
        """分析单个 trace 的失败归因。"""
        layers: dict[str, str] = {}

        # 1. 目标理解层 — 检查 task span 是否存在
        task_spans = [s for s in spans if s.get("name") == "task"]
        if not task_spans:
            layers[AttributionLayer.GOAL_UNDERSTANDING.value] = "缺少 task span，无法确定原始目标"

        # 2. 上下文层 — 检查 LLM span 的 context_refs
        llm_spans = [s for s in spans if s.get("name") == "llm"]
        for ls in llm_spans:
            ctx_refs = ls.get("input", {}).get("context_refs", [])
            if not ctx_refs:
                layers[AttributionLayer.CONTEXT.value] = "LLM 调用缺少上下文来源引用"

        # 3. 工具选择层 — 检查工具调用是否合理
        tool_spans = [s for s in spans if s.get("name", "").startswith("tool")]
        error_tools = [s for s in tool_spans if s.get("metadata", {}).get("status") == "error"]
        if error_tools:
            error_names = [s.get("input", {}).get("tool_name", "") for s in error_tools]
            layers[AttributionLayer.TOOL_SELECTION.value] = f"工具调用失败: {', '.join(set(error_names))}"

        # 4. 工具参数层 — 检查 schema_validation
        for ts in error_tools:
            validation = ts.get("metadata", {}).get("schema_validation", "")
            if validation == "failed":
                layers[AttributionLayer.TOOL_PARAM.value] = "参数 schema 校验失败"

        # 5. 权限层 — 检查权限错误
        for ts in error_tools:
            error_msg = ts.get("metadata", {}).get("error", "")
            if any(kw in error_msg.lower() for kw in ("permission", "blocked", "not allowed")):
                layers[AttributionLayer.PERMISSION.value] = f"权限错误: {error_msg[:100]}"

        # 6. 成本层 — 检查是否超预算
        total_tokens = sum(
            s.get("metadata", {}).get("input_tokens", 0) +
            s.get("metadata", {}).get("output_tokens", 0)
            for s in llm_spans
        )
        if total_tokens > 100000:
            layers[AttributionLayer.COST.value] = f"Token 消耗过高: {total_tokens:,}"

        # 7. 评估层 — 检查是否有 final_answer
        has_answer = any(s.get("name") == "final_answer" for s in spans)
        if not has_answer:
            layers[AttributionLayer.EVAL.value] = "任务未产生最终答案"

        # 确定根因
        root_cause = self._determine_root_cause(layers, spans)

        return FailureAttribution(
            trace_id=trace_id,
            layers=layers,
            root_cause=root_cause,
            detail=layers.get(root_cause, ""),
            suggested_fix=self._suggest_fix(root_cause, layers),
            confidence=0.7 if layers else 0.0,
        )

    def _determine_root_cause(self, layers: dict[str, str],
                              spans: list[dict]) -> str:
        """从多层归因中确定根因。"""
        # 优先级：权限 > 参数 > 工具选择 > 目标理解 > 上下文 > 成本 > 评估
        priority = [
            AttributionLayer.PERMISSION.value,
            AttributionLayer.TOOL_PARAM.value,
            AttributionLayer.TOOL_SELECTION.value,
            AttributionLayer.GOAL_UNDERSTANDING.value,
            AttributionLayer.CONTEXT.value,
            AttributionLayer.COST.value,
            AttributionLayer.EVAL.value,
        ]
        for layer in priority:
            if layer in layers:
                return layer
        return ""

    def _suggest_fix(self, root_cause: str, layers: dict[str, str]) -> str:
        """根据根因建议修复方向。"""
        fixes = {
            AttributionLayer.PERMISSION.value: "检查工具权限配置，确认 agent 有权调用该工具",
            AttributionLayer.TOOL_PARAM.value: "检查参数 schema 定义，确保 LLM 生成的参数格式正确",
            AttributionLayer.TOOL_SELECTION.value: "优化工具描述，改进路由策略",
            AttributionLayer.GOAL_UNDERSTANDING.value: "检查目标解析逻辑，确保正确理解用户意图",
            AttributionLayer.CONTEXT.value: "检查上下文组装逻辑，确保关键信息被传入",
            AttributionLayer.COST.value: "启用预算分层，限制 token 消耗",
            AttributionLayer.EVAL.value: "检查 FSM 流程，确保任务能正常完成",
        }
        return fixes.get(root_cause, "需要人工分析")
