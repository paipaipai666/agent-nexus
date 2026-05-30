"""Tool Selection Accuracy Evaluator.

Measures whether the agent picks the right tool for user intent.
Reads JSONL traces, extracts (query, selected_tool) pairs from tool spans,
compares against a labeled eval set of (query, expected_tool) pairs.

Article threshold: >0.92 for <5 tools, >0.85 for 5+ tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentnexus.evaluation.utils import iter_spans


@dataclass
class ToolSelectionReport:
    total_queries: int = 0
    correct: int = 0
    accuracy: float = 0.0
    by_tool: dict[str, dict] = field(default_factory=dict)  # tool -> {total, correct}
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


# Minimal labeled eval set for AgentNexus's built-in tools.
# Format: {query_fragment: expected_tool_name}
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

        # Collect (task_text, first_tool_name) pairs per trace
        trace_tasks: dict[str, str] = {}
        trace_tools: dict[str, str] = {}

        for span in iter_spans(traces_dir):
            tid = span.get("trace_id", "")
            name = span.get("name", "")
            if not tid:
                continue

            if name == "task":
                inp = span.get("input", {})
                task = inp.get("task", "")
                if task:
                    trace_tasks[tid] = task

            elif name == "tool":
                inp = span.get("input", {}) or {}
                tool_name = inp.get("tool_name", "")
                if tool_name and tid not in trace_tools:
                    trace_tools[tid] = tool_name

        # Evaluate each trace that has both a task and a tool call
        for tid, task in trace_tasks.items():
            actual_tool = trace_tools.get(tid)
            if not actual_tool:
                continue

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
