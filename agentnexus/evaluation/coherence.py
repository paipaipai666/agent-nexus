"""Multi-Step Coherence Evaluator — Judge LLM scores trace-level logical flow.

Uses the independent judge model (different family from generator) per article rule:
  "Use a different model family for the judge than for the generator."

Checks: Does each step build on prior steps? Does the final output reflect the full
reasoning chain? Critical for traces of 4+ steps.

Article threshold: >0.85 coherence on 4+ step traces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

COHERENCE_PROMPT = """你是一个 AI 评估助手，专门评估多步 Agent 推理的连贯性。
你需要根据提供的 trace 步骤，判断每条后续步骤是否建立在前面的步骤之上。
忽略具体的任务内容，只关注步骤之间的衔接性。

Trace 信息:
{steps_text}

请根据以上 trace，评分（0 到 10 分）：

**连贯性分数**: <分数>
**主要问题**（如有）: <简要描述>
"""


@dataclass
class CoherenceReport:
    trace_id: str
    total_steps: int = 0
    coherence_score: float = 0.0
    issues: str = ""

    @property
    def passed(self) -> bool:
        if self.total_steps < 4:
            return True  # short traces don't stress coherence
        return self.coherence_score >= 8.5  # scaled to 0-10, 8.5 ≈ 0.85

    def summary(self) -> str:
        return f"Trace: {self.trace_id} | Steps: {self.total_steps} | Coherence: {self.coherence_score:.1f}/10"


class CoherenceEvaluator:
    """Evaluate multi-step coherence using the independent judge LLM."""

    def evaluate_all(self, traces_dir: str) -> list[CoherenceReport]:
        reports: list[CoherenceReport] = []
        for f in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
            traces = self._load_traces(str(f))
            for tid, spans in traces.items():
                if len(spans) >= 3:  # need enough steps
                    reports.append(self._evaluate_one(tid, spans))
        return reports

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> CoherenceReport | None:
        for f in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
            with open(f, "r", encoding="utf-8") as fh:
                spans = [json.loads(line) for line in fh
                         if line.strip() and json.loads(line.strip()).get("trace_id") == trace_id]
                if spans:
                    return self._evaluate_one(trace_id, spans)
        return None

    def _evaluate_one(self, trace_id: str, spans: list[dict]) -> CoherenceReport:
        named = [s for s in spans if s.get("name") and s["name"] != "task"]
        named.sort(key=lambda s: s.get("start_time", 0))

        report = CoherenceReport(trace_id=trace_id, total_steps=len(named))
        if len(named) < 2:
            report.coherence_score = 10.0
            return report

        # Build step summary for the judge
        steps_text_parts = []
        for i, s in enumerate(named):
            name = s.get("name", "unknown")
            meta_status = s.get("metadata", {}).get("status", "ok")
            out = str(s.get("output", ""))[:200]
            steps_text_parts.append(
                f"Step {i + 1} [{name}] status={meta_status}: {out}"
            )
        steps_text = "\n".join(steps_text_parts)

        try:
            from agentnexus.core.judge_llm import get_judge_llm
            judge = get_judge_llm()
            response = judge.think(
                [{"role": "user", "content": COHERENCE_PROMPT.format(steps_text=steps_text)}],
                silent=True,
            ) or ""
        except Exception:
            return report  # return with score=0 on judge failure

        score = self._parse_score(response)
        report.coherence_score = score
        report.issues = response[response.find("主要问题"):][:200] if "主要问题" in response else ""
        return report

    @staticmethod
    def _parse_score(response: str) -> float:
        import re
        m = re.search(r'[连貫]贯性分[数数].*?[：:]\s*(\d+\.?\d*)', response)
        if not m:
            m = re.search(r'[Sscore]+[：:]\s*(\d+\.?\d*)', response, re.IGNORECASE)
        if not m:
            m = re.search(r'(\d+\.?\d*)\s*分', response)
        if not m:
            m = re.search(r'(\d+\.?\d*)', response)
        if m:
            return min(10.0, max(0.0, float(m.group(1))))
        return 5.0  # default mid-score on parse failure

    @staticmethod
    def _load_traces(filepath: str) -> dict[str, list[dict]]:
        traces: dict[str, list[dict]] = {}
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                traces.setdefault(span.get("trace_id", "unknown"), []).append(span)
        return traces
