"""Re-judge RGB noise reports against DeepSeek with thinking disabled.

The AgentLLM path probes deepseek-flash into thinking mode, where each judge
call streams minutes of reasoning — unacceptable for 0-1 scoring. This script
calls the OpenAI-compatible endpoint directly with thinking disabled and
8-way concurrency: ~2 minutes per 300-query report.
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from agentnexus.eval.benchmarks.rgb_loader import load_rgb_queries

API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"

PROMPT = """你是 RAG 评估专家。请判断回答与标准答案的一致程度(correctness)。

[问题]
{question}

[标准答案]
{ground_truth}

[回答]
{answer}

要求:先简要分析,最后一行只输出 0 到 1 之间的一个分数(保留两位小数)。"""


def parse_score(text: str) -> float:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    for line in reversed(lines):
        m = re.search(r"([01](?:\.\d+)?)", line)
        if m:
            return max(0.0, min(1.0, float(m.group(1))))
    return 0.0


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    return key


def judge_one(client: httpx.Client, query, generation: str, limiter) -> tuple[bool, float, str]:
    limiter.acquire()
    try:
        r = client.post(API, headers={"Authorization": f"Bearer {_api_key()}"}, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT.format(
                question=query.query, ground_truth=query.ground_truth_text(), answer=generation)}],
            "max_tokens": 800,
            "thinking": {"type": "disabled"},
        }, timeout=90)
        if r.status_code != 200:
            return False, 0.0, f"HTTP {r.status_code}"
        content = r.json()["choices"][0]["message"].get("content") or ""
        score = parse_score(content)
        return score >= 0.5, score, ""
    except Exception as exc:
        return False, 0.0, f"{type(exc).__name__}: {exc}"


def rejudge(report_path: str):
    from agentnexus.eval.benchmarks.ratelimit import RateLimiter

    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    lang = payload["summary"]["language"]
    records = payload["records"]
    queries = {q.query_id: q for q in load_rgb_queries(lang, "noise", offline=True)}

    limiter = RateLimiter(rate_per_minute=240)
    client = httpx.Client()
    t0 = time.perf_counter()

    def work(record):
        q = queries.get(str(record["query_id"]))
        if q is None:
            record["rejudge_error"] = "query not found"
            return record
        ok, score, err = judge_one(client, q, record.get("generation", ""), limiter)
        record["correct_new"], record["judge_score_new"], record["rejudge_error"] = ok, score, err
        return record

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(work, records))

    buckets = {}
    for r in records:
        if r.get("rejudge_error"):
            continue
        nr = r.get("noise_rate", 0)
        key = "0.0-0.5" if nr < 0.5 else ("0.5-0.8" if nr < 0.8 else "0.8+")
        buckets.setdefault(key, []).append(bool(r["correct_new"]))

    n_ok = sum(1 for r in records if not r.get("rejudge_error"))
    n_correct = sum(1 for r in records if r.get("correct_new") and not r.get("rejudge_error"))
    out = {
        "kind": "rgb-e2e-rejudge",
        "source_report": report_path,
        "new_judge": f"{MODEL} (direct, thinking disabled)",
        "n_total": len(records),
        "n_judged": n_ok,
        "n_errors": len(records) - n_ok,
        "accuracy_new": n_correct / max(n_ok, 1),
        "by_noise_bucket_new": {k: sum(v) / len(v) for k, v in buckets.items()},
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "records": records,
    }
    dest = Path(report_path).with_name(f"rejudge-{Path(report_path).stem}.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{lang}: accuracy={out['accuracy_new']:.4f} errors={out['n_errors']} "
          f"buckets={ {k: round(v, 3) for k, v in out['by_noise_bucket_new'].items()} } -> {dest}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        rejudge(p)
