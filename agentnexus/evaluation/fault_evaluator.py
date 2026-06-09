"""故障归因评估器 — 从 trace 中提取工具调用故障归因统计"""

from __future__ import annotations

import logging

from agentnexus.evaluation.utils import load_all_traces
from agentnexus.observability.fault_attribution import (
    FaultAttributionReport,
    FaultRecord,
    FaultSeverity,
    FaultType,
    classify_tool_fault,
)

logger = logging.getLogger(__name__)


class FaultEvaluator:
    """从 JSONL trace 文件中提取工具调用故障归因。"""

    def evaluate_all(self, traces_dir: str) -> FaultAttributionReport:
        """评估所有 trace 中的工具调用故障。"""
        traces = load_all_traces(traces_dir)
        all_faults: list[FaultRecord] = []
        total_tool_calls = 0

        for trace_id, spans in traces.items():
            report = self._evaluate_trace(trace_id, spans)
            all_faults.extend(report.faults)
            total_tool_calls += report.total_tool_calls

        return FaultAttributionReport(
            trace_id="aggregate",
            faults=all_faults,
            total_tool_calls=total_tool_calls,
            fault_count=len(all_faults),
        )

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> FaultAttributionReport | None:
        """评估单个 trace 的工具调用故障。"""
        traces = load_all_traces(traces_dir)
        spans = traces.get(trace_id)
        if spans is None:
            return None
        return self._evaluate_trace(trace_id, spans)

    def _evaluate_trace(self, trace_id: str, spans: list[dict]) -> FaultAttributionReport:
        """分析单个 trace 中的工具调用故障。"""
        faults: list[FaultRecord] = []
        tool_calls = 0

        # 提取 LLM span 中的 tool_calls 列表（用于判断工具选择）
        available_tools: list[str] = []
        for span in spans:
            if span.get("name") == "llm":
                meta = span.get("metadata", {})
                tc = meta.get("tool_calls", [])
                if tc:
                    available_tools = list(set(available_tools + tc))

        # 提取原始任务描述
        task_desc = ""
        for span in spans:
            if span.get("name") == "task":
                task_desc = span.get("input", {}).get("task", "")
                break

        # 分析每个 tool span
        for span in spans:
            if not span.get("name", "").startswith("tool"):
                continue

            tool_calls += 1
            meta = span.get("metadata", {})
            status = meta.get("status", "ok")

            if status == "error":
                error_msg = meta.get("error", "")
                tool_name = span.get("input", {}).get("tool_name", span.get("name", ""))
                params = span.get("input", {}).get("params", {})

                fault = classify_tool_fault(
                    tool_name=tool_name,
                    error_message=error_msg,
                    params=params,
                    available_tools=available_tools,
                    caller_intent=task_desc[:200] if task_desc else None,
                )
                if fault:
                    fault.trace_id = trace_id
                    faults.append(fault)

            # 检查结果理解错误（工具成功但后续可能误解）
            if status == "ok":
                result_summary = span.get("output", {}).get("result_summary", "")
                # 检查是否有异常关键词在结果中但未被后续引用
                if result_summary and any(kw in result_summary.lower()
                                          for kw in ("异常", "错误", "失败", "error", "failure")):
                    # 标记为潜在的结果理解风险（低严重度）
                    tool_name = span.get("input", {}).get("tool_name", "")
                    faults.append(FaultRecord(
                        fault_type=FaultType.RESULT_UNDERSTANDING,
                        tool_name=tool_name,
                        detail=f"工具 '{tool_name}' 返回包含异常信号，需确认模型是否正确理解",
                        severity=FaultSeverity.LOW,
                        evidence={"result_preview": result_summary[:200]},
                        trace_id=trace_id,
                    ))

        return FaultAttributionReport(
            trace_id=trace_id,
            faults=faults,
            total_tool_calls=tool_calls,
            fault_count=len(faults),
        )
