"""Tool Selection Accuracy Evaluator.

Measures whether the agent picks the right tool for user intent.
Reads JSONL traces, extracts (query, selected_tool) pairs, compares against
a small labeled eval set of (query, expected_tool) pairs.

Article threshold: >0.92 for <5 tools, >0.85 for 5+ tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolSelectionReport:
    total_queries: int = 0
    correct: int = 0
    accuracy: float = 0.0
    by_tool: dict[str, dict] = field(default_factory=dict)  # tool → {total, correct}
    mismatches: list[dict] = field(default_factory=list)     # {query, expected, actual}

    @property
    def passed(self) -> bool:
        return self.accuracy >= 0.92

    def summary(self) -> str:
        lines = [
            f"Accuracy: {self.accuracy:.1%} ({self.correct}/{self.total_queries})",
        ]
        for tool, stats in sorted(self.by_tool.items()):
            acc = stats["correct"] / stats["total"] if stats["total"] else 0
            lines.append(f"  {tool}: {acc:.1%} ({stats['correct']}/{stats['total']})")
        return "\n".join(lines)


# Minimal labeled eval set for AgentNexus's 3 tools.
# Format: {query_fragment: expected_tool_name}
# Expand this as tool count grows.
LABELED_EVAL_SET: dict[str, str] = {
    "搜索": "web_search",
    "search": "web_search",
    "查询": "web_search",
    "最新": "web_search",
    "代码": "python_execute",
    "code": "python_execute",
    "计算": "python_execute",
    "运行": "python_execute",
    "生成图表": "python_execute",
    "记忆": "memory_search",
    "偏好": "memory_search",
    "记住": "memory_search",
    "之前": "memory_search",
}


class ToolSelectionEvaluator:
    """Evaluate tool selection accuracy from trace data."""

    def __init__(self, eval_set: dict[str, str] | None = None):
        self.eval_set = eval_set or LABELED_EVAL_SET

    def evaluate_from_traces(self, traces_dir: str) -> ToolSelectionReport:
        """Extract tool selection pairs from traces and evaluate."""
        report = ToolSelectionReport()
        queries: dict[str, list[str]] = {}  # trace_id → [tool_names in order]

        for f in sorted(Path(traces_dir).glob("*.jsonl")):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = span.get("trace_id", "")
                    name = span.get("name", "")
                    if tid and name:
                        queries.setdefault(tid, []).append(name)

        # For each trace, map first tool-like span to a query type
        for tid, node_names in queries.items():
            task = self._get_task_from_trace(traces_dir, tid)
            if not task:
                continue

            # Find the first tool execution or research node
            first_action = next((n for n in node_names
                                 if n in ("research_node", "execute_node")), None)
            if first_action == "research_node":
                actual_tool = "web_search"
            elif first_action == "execute_node":
                actual_tool = "python_execute"
            else:
                continue

            # Map query to expected tool
            expected = self._classify_query(task)
            report.total_queries += 1

            tool_stats = report.by_tool.setdefault(expected, {"total": 0, "correct": 0})
            tool_stats["total"] += 1

            if actual_tool == expected:
                report.correct += 1
                tool_stats["correct"] += 1
            else:
                report.mismatches.append({
                    "trace_id": tid, "query": task[:80],
                    "expected": expected, "actual": actual_tool,
                })

        report.accuracy = report.correct / report.total_queries if report.total_queries else 0.0
        return report

    def _classify_query(self, task: str) -> str:
        """Classify a task into expected tool based on keyword matching."""
        task_lower = task.lower()
        for keyword, tool in self.eval_set.items():
            if keyword.lower() in task_lower:
                return tool
        return "web_search"  # default

    @staticmethod
    def _get_task_from_trace(traces_dir: str, trace_id: str) -> str | None:
        """Extract the original task text from the trace's root span."""
        for f in sorted(Path(traces_dir).glob("*.jsonl")):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (span.get("trace_id") == trace_id
                            and span.get("name") == "task"):
                        inp = span.get("input", {})
                        return inp.get("task", "")
        return None
