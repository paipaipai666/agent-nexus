"""Component Evaluator — deterministic quality checks for single ReAct agent.

Reads JSONL trace spans and validates:
  - LLM: truncation, error rate, empty responses
  - Tool execution: success rate, error patterns, timeout detection
  - Answer: final answer presence, degradation signals
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentnexus.evaluation.utils import load_all_traces


@dataclass
class ComponentIssue:
    component: str
    severity: str  # "error" | "warning"
    detail: str
    trace_id: str = ""


@dataclass
class ComponentReport:
    total_traces: int = 0
    issues: list[ComponentIssue] = field(default_factory=list)
    by_component: dict[str, dict] = field(default_factory=dict)
    by_tool: dict[str, dict] = field(default_factory=dict)  # tool_name -> {total, success}

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def passed(self) -> bool:
        return all(c.get("score", 10) >= 6.0 for c in self.by_component.values())


class ComponentEvaluator:
    """Per-component evaluation from single ReAct agent trace data."""

    def evaluate_all(self, traces_dir: str) -> ComponentReport:
        report = ComponentReport()
        component_stats: dict[str, list[float]] = {
            "llm": [], "tool_execution": [], "answer": [],
        }

        all_traces = load_all_traces(traces_dir)
        report.total_traces = len(all_traces)
        for tid, trace_spans in all_traces.items():
            self._check_llm(trace_spans, tid, report, component_stats)
            self._check_tool_execution(trace_spans, tid, report, component_stats)
            self._check_answer(trace_spans, tid, report, component_stats)

        for component, scores in component_stats.items():
            if scores:
                avg = sum(scores) / len(scores)
                report.by_component[component] = {"score": round(avg, 1), "count": len(scores)}

        return report

    def _check_llm(self, spans: list[dict], tid: str, report: ComponentReport,
                   stats: dict[str, list[float]]):
        llm_spans = [s for s in spans if s.get("name") == "llm"]
        if not llm_spans:
            return

        score = 10.0
        errors = 0
        truncations = 0

        for s in llm_spans:
            meta = s.get("metadata", {}) or {}
            status = meta.get("status", "ok")

            if status == "error":
                errors += 1
                score -= 2
                report.issues.append(ComponentIssue("llm", "error", "LLM call failed", tid))

            if meta.get("truncated", False):
                truncations += 1
                score -= 1
                report.issues.append(ComponentIssue("llm", "warning", "LLM output truncated", tid))

        total = len(llm_spans)
        if total > 0 and errors / total > 0.3:
            score -= 2
            report.issues.append(ComponentIssue(
                "llm", "error", f"High LLM error rate: {errors}/{total}", tid,
            ))

        stats["llm"].append(max(0, score))

    def _check_tool_execution(self, spans: list[dict], tid: str, report: ComponentReport,
                              stats: dict[str, list[float]]):
        tool_spans = [s for s in spans if s.get("name") == "tool"]
        if not tool_spans:
            return

        score = 10.0
        failed = 0
        total = len(tool_spans)

        for s in tool_spans:
            meta = s.get("metadata", {}) or {}
            inp = s.get("input", {}) or {}
            tool_name = inp.get("tool_name", "unknown")
            is_error = meta.get("status") == "error"

            ts = report.by_tool.setdefault(tool_name, {"total": 0, "success": 0})
            ts["total"] += 1
            if is_error:
                failed += 1
            else:
                ts["success"] += 1

            output = str(s.get("output", {}))
            if "timed out" in output.lower() or "timeout" in output.lower():
                score -= 1
                report.issues.append(ComponentIssue(
                    "tool_execution", "warning", f"Tool '{tool_name}' timed out", tid,
                ))

        if total > 0 and failed / total > 0.5:
            score -= 2
            report.issues.append(ComponentIssue(
                "tool_execution", "error", f"High tool failure rate: {failed}/{total}", tid,
            ))

        stats["tool_execution"].append(max(0, score))

    def _check_answer(self, spans: list[dict], tid: str, report: ComponentReport,
                      stats: dict[str, list[float]]):
        answer_spans = [s for s in spans if s.get("name") == "final_answer"]
        llm_spans = [s for s in spans if s.get("name") == "llm"]

        if not llm_spans and not answer_spans:
            return  # nothing to evaluate

        score = 10.0

        if llm_spans and not answer_spans:
            score -= 3
            report.issues.append(ComponentIssue(
                "answer", "error", "Agent ran LLM steps but produced no final answer", tid,
            ))

        for s in answer_spans:
            output = str(s.get("output", {}))
            meta = s.get("metadata", {}) or {}
            if "降级" in output or "fallback" in output.lower():
                score -= 1
                report.issues.append(ComponentIssue(
                    "answer", "warning", "Degraded answer (fallback activated)", tid,
                ))
            if meta.get("used_subagent"):
                sub_status = meta.get("subagent_status", "")
                if sub_status == "failed":
                    score -= 1
                    report.issues.append(ComponentIssue(
                        "answer", "warning", "Subagent failed during answer generation", tid,
                    ))

        stats["answer"].append(max(0, score))
