"""报告与采集结构 — RunMetrics（EvalTrace）/ Judges / CaseOutcome / FineGrainedReport。

报告数据优先：每个指标给出具体数值与 n/m，不用 PASS/FAIL 当结论。
judge 判定只用于聚合比率，原始 reasoning 逐用例保留可下钻。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from agentnexus.evaluation.fine_grained.cases import DATASET_PRIMARY_METRIC

logger = logging.getLogger(__name__)

_BOOKKEEPING_TOOLS = {"todo_add", "todo_update"}  # 记账工具不计入工具决策判定


# ── EvalTrace：单次运行的采集结构（文章 §6.3）────────────────────────


@dataclass
class RunMetrics:
    """单次用例运行的成本/性能/行为采集。"""

    wall_ms: float = 0.0
    steps: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)   # [{name, params, ok}]
    json_retries: int = 0
    error: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [c["name"] for c in self.tool_calls]

    @property
    def real_tool_names(self) -> list[str]:
        """剔除记账类工具后的真实工具调用。"""
        return [n for n in self.tool_names if n not in _BOOKKEEPING_TOOLS]


# ── Judge 封装 ────────────────────────────────────────────────────────


def _parse_judge_json(text: str) -> dict:
    """Robustly parse judge JSON output (tolerates prose around it)."""
    if not text:
        return {}
    try:
        return json.loads(text.strip().lstrip("```json").rstrip("```").strip())
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return {}
    return {}


class Judges:
    """单一职责 Judge 集合（文章 §5.2：一个 prompt 只评一个指标，CoT 先行）。"""

    def __init__(self, judge_llm: Any):
        self._llm = judge_llm

    def _ask(self, prompt_name: str, **kwargs) -> dict:
        from agentnexus.prompts import load_prompt
        prompt = load_prompt(prompt_name).format(**kwargs)
        raw = self._llm.think([{"role": "user", "content": prompt}], silent=True) or ""
        data = _parse_judge_json(raw)
        data["_raw_len"] = len(raw)
        return data

    def task_completion(self, user_input: str, answer: str, reference: str = "") -> dict:
        return self._ask("judge_task_completion", user_input=user_input,
                         answer=answer, reference=reference or "（无）")

    def instruction_following(self, user_input: str, answer: str) -> dict:
        return self._ask("judge_instruction_following", user_input=user_input, answer=answer)

    def faithfulness(self, user_input: str, answer: str, context: str) -> dict:
        return self._ask("judge_faithfulness", user_input=user_input,
                         answer=answer, context=context or "（空）")

    def multi_turn(self, conversation: str) -> dict:
        return self._ask("judge_multi_turn", conversation=conversation)

    def abnormal(self, user_input: str, answer: str) -> dict:
        return self._ask("judge_abnormal", user_input=user_input, answer=answer)


# ── 用例结果与报告 ────────────────────────────────────────────────────


@dataclass
class CaseOutcome:
    case_id: str
    dataset: str
    verdict: str = "error"          # pass | fail | skip | error（skip 不计入分母）
    metric_values: dict[str, Any] = field(default_factory=dict)  # 具体数值
    reasoning: str = ""             # judge 的推理（可解释性）
    answer_preview: str = ""        # Agent 回答摘要（用例诊断入口）
    run: RunMetrics = field(default_factory=RunMetrics)


class FineGrainedReport:
    """数据优先的评测报告：每个指标都带具体数值和样本量。"""

    def __init__(self, scope: str, outcomes: list[CaseOutcome],
                 memory_dimensions: dict[str, float] | None = None,
                 memory_probe_rows: list[dict] | None = None,
                 duration_s: float = 0.0):
        self.scope = scope
        self.outcomes = outcomes
        self.memory_dimensions = memory_dimensions or {}
        self.memory_probe_rows = memory_probe_rows or []
        self.duration_s = duration_s

    # ── 指标聚合 ──

    def _rate(self, outcomes: list[CaseOutcome], key: str) -> tuple[float, int, int]:
        """metric_values[key] 为 1.0/0.0 的比率，返回 (rate, hit, total)。"""
        vals = [o for o in outcomes if isinstance(o.metric_values.get(key), (int, float))
                and o.verdict != "skip"]
        if not vals:
            return (0.0, 0, 0)
        hit = sum(1 for o in vals if o.metric_values[key] >= 1.0)
        return (hit / len(vals), hit, len(vals))

    def metrics_table(self) -> dict[str, dict[str, str]]:
        """dataset → {metric: "value (n/m)"} — 报告的唯一事实来源。"""
        table: dict[str, dict[str, str]] = {}
        for ds in {o.dataset for o in self.outcomes}:
            outs = [o for o in self.outcomes if o.dataset == ds]
            row: dict[str, str] = {}
            # 收集该数据集出现过的所有比率先 metrics
            keys = {k for o in outs for k, v in o.metric_values.items()
                    if isinstance(v, (int, float)) and k.endswith("_rate") or k.endswith("accuracy")
                    or k in ("task_completion", "instruction_following", "faithfulness",
                             "multi_turn_completion", "abnormal_handling")}
            for k in sorted(keys):
                rate, hit, total = self._rate(outs, k)
                if total:
                    row[k] = f"{rate:.2f} ({hit}/{total})"
            # 均值类
            for k in ("avg_steps", "avg_tool_calls"):
                vals = [o.metric_values[k] for o in outs if isinstance(o.metric_values.get(k), (int, float))]
                if vals:
                    row[k] = f"{sum(vals) / len(vals):.2f}"
            table[ds] = row
        return table

    def cost_perf(self) -> dict[str, float]:
        runs = [o.run for o in self.outcomes if o.run.wall_ms > 0]
        if not runs:
            return {}
        walls = sorted(r.wall_ms for r in runs)
        p95 = walls[min(len(walls) - 1, int(len(walls) * 0.95))]
        return {
            "用例数": len(runs),
            "总输入tokens": sum(r.input_tokens for r in runs),
            "总输出tokens": sum(r.output_tokens for r in runs),
            "平均输入tokens/用例": round(sum(r.input_tokens for r in runs) / len(runs)),
            "平均输出tokens/用例": round(sum(r.output_tokens for r in runs) / len(runs)),
            "平均LLM调用/用例": round(sum(r.llm_calls for r in runs) / len(runs), 2),
            "平均工具调用/用例": round(sum(len(r.tool_calls) for r in runs) / len(runs), 2),
            "平均延迟s": round(sum(walls) / len(walls) / 1000, 1),
            "P95延迟s": round(p95 / 1000, 1),
        }

    def summary(self) -> str:
        lines = [f"精细化评测报告  scope={self.scope}  用例={len(self.outcomes)}  "
                 f"耗时={self.duration_s:.0f}s", ""]
        lines.append("■ 质量（按数据集，数值为通过率/比率，括号为 命中/有效样本）")
        for ds, row in self.metrics_table().items():
            primary = DATASET_PRIMARY_METRIC.get(ds, "")
            lines.append(f"  [{ds}] 主指标: {primary}")
            for k, v in row.items():
                mark = " ←主指标" if k == primary or k.startswith(primary) else ""
                lines.append(f"    {k}: {v}{mark}")
        if self.memory_dimensions:
            lines.append("  [memory_probes] 记忆探针各维度通过率:")
            for d, rate in self.memory_dimensions.items():
                lines.append(f"    {d}: {rate:.2f}")
        cp = self.cost_perf()
        if cp:
            lines.append("")
            lines.append("■ 成本与性能")
            for k, v in cp.items():
                lines.append(f"  {k}: {v}")
        skipped = [o for o in self.outcomes if o.verdict == "skip"]
        failed = [o for o in self.outcomes if o.verdict == "fail"]
        errors = [o for o in self.outcomes if o.verdict == "error"]
        lines.append("")
        lines.append(f"■ 用例结果分布: fail={len(failed)}  skip={len(skipped)}  error={len(errors)}  "
                     f"其余={len(self.outcomes) - len(failed) - len(skipped) - len(errors)}")
        if failed:
            lines.append("")
            lines.append("■ 失败明细（含 judge 推理）")
            for o in failed:
                lines.append(f"  [{o.case_id}] {o.reasoning[:150]}")
        if skipped:
            lines.append("")
            lines.append("■ 跳过明细（上游未触发，不计入指标）")
            for o in skipped:
                lines.append(f"  [{o.case_id}] {o.reasoning[:120]}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "duration_s": self.duration_s,
            "metrics": self.metrics_table(),
            "memory_dimensions": self.memory_dimensions,
            "cost_perf": self.cost_perf(),
            "outcomes": [
                {
                    "case_id": o.case_id, "dataset": o.dataset, "verdict": o.verdict,
                    "metric_values": o.metric_values, "reasoning": o.reasoning,
                    "answer_preview": o.answer_preview,
                    "run": {
                        "wall_ms": o.run.wall_ms, "steps": o.run.steps,
                        "llm_calls": o.run.llm_calls,
                        "input_tokens": o.run.input_tokens,
                        "output_tokens": o.run.output_tokens,
                        "tool_calls": o.run.tool_calls,
                    },
                }
                for o in self.outcomes
            ],
        }
