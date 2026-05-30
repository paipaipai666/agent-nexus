"""Single Agent Evaluator — execution quality metrics from JSONL traces.

Designed for the current single ReActAgent architecture (nexus tui / nexus run).
Reads structured spans from ~/.agentnexus/traces/*.jsonl and computes:

  Per-trace metrics:
    - steps, tool calls, tokens, latency, answer status

  Aggregate report:
    - answer rate, avg steps, tool success rate, token efficiency
    - latency percentiles, truncation/error rates
    - per-tool breakdown with success counts
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from agentnexus.evaluation.utils import find_trace, load_trace_spans

# Pricing (CNY per million tokens) — mirrors observability/stats.py
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v3": (1.0, 2.0),
    "deepseek-v4-flash": (0.6, 1.2),
    "deepseek-v4-pro": (1.0, 4.0),
    "deepseek-r1": (4.0, 16.0),
    "qwen-max": (2.5, 10.0),
    "gpt-4o": (17.5, 70.0),
    "gpt-4o-mini": (1.0, 4.0),
}

_MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-v3",
    "deepseek-reasoner": "deepseek-r1",
}


def _resolve_model(m: str) -> str:
    m = _MODEL_ALIASES.get(m, m)
    for key in _PRICING:
        if key in m.lower():
            return key
    return m


def _cost(input_tokens: int, output_tokens: int, model: str) -> float:
    key = _resolve_model(model)
    prices = _PRICING.get(key)
    if not prices:
        return 0.0
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


# ── Per-trace record ─────────────────────────────────────────────


@dataclass
class TraceRecord:
    """Metrics extracted from a single trace (one user input → agent execution)."""
    trace_id: str
    task_preview: str = ""
    steps: int = 0
    tool_calls_total: int = 0
    tool_calls_unique: list[str] = field(default_factory=list)
    had_answer: bool = False
    had_error: bool = False
    had_truncation: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0
    tool_counts: dict[str, int] = field(default_factory=dict)
    tool_errors_by_name: dict[str, int] = field(default_factory=dict)

    @property
    def cost_cny(self) -> float:
        return _cost(self.total_input_tokens, self.total_output_tokens, "deepseek-v4-flash")


# ── Aggregate report ─────────────────────────────────────────────


@dataclass
class AgentReport:
    """Aggregate evaluation report over multiple traces."""
    total_traces: int = 0

    # Core metrics
    answer_rate: float = 0.0
    avg_steps: float = 0.0
    tool_success_rate: float = 0.0

    # Reliability
    truncation_rate: float = 0.0
    error_rate: float = 0.0

    # Token & cost
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    avg_cost_cny: float = 0.0
    total_cost_cny: float = 0.0

    # Latency
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    avg_trace_latency_ms: float = 0.0

    # Tool breakdown
    tool_breakdown: dict[str, dict] = field(default_factory=dict)

    # Per-trace details
    traces: list[TraceRecord] = field(default_factory=list)
    failed_traces: list[TraceRecord] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """CI gate: all core metrics must meet thresholds."""
        return (
            self.answer_rate >= 0.85
            and self.tool_success_rate >= 0.80
            and self.truncation_rate < 0.10
            and self.error_rate < 0.10
        )

    def summary(self) -> str:
        lines = [
            f"评估报告: {self.total_traces} 条 trace",
            "",
            (
                f"  答案产出率:  {self.answer_rate:.1%}  "
                f"{'[PASS]' if self.answer_rate >= 0.85 else '[FAIL]'} (阈值 85%)"
            ),
            f"  平均步数:    {self.avg_steps:.1f}",
            (
                f"  工具成功率:  {self.tool_success_rate:.1%}  "
                f"{'[PASS]' if self.tool_success_rate >= 0.80 else '[FAIL]'} (阈值 80%)"
            ),
            (
                f"  截断率:      {self.truncation_rate:.1%}  "
                f"{'[PASS]' if self.truncation_rate < 0.10 else '[FAIL]'} (阈值 <10%)"
            ),
            (
                f"  错误率:      {self.error_rate:.1%}  "
                f"{'[PASS]' if self.error_rate < 0.10 else '[FAIL]'} (阈值 <10%)"
            ),
            "",
            f"  平均输入 token:  {self.avg_input_tokens:,.0f}",
            f"  平均输出 token:  {self.avg_output_tokens:,.0f}",
            f"  平均成本:        CNY {self.avg_cost_cny:.4f}",
            f"  总成本:          CNY {self.total_cost_cny:.4f}",
            "",
            (
                f"  延迟 P50: {self.latency_p50_ms:.0f}ms  "
                f"P95: {self.latency_p95_ms:.0f}ms  P99: {self.latency_p99_ms:.0f}ms"
            ),
        ]
        return "\n".join(lines)


# ── Evaluator ────────────────────────────────────────────────────


class AgentEvaluator:
    """Read JSONL traces and produce per-trace + aggregate metrics."""

    def evaluate_all(self, traces_dir: str, days: int | None = None) -> AgentReport:
        """Evaluate all traces in a directory, optionally filtered by recency."""
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_EVAL_RUN, {
            "traces_dir": traces_dir, "days": days,
        })

        traces: dict[str, list[dict]] = defaultdict(list)
        cutoff = (time.time() - days * 86400) if days else 0

        for f in sorted(Path(traces_dir).glob("*.jsonl")):
            if days and f.stat().st_mtime < cutoff:
                continue
            for tid, spans in load_trace_spans(f).items():
                traces[tid].extend(spans)

        records = [self._evaluate_trace(tid, spans) for tid, spans in traces.items()]
        report = self._aggregate(records)

        hook_mgr.fire(HookType.AFTER_EVAL_RUN, {
            "traces_dir": traces_dir, "days": days,
            "trace_count": len(traces), "record_count": len(records),
        })
        return report

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> TraceRecord | None:
        """Evaluate a single trace by ID."""
        spans = find_trace(traces_dir, trace_id)
        if spans:
            return self._evaluate_trace(trace_id, spans)
        return None

    # ── per-trace logic ──────────────────────────────────────────

    @staticmethod
    def _evaluate_trace(trace_id: str, spans: list[dict]) -> TraceRecord:
        llm_spans = sorted(
            [s for s in spans if s.get("name") == "llm"],
            key=lambda s: s.get("start_time", 0),
        )
        task_span = next((s for s in spans if s.get("name") == "task"), None)

        task_input = (task_span or {}).get("input", {}) or {}
        task_preview = str(task_input.get("task", ""))[:200] if task_input.get("task") else ""
        if not task_preview:
            # Fallback: use input_preview from first llm span
            first_llm = llm_spans[0] if llm_spans else {}
            inp = first_llm.get("input", {}) or {}
            task_preview = str(inp.get("input_preview", ""))[:200]

        rec = TraceRecord(trace_id=trace_id, task_preview=task_preview)

        if not llm_spans:
            return rec

        rec.steps = len(llm_spans)

        for s in llm_spans:
            meta = s.get("metadata", {}) or {}
            status = meta.get("status", "ok")
            rec.total_input_tokens += meta.get("input_tokens", 0) or 0
            rec.total_output_tokens += meta.get("output_tokens", 0) or 0
            rec.total_latency_ms += s.get("latency_ms", 0) or 0
            if status == "error":
                rec.had_error = True
            if meta.get("truncated", False):
                rec.had_truncation = True
            tool_calls: list = meta.get("tool_calls") or []
            rec.tool_calls_total += len(tool_calls)
            for tc in tool_calls:
                name = str(tc)
                rec.tool_counts[name] = rec.tool_counts.get(name, 0) + 1
                if name not in rec.tool_calls_unique:
                    rec.tool_calls_unique.append(name)

        # Determine if agent produced an answer:
        # If the last llm span has no tool_calls → answer returned naturally
        last_meta = (llm_spans[-1].get("metadata", {}) or {})
        last_tool_calls: list = last_meta.get("tool_calls") or []
        rec.had_answer = len(last_tool_calls) == 0

        return rec

    # ── aggregation ──────────────────────────────────────────────

    @staticmethod
    def _aggregate(records: list[TraceRecord]) -> AgentReport:
        report = AgentReport()
        report.total_traces = len(records)

        if not records:
            return report

        report.traces = sorted(records, key=lambda r: r.total_latency_ms, reverse=True)

        total_steps = 0
        total_tool_calls = 0
        answer_count = 0
        truncation_count = 0
        error_count = 0
        trace_latencies: list[float] = []
        tool_calls_by_name: dict[str, int] = defaultdict(int)
        tool_errors_by_name: dict[str, int] = defaultdict(int)

        for r in records:
            total_steps += r.steps
            total_tool_calls += r.tool_calls_total
            report.total_input_tokens += r.total_input_tokens
            report.total_output_tokens += r.total_output_tokens
            report.total_cost_cny += r.cost_cny

            if r.had_answer:
                answer_count += 1
            if r.had_truncation:
                truncation_count += 1
            if r.had_error:
                error_count += 1
                report.failed_traces.append(r)

            tool_errors_by_name_span = getattr(r, 'tool_errors_by_name', {})
            for name, count in tool_errors_by_name_span.items():
                tool_errors_by_name[name] += count

            for name, count in r.tool_counts.items():
                tool_calls_by_name[name] += count

            trace_latencies.append(r.total_latency_ms)

        report.avg_steps = total_steps / len(records)
        report.answer_rate = answer_count / len(records)
        report.truncation_rate = truncation_count / len(records)
        report.error_rate = error_count / len(records)
        report.avg_input_tokens = report.total_input_tokens / len(records)
        report.avg_output_tokens = report.total_output_tokens / len(records)
        report.avg_cost_cny = report.total_cost_cny / len(records)

        total_tool_fails = sum(tool_errors_by_name.values())
        if total_tool_calls > 0:
            report.tool_success_rate = (total_tool_calls - total_tool_fails) / total_tool_calls
        else:
            report.tool_success_rate = 1.0

        # Latency percentiles (per-trace)
        sorted_lat = sorted(trace_latencies)
        if sorted_lat:
            report.avg_trace_latency_ms = sum(sorted_lat) / len(sorted_lat)
            report.latency_p50_ms = _percentile(sorted_lat, 50)
            report.latency_p95_ms = _percentile(sorted_lat, 95)
            report.latency_p99_ms = _percentile(sorted_lat, 99)

        # Tool breakdown
        for name in sorted(tool_calls_by_name):
            total = tool_calls_by_name[name]
            errs = tool_errors_by_name.get(name, 0)
            report.tool_breakdown[name] = {
                "calls": total,
                "errors": errs,
                "success_rate": (total - errs) / total if total else 1.0,
            }

        return report


def _percentile(sorted_values: list[float], pct: int) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100)))
    return round(sorted_values[idx], 1)
