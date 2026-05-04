"""Token 成本统计 — 从 JSONL trace 文件聚合历史任务的资源消耗"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict


# DeepSeek V3 官方定价（人民币/百万 token）
_PRICING = {
    "deepseek-v3":      (1.0, 2.0),
    "deepseek-v4-flash": (0.6, 1.2),
    "deepseek-v4-pro":   (1.0, 4.0),
    "deepseek-r1":       (4.0, 16.0),
    "qwen-max":          (2.5, 10.0),
    "gpt-4o":            (17.5, 70.0),
    "gpt-4o-mini":       (1.0, 4.0),
}

_MODEL_ALIASES = {
    "deepseek-chat": "deepseek-v3",
    "deepseek-reasoner": "deepseek-r1",
}


@dataclass
class TokenStats:
    total_tasks: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_cny: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    avg_retries: float = 0.0
    by_model: dict[str, dict] = field(default_factory=dict)
    by_date: dict[str, dict] = field(default_factory=dict)


def _cost(input_tokens: int, output_tokens: int, model: str) -> float:
    model = _MODEL_ALIASES.get(model, model)
    for key, (in_price, out_price) in _PRICING.items():
        if key in model.lower():
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return 0.0


def _short_model(model: str) -> str:
    for alias, full in _MODEL_ALIASES.items():
        if model == alias:
            return full
    return model


def compute_stats(traces_dir: str, days: int = 7) -> TokenStats:
    base = Path(traces_dir)
    if not base.exists():
        return TokenStats()

    cutoff = time.time() - days * 86400
    stats = TokenStats()
    seen_traces: set[str] = set()
    all_latencies: list[float] = []
    trace_plan_counts: dict[str, int] = defaultdict(int)

    date_model_traces: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set))
    date_model_tokens: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"input": 0, "output": 0}))

    jsonl_files = sorted(base.glob("*.jsonl"), reverse=True)
    for f in jsonl_files:
        try:
            file_date = f.stem
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if span.get("start_time", 0) < cutoff:
                        continue

                    tid = span.get("trace_id", "")

                    if span.get("name") == "task":
                        seen_traces.add(tid)

                    if span.get("name") == "plan_node":
                        trace_plan_counts[tid] += 1

                    meta = span.get("metadata", {})
                    tokens_in = meta.get("input_tokens", 0)
                    tokens_out = meta.get("output_tokens", 0)
                    model = _short_model(meta.get("model", "deepseek-v4-flash"))
                    latency = span.get("latency_ms", 0)

                    if tokens_in or tokens_out:
                        stats.total_input_tokens += tokens_in
                        stats.total_output_tokens += tokens_out
                        stats.total_cost_cny += _cost(tokens_in, tokens_out, model)
                        date_model_traces[file_date][model].add(tid)
                        date_model_tokens[file_date][model]["input"] += tokens_in
                        date_model_tokens[file_date][model]["output"] += tokens_out

                    if latency > 0:
                        all_latencies.append(latency)

        except Exception:
            continue

    stats.total_tasks = len(seen_traces)

    if trace_plan_counts:
        retries = [max(0, trace_plan_counts[t] - 1) for t in seen_traces if t in trace_plan_counts]
        stats.avg_retries = round(sum(retries) / len(retries), 2) if retries else 0.0

    if all_latencies:
        all_latencies.sort()
        stats.avg_latency_ms = round(sum(all_latencies) / len(all_latencies), 1)
        stats.max_latency_ms = round(all_latencies[-1], 1)
        p95_idx = math.ceil(len(all_latencies) * 0.95) - 1
        stats.p95_latency_ms = round(all_latencies[max(0, p95_idx)], 1)

    stats.by_model = {
        model: {
            "tasks": len(set.union(*(dm[model] for dm in date_model_traces.values() if model in dm))),
            "input_tokens": sum(date_model_tokens[dm][model]["input"]
                                for dm in date_model_tokens if model in date_model_tokens[dm]),
            "output_tokens": sum(date_model_tokens[dm][model]["output"]
                                 for dm in date_model_tokens if model in date_model_tokens[dm]),
            "cost_cny": round(sum(
                _cost(date_model_tokens[dm][model]["input"],
                      date_model_tokens[dm][model]["output"], model)
                for dm in date_model_tokens if model in date_model_tokens[dm]
            ), 4),
        }
        for model in sorted(
            set(m for dm in date_model_tokens for m in date_model_tokens[dm]),
            key=lambda m: sum(date_model_tokens[dm][m]["input"]
                              for dm in date_model_tokens if m in date_model_tokens[dm]),
            reverse=True,
        )
    }

    stats.by_date = {}
    for d in sorted(date_model_traces.keys(), reverse=True):
        stats.by_date[d] = {}
        for model in date_model_traces[d]:
            stats.by_date[d][model] = {
                "tasks": len(date_model_traces[d][model]),
                "input": date_model_tokens[d][model]["input"],
                "output": date_model_tokens[d][model]["output"],
            }

    return stats
