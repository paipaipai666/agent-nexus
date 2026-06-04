"""统计引擎 — pass@k / pass^k / bootstrap CI 计算。

对应 Anthropic 方法论中的非确定性评估指标:
  - pass@k: 至少一次成功的概率
  - pass^k: 全部成功的概率
  - bootstrap CI: 置信区间
"""

from __future__ import annotations

import math
import random
from typing import Any


def pass_at_k(n: int, c: int, k: int) -> float:
    """精确 pass@k 公式: 1 - C(n-c, k) / C(n, k)。

    n = 总 trial 数
    c = 成功 trial 数
    k = 目标 k (至少 k 次中的 1 次成功)

    参考: https://proceedings.neurips.cc/paper/2019/file/7298332f04ac004a0ca44cc69ecf6f6b-Paper.pdf
    """
    if n <= 0 or k <= 0:
        return 0.0
    if c <= 0:
        return 0.0
    if k > n:
        k = n
    if c >= n:
        return 1.0
    # 1 - C(n-c, k) / C(n, k)
    # 使用 log 避免大数溢出
    try:
        log_num = _log_combination(n - c, k)
        log_den = _log_combination(n, k)
        ratio = math.exp(log_num - log_den)
        return 1.0 - ratio
    except (ValueError, OverflowError):
        # 回退到近似计算
        return _pass_at_k_approximate(n, c, k)


def _log_combination(n: int, k: int) -> float:
    """计算 log(C(n, k))。"""
    if k < 0 or k > n:
        return float('-inf')
    if k == 0 or k == n:
        return 0.0
    # log(n!) - log(k!) - log((n-k)!)
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _pass_at_k_approximate(n: int, c: int, k: int) -> float:
    """pass@k 的近似计算（蒙特卡洛）。"""
    trials = 10000
    success = 0
    for _ in range(trials):
        # 随机选 k 个 (不放回)
        sample = random.sample(range(n), min(k, n))
        if any(i < c for i in sample):
            success += 1
    return success / trials


def pass_hat_k(per_trial_success_rate: float, k: int) -> float:
    """pass^k: p^k — 全部 k 次 trial 都成功的概率。

    per_trial_success_rate: 单次 trial 成功率 (0.0-1.0)
    k: trial 数
    """
    if k <= 0:
        return 0.0
    if per_trial_success_rate <= 0:
        return 0.0
    if per_trial_success_rate >= 1:
        return 1.0
    return per_trial_success_rate ** k


def compute_pass_metrics(n_trials: int, n_success: int) -> dict[str, Any]:
    """计算完整的 pass 指标集。

    Returns:
        {
            "pass_at_1": float,
            "pass_at_3": float,
            "pass_at_5": float,
            "pass_at_10": float,
            "pass_hat_1": float,
            "pass_hat_3": float,
            "pass_hat_5": float,
            "pass_hat_10": float,
            "success_rate": float,
        }
    """
    success_rate = n_success / n_trials if n_trials > 0 else 0.0

    result: dict[str, Any] = {
        "success_rate": success_rate,
    }

    # pass@k for common k values
    for k in [1, 3, 5, 10]:
        if k <= n_trials:
            result[f"pass_at_{k}"] = pass_at_k(n_trials, n_success, k)
        else:
            result[f"pass_at_{k}"] = None

    # pass^k for common k values
    for k in [1, 3, 5, 10]:
        result[f"pass_hat_{k}"] = pass_hat_k(success_rate, k)

    return result


def bootstrap_ci(
    values: list[float],
    n_resample: int = 1000,
    ci: float = 0.95,
    statistic: str = "mean",
) -> tuple[float, float]:
    """Bootstrap 置信区间。

    Args:
        values: 观测值列表
        n_resample: 重采样次数
        ci: 置信水平 (如 0.95)
        statistic: 统计量 ("mean" 或 "median")

    Returns:
        (lower, upper) 置信区间
    """
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])

    rng = random.Random(42)  # 固定种子以保证可复现
    stats: list[float] = []

    for _ in range(n_resample):
        sample = [rng.choice(values) for _ in range(len(values))]
        if statistic == "median":
            sample.sort()
            mid = len(sample) // 2
            stat = sample[mid] if len(sample) % 2 else (sample[mid - 1] + sample[mid]) / 2
        else:
            stat = sum(sample) / len(sample)
        stats.append(stat)

    stats.sort()
    alpha = (1 - ci) / 2
    lower_idx = int(alpha * n_resample)
    upper_idx = int((1 - alpha) * n_resample) - 1
    lower_idx = max(0, min(lower_idx, n_resample - 1))
    upper_idx = max(0, min(upper_idx, n_resample - 1))

    return (stats[lower_idx], stats[upper_idx])


def compute_trial_consistency(scores: list[float]) -> dict[str, float]:
    """trial 一致性统计。

    Returns:
        {
            "mean": float,
            "std": float,
            "min": float,
            "max": float,
            "p5": float,   # 5th percentile
            "p95": float,  # 95th percentile
            "iqr": float,  # interquartile range
        }
    """
    if not scores:
        return {"mean": 0, "std": 0, "min": 0, "max": 0, "p5": 0, "p95": 0, "iqr": 0}

    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    mean = sum(sorted_scores) / n
    variance = sum((s - mean) ** 2 for s in sorted_scores) / n
    std = math.sqrt(variance)

    def percentile(p: float) -> float:
        idx = p * (n - 1)
        lower = int(math.floor(idx))
        upper = int(math.ceil(idx))
        if lower == upper:
            return sorted_scores[lower]
        frac = idx - lower
        return sorted_scores[lower] * (1 - frac) + sorted_scores[upper] * frac

    p5 = percentile(0.05)
    p95 = percentile(0.95)
    p25 = percentile(0.25)
    p75 = percentile(0.75)

    return {
        "mean": mean,
        "std": std,
        "min": sorted_scores[0],
        "max": sorted_scores[-1],
        "p5": p5,
        "p95": p95,
        "iqr": p75 - p25,
    }


def compute_saturation_score(
    pass_rates: list[float],
    difficulty_distribution: dict[str, float] | None = None,
) -> dict[str, Any]:
    """评估饱和度。

    当所有任务都通过时，eval 已饱和，需要升级难度。

    Returns:
        {
            "saturation": float,  # 0-1, 1 = 全部通过
            "is_saturated": bool,
            "difficulty_distribution": {...},
            "upgrade_suggestion": str | None,
        }
    """
    if not pass_rates:
        return {
            "saturation": 0.0,
            "is_saturated": False,
            "difficulty_distribution": difficulty_distribution or {},
            "upgrade_suggestion": None,
        }

    saturation = sum(pass_rates) / len(pass_rates)
    is_saturated = saturation >= 0.95

    suggestion = None
    if is_saturated:
        suggestion = "Eval 已饱和 (>=95% 通过率)。建议添加更难的任务或将已通过的任务 graduated 到 regression suite。"
    elif saturation >= 0.80:
        suggestion = "Eval 接近饱和 (>=80% 通过率)。考虑添加更难的任务。"

    return {
        "saturation": saturation,
        "is_saturated": is_saturated,
        "difficulty_distribution": difficulty_distribution or {},
        "upgrade_suggestion": suggestion,
    }
