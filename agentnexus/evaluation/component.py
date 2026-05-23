"""Component Evaluator — per-agent deterministic quality checks.

Checks each agent type against its contractual obligations without LLM-as-Judge.
Reads JSONL trace spans and validates:
  - Coder: schema compliance, __main__ presence, truncation detection
  - Researcher: source citation presence, claim structure
  - Executor: success rate, exception handling
  - Critic/Analyst: score validity, fallback activation
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ComponentIssue:
    agent: str
    severity: str  # "error" | "warning"
    detail: str
    trace_id: str = ""


@dataclass
class ComponentReport:
    total_traces: int = 0
    issues: list[ComponentIssue] = field(default_factory=list)
    by_agent: dict[str, dict] = field(default_factory=dict)
    by_tool: dict[str, dict] = field(default_factory=dict)  # tool_name → {total, success}

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def passed(self) -> bool:
        return all(a.get("score", 10) >= 6.0 for a in self.by_agent.values())


class ComponentEvaluator:
    """Per-agent component evaluation from trace data."""

    def evaluate_all(self, traces_dir: str) -> ComponentReport:
        report = ComponentReport()
        agent_stats: dict[str, list[float]] = {"coder": [], "researcher": [], "executor": [], "analyst": []}

        for f in sorted(Path(traces_dir).glob("*.jsonl")):
            spans: list[dict] = []
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            spans.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            traces: dict[str, list[dict]] = {}
            for s in spans:
                traces.setdefault(s.get("trace_id", "unknown"), []).append(s)

            report.total_traces += len(traces)
            for tid, trace_spans in traces.items():
                self._check_coder(trace_spans, tid, report, agent_stats)
                self._check_researcher(trace_spans, tid, report, agent_stats)
                self._check_executor(trace_spans, tid, report, agent_stats)
                self._check_analyst(trace_spans, tid, report, agent_stats)

        for agent, scores in agent_stats.items():
            if scores:
                avg = sum(scores) / len(scores)
                report.by_agent[agent] = {"score": round(avg, 1), "count": len(scores)}

        return report

    def _check_coder(self, spans: list[dict], tid: str, report: ComponentReport,
                     stats: dict[str, list[float]]):
        code_spans = [s for s in spans if s.get("name") == "code_node"]
        if not code_spans:
            return

        score = 10.0
        for s in code_spans:
            output = str(s.get("output", ""))
            # Check for schema validation failure
            if "SCHEMA_VIOLATION" in output or "validation" in output.lower():
                score -= 2
                report.issues.append(ComponentIssue("coder", "error", "Schema validation failed", tid))
            # Check for truncation
            meta = s.get("metadata", {})
            if meta.get("status") == "truncated":
                score -= 1
                report.issues.append(ComponentIssue("coder", "warning", "Code generation truncated", tid))
            # Check __main__ presence in output
            if output and 'if __name__' not in output and 'code_result' in output.lower():
                score -= 0.5

        stats["coder"].append(max(0, score))

    def _check_researcher(self, spans: list[dict], tid: str, report: ComponentReport,
                          stats: dict[str, list[float]]):
        research_spans = [s for s in spans if s.get("name") == "research_node"]
        if not research_spans:
            return

        score = 10.0
        for s in research_spans:
            output = str(s.get("output", ""))
            # Check for source claims
            if "SourceClaim" not in output and "source" not in output.lower() and "来源" not in output:
                score -= 2
                report.issues.append(
                    ComponentIssue("researcher", "error", "No source citations in research output", tid)
                )
            # Check for empty result
            if not output or output.strip() == "":
                score -= 3
                report.issues.append(ComponentIssue("researcher", "error", "Empty research result", tid))

        stats["researcher"].append(max(0, score))

    def _check_executor(self, spans: list[dict], tid: str, report: ComponentReport,
                        stats: dict[str, list[float]]):
        exec_spans = [s for s in spans if s.get("name") == "execute_node"]
        if not exec_spans:
            return

        score = 10.0
        failed = 0
        total = len(exec_spans)
        for s in exec_spans:
            is_error = s.get("metadata", {}).get("status") == "error"
            if is_error:
                failed += 1
            output = str(s.get("output", ""))
            if "exception" in output.lower() or "traceback" in output.lower():
                score -= 1

            # Categorize by tool: inspect span input for tool name
            inp = str(s.get("input", ""))
            tool = "unknown"
            for t in ("web_search", "python_execute", "memory_search", "code_executor"):
                if t in inp:
                    tool = t
                    break
            ts = report.by_tool.setdefault(tool, {"total": 0, "success": 0})
            ts["total"] += 1
            if not is_error:
                ts["success"] += 1

        if total > 0 and failed / total > 0.5:
            score -= 2
            report.issues.append(ComponentIssue("executor", "error", f"High failure rate: {failed}/{total}", tid))

        stats["executor"].append(max(0, score))

    def _check_analyst(self, spans: list[dict], tid: str, report: ComponentReport,
                       stats: dict[str, list[float]]):
        analyst_spans = [s for s in spans if s.get("name") == "analyst_node"]
        if not analyst_spans:
            return

        score = 10.0
        for s in analyst_spans:
            output = str(s.get("output", ""))
            # Check for degraded answer fallback
            if "降级" in output or "fallback" in output.lower():
                score -= 1
                report.issues.append(ComponentIssue("analyst", "warning", "LLM fallback activated", tid))
            # Check critic score validity
            if "critique_score" in output:
                try:
                    import re
                    m = re.search(r"'critique_score':\s*([\d.]+)", output)
                    if m:
                        cs = float(m.group(1))
                        if cs < 0 or cs > 10:
                            score -= 1
                            report.issues.append(ComponentIssue("analyst", "error", f"Invalid critic score: {cs}", tid))
                except ValueError:
                    pass

        stats["analyst"].append(max(0, score))
