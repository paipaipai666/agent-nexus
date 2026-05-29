"""Probe the Judge LLM API concurrency limit.

Usage: python scripts/probe_rate_limit.py [--max-concurrent 16]

Sends increasing numbers of concurrent requests to the judge API,
reports how many succeed vs 429, and identifies the safe concurrency level.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, ".")

from agentnexus.core.config import get_settings


def make_one_request(idx: int, model: str, api_key: str, base_url: str, timeout: int) -> dict:
    """Make a single minimal LLM call and report status."""
    import litellm

    messages = [{"role": "user", "content": "Reply with only the number 1."}]
    t0 = time.perf_counter()
    try:
        resp = litellm.completion(
            model=model,
            messages=messages,
            api_key=api_key,
            api_base=base_url,
            max_tokens=5,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - t0
        usage = resp.usage or {}
        return {
            "idx": idx,
            "status": "ok",
            "elapsed": round(elapsed, 2),
            "tokens": getattr(usage, "total_tokens", 0),
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        err = str(e)
        is_429 = "429" in err or "rate" in err.lower() or "limit" in err.lower()
        return {
            "idx": idx,
            "status": "429" if is_429 else "error",
            "elapsed": round(elapsed, 2),
            "error": err[:120],
        }


def probe_concurrency(n: int, model: str, api_key: str, base_url: str, timeout: int):
    """Send n concurrent requests and report results."""
    print(f"\n--- 并发数 = {n} ---")
    t0 = time.perf_counter()
    results = []

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {
            pool.submit(make_one_request, i, model, api_key, base_url, timeout): i
            for i in range(n)
        }
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            icon = "ok" if r["status"] == "ok" else "429" if r["status"] == "429" else "ERR"
            print(f"  [{icon}] #{r['idx']} {r['elapsed']}s", end="")
            if r.get("error"):
                print(f"  {r['error'][:60]}", end="")
            print()

    total_time = time.perf_counter() - t0
    ok = sum(1 for r in results if r["status"] == "ok")
    rate_limited = sum(1 for r in results if r["status"] == "429")
    errors = sum(1 for r in results if r["status"] == "error")
    avg_latency = sum(r["elapsed"] for r in results if r["status"] == "ok") / max(ok, 1)

    print(f"  结果: {ok} 成功 / {rate_limited} 限流 / {errors} 错误 | "
          f"总耗时 {total_time:.1f}s | 平均延迟 {avg_latency:.1f}s")
    return ok, rate_limited, errors


def main():
    parser = argparse.ArgumentParser(description="Probe judge API concurrency limit")
    parser.add_argument("--max-concurrent", type=int, default=16, help="Max concurrent requests to test")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout (seconds)")
    args = parser.parse_args()

    settings = get_settings()
    model = settings.judge_model_id
    api_key = settings.judge_api_key.get_secret_value()
    base_url = settings.judge_base_url

    print(f"Judge API: {model} @ {base_url}")
    print(f"测试并发上限 (最多 {args.max_concurrent} 个同时请求)...\n")

    # Ramp up: 1, 2, 4, 8, 12, 16, ...
    levels = []
    n = 1
    while n <= args.max_concurrent:
        levels.append(n)
        if n < 4:
            n += 1
        else:
            n += 4

    safe_level = 1
    for level in levels:
        ok, rate_limited, errors = probe_concurrency(level, model, api_key, base_url, args.timeout)
        if rate_limited == 0 and errors == 0:
            safe_level = level
        else:
            print(f"\n!!! 在并发数={level} 时触发限流，安全并发上限 = {safe_level} !!!")
            break
        time.sleep(2)  # Cool-down between rounds

    print(f"\n=== 结论: 安全并发数 = {safe_level} ===")
    print(f"建议: nexus eval run --parallel --jobs {safe_level}")


if __name__ == "__main__":
    main()
