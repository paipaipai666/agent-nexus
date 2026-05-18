"""Trajectory Evaluator — deterministic trace quality assessment.

Reads JSONL trace files and applies 5 rule-based checks (no LLM-as-Judge):
  1. duplicate_calls   — same tool + same params called ≥3 times in a row
  2. tool_appropriateness — code_error without switching to research
  3. loop_detection     — plan_node appears ≥4 times
  4. retry_efficiency   — critic score not improving across retries
  5. plan_adherence     — actual plan_node count vs original plan steps
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TrajectoryIssue:
    check: str
    severity: str  # "error" | "warning"
    detail: str
    evidence: str = ""


@dataclass
class TrajectoryReport:
    trace_id: str
    total_spans: int
    issues: list[TrajectoryIssue] = field(default_factory=list)
    score: float = 10.0  # starts at 10, deductions for each issue

    @property
    def passed(self) -> bool:
        return self.score >= 6.0

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def summary(self) -> str:
        lines = [
            f"Trace: {self.trace_id}",
            f"Spans: {self.total_spans}",
            f"Score: {self.score:.0f}/10",
            f"Issues: {self.issue_count}",
        ]
        if self.issues:
            lines.append("")
            for i in self.issues[:5]:
                lines.append(f"  [{i.severity.upper()}] {i.check}: {i.detail}")
        return "\n".join(lines)


class TrajectoryEvaluator:
    """Deterministic rule-based trajectory quality evaluator."""

    def evaluate_file(self, filepath: str) -> list[TrajectoryReport]:
        """Evaluate all traces in a JSONL file."""
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Group spans by trace_id
        traces: dict[str, list[dict]] = defaultdict(list)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue
            traces[span.get("trace_id", "unknown")].append(span)

        return [self._evaluate_one(tid, spans) for tid, spans in traces.items()]

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> TrajectoryReport | None:
        """Evaluate a single trace by ID from a traces directory."""
        for f in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
            spans = self._load_trace_from_file(str(f), trace_id)
            if spans:
                return self._evaluate_one(trace_id, spans)
        return None

    def evaluate_all(self, traces_dir: str) -> list[TrajectoryReport]:
        """Evaluate all traces in a traces directory."""
        all_spans: list[dict] = []
        for f in sorted(Path(traces_dir).glob("*.jsonl")):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            all_spans.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        traces: dict[str, list[dict]] = defaultdict(list)
        for span in all_spans:
            traces[span.get("trace_id", "unknown")].append(span)
        return [self._evaluate_one(tid, spans) for tid, spans in traces.items()]

    # ── per-trace evaluation ─────────────────────────────────────

    def _evaluate_one(self, trace_id: str, spans: list[dict]) -> TrajectoryReport:
        spans = sorted(spans, key=lambda s: s.get("start_time", 0))
        report = TrajectoryReport(trace_id=trace_id, total_spans=len(spans))

        # Extract named spans and tool calls
        named_spans = [s for s in spans if s.get("name") and s["name"] != "task"]
        tool_calls = [s for s in spans if "tool" in s.get("name", "").lower()
                      or "execute" in s.get("name", "").lower()]

        # 1. Duplicate call detection
        self._check_duplicate_calls(tool_calls, report)

        # 2. Tool appropriateness
        self._check_tool_appropriateness(named_spans, report)

        # 3. Loop detection
        self._check_loops(named_spans, report)

        # 4. Retry efficiency
        self._check_retry_efficiency(named_spans, report)

        # 5. Plan adherence
        self._check_plan_adherence(named_spans, report)

        report.score = max(0, report.score)
        return report

    # ── individual checks ─────────────────────────────────────────

    def _check_duplicate_calls(self, tool_spans: list[dict], report: TrajectoryReport):
        """Detect same tool + same input called ≥3 times consecutively."""
        for i in range(len(tool_spans) - 2):
            a, b, c = tool_spans[i], tool_spans[i + 1], tool_spans[i + 2]
            a_name = a.get("name", "")
            if a_name == b.get("name", "") == c.get("name", ""):
                a_in = str(a.get("input", ""))[:100]
                b_in = str(b.get("input", ""))[:100]
                c_in = str(c.get("input", ""))[:100]
                if a_in == b_in == c_in:
                    report.score -= 1.5
                    report.issues.append(TrajectoryIssue(
                        check="duplicate_calls",
                        severity="error",
                        detail=f"'{a_name}' called 3+ times with identical input: {a_in[:60]}",
                    ))
                    return  # report once per trace

    def _check_tool_appropriateness(self, spans: list[dict], report: TrajectoryReport):
        """If code_error occurs but agent never switches to research, flag it."""
        has_code_error = any(
            "error" in s.get("metadata", {}).get("status", "")
            and "code" in s.get("name", "").lower()
            for s in spans
        )
        has_research = any("research" in s.get("name", "").lower() for s in spans)
        if has_code_error and not has_research:
            report.score -= 1.0
            report.issues.append(TrajectoryIssue(
                check="tool_appropriateness",
                severity="warning",
                detail="Code error occurred but agent never switched to research for docs lookup",
            ))

    def _check_loops(self, spans: list[dict], report: TrajectoryReport):
        """Flag if plan_node appears ≥4 times (possible infinite retry loop)."""
        plan_count = sum(1 for s in spans if s.get("name") == "plan_node")
        if plan_count >= 4:
            report.score -= 2.0
            report.issues.append(TrajectoryIssue(
                check="loop_detection",
                severity="error",
                detail=f"plan_node executed {plan_count} times (≥4 indicates possible loop)",
                evidence=f"plan_node count={plan_count}",
            ))

    def _check_retry_efficiency(self, spans: list[dict], report: TrajectoryReport):
        """Check if critic scores are improving across analyst calls."""
        analyst_spans = [s for s in spans if "analyst" in s.get("name", "").lower()]
        scores: list[float] = []
        for s in analyst_spans:
            output = s.get("output", {})
            for v in output.values():
                v_str = str(v)
                if "critique_score" in v_str or "score" in v_str:
                    try:
                        import re
                        m = re.search(r"'critique_score':\s*([\d.]+)", v_str)
                        if m:
                            scores.append(float(m.group(1)))
                    except (ValueError, IndexError):
                        pass

        if len(scores) >= 2:
            for i in range(1, len(scores)):
                if scores[i] < scores[i - 1] - 1.0:  # dropped >1 point
                    report.score -= 0.5
                    report.issues.append(TrajectoryIssue(
                        check="retry_efficiency",
                        severity="warning",
                        detail=f"Critic score dropped from {scores[i-1]:.1f} to {scores[i]:.1f} on retry",
                    ))

    def _check_plan_adherence(self, spans: list[dict], report: TrajectoryReport):
        """Check if actual node execution count matches expected plan."""
        plan_spans = [s for s in spans if s.get("name") == "plan_node"]
        if plan_spans:
            first_plan = plan_spans[0]
            plan_input = str(first_plan.get("input", "") or "")
            # Count research: lines in plan input
            plan_steps = plan_input.count("research:") + plan_input.count("code:")
            if plan_steps > 0:
                actual_research = sum(1 for s in spans if s.get("name") == "research_node")
                actual_code = sum(1 for s in spans if s.get("name") == "code_node")
                actual = actual_research + actual_code
                if actual > plan_steps * 2:  # >200% of planned
                    report.score -= 1.0
                    report.issues.append(TrajectoryIssue(
                        check="plan_adherence",
                        severity="warning",
                        detail=f"Planned {plan_steps} steps but executed {actual} nodes",
                    ))

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _load_trace_from_file(filepath: str, trace_id: str) -> list[dict] | None:
        spans: list[dict] = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if span.get("trace_id") == trace_id:
                    spans.append(span)
        return spans if spans else None
