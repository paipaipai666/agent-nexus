"""确定性回放引擎 — 固定输入和工具返回，回放失败任务

基于文章《Harness的可观测性》第九章第五层：回放与评测层。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agentnexus.evaluation.utils import load_all_traces

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    """回放结果"""
    original_trace_id: str
    new_trace_id: str = ""
    original_status: str = ""
    new_status: str = ""
    improved: bool = False
    original_steps: int = 0
    new_steps: int = 0
    original_tool_errors: int = 0
    new_tool_errors: int = 0
    steps_diff: list[dict[str, Any]] = field(default_factory=list)
    replay_duration_ms: float = 0.0

    @property
    def summary(self) -> str:
        status = "改善" if self.improved else "未改善"
        return (
            f"回放{status}: {self.original_steps}→{self.new_steps} 步, "
            f"工具错误 {self.original_tool_errors}→{self.new_tool_errors}"
        )


class ReplayEngine:
    """确定性回放引擎。

    从 trace 中提取原始输入和工具返回结果，
    用当前版本的 agent 重新执行，对比新旧轨迹。
    """

    def extract_replay_data(self, trace_id: str, traces_dir: str) -> dict[str, Any] | None:
        """从 trace 中提取回放所需数据。

        Returns:
            包含 task、tool_calls、tool_results 的字典，或 None
        """
        traces = load_all_traces(traces_dir)
        spans = traces.get(trace_id)
        if spans is None:
            return None

        task = ""
        tool_calls: list[dict] = []
        tool_results: dict[str, str] = {}

        for span in spans:
            name = span.get("name", "")

            if name == "task":
                task = span.get("input", {}).get("task", "")

            elif name.startswith("tool"):
                tool_name = span.get("input", {}).get("tool_name", "")
                params = span.get("input", {}).get("params", {})
                result = span.get("output", {}).get("result_summary", "")
                status = span.get("metadata", {}).get("status", "ok")

                if tool_name:
                    call_key = f"{tool_name}:{hash(str(params))}"
                    tool_calls.append({
                        "tool_name": tool_name,
                        "params": params,
                        "call_key": call_key,
                    })
                    tool_results[call_key] = result

        return {
            "task": task,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "trace_id": trace_id,
        }

    def compare_traces(self, original_trace_id: str, new_trace_id: str,
                       traces_dir: str) -> ReplayResult:
        """对比原始 trace 和新 trace。"""
        traces = load_all_traces(traces_dir)
        original_spans = traces.get(original_trace_id, [])
        new_spans = traces.get(new_trace_id, [])

        original_stats = self._compute_trace_stats(original_spans)
        new_stats = self._compute_trace_stats(new_spans)

        improved = (
            new_stats["tool_errors"] < original_stats["tool_errors"]
            or (new_stats["has_answer"] and not original_stats["has_answer"])
        )

        return ReplayResult(
            original_trace_id=original_trace_id,
            new_trace_id=new_trace_id,
            original_status="success" if original_stats["has_answer"] else "failed",
            new_status="success" if new_stats["has_answer"] else "failed",
            improved=improved,
            original_steps=original_stats["steps"],
            new_steps=new_stats["steps"],
            original_tool_errors=original_stats["tool_errors"],
            new_tool_errors=new_stats["tool_errors"],
        )

    def _compute_trace_stats(self, spans: list[dict]) -> dict[str, Any]:
        """计算 trace 的统计信息。"""
        steps = len([s for s in spans if s.get("name") == "llm"])
        tool_errors = len([
            s for s in spans
            if s.get("name", "").startswith("tool")
            and s.get("metadata", {}).get("status") == "error"
        ])
        has_answer = any(s.get("name") == "final_answer" for s in spans)
        return {"steps": steps, "tool_errors": tool_errors, "has_answer": has_answer}
