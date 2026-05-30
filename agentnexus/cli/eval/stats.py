"""Statistical helpers for eval CLI commands."""

import json
from pathlib import Path

from agentnexus.cli import console


def _compute_calibration(samples: list[dict], score_file: str) -> None:
    """Compute Spearman/Pearson correlation between Judge and human scores."""
    score_path = Path(score_file)
    if not score_path.exists():
        console.print(f"[red]Score file not found: {score_file}[/red]")
        return

    try:
        human_scores = json.loads(score_path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Failed to read score file: {e}[/red]")
        return

    human_map = {}
    for h in human_scores:
        if h.get("human_precision") is not None or h.get("human_recall") is not None:
            human_map[h["sample_idx"]] = h

    judge_pre = []
    judge_rec = []
    human_pre = []
    human_rec = []
    labels = []

    for s in samples:
        idx = s["sample_idx"]
        if idx not in human_map:
            continue
        h = human_map[idx]
        jp, jr = s["judge_precision"], s["judge_recall"]
        hp, hr = h.get("human_precision"), h.get("human_recall")
        if jp is not None and hp is not None:
            judge_pre.append(jp)
            human_pre.append(hp)
            labels.append(f"#{idx}")
        if jr is not None and hr is not None:
            judge_rec.append(jr)
            human_rec.append(hr)

    console.print(f"\n[bold]Calibration Results[/bold] ({len(labels)} samples)\n")

    if len(judge_pre) >= 3:
        sp, pp = _spearman(judge_pre, human_pre)
        pr, _ = _pearson(judge_pre, human_pre)
        console.print(f"[bold]Precision[/bold]  Spearman ρ={sp:.3f} (p={pp:.4f})  Pearson r={pr:.3f}")

        console.print("  Judge → Human scatter (Precision):")
        for j, h, lbl in sorted(zip(judge_pre, human_pre, labels), key=lambda x: x[1]):
            bar = "█" * max(1, int(h * 20))
            console.print(f"    {lbl:>4s}  J={j:.2f}  H={h:.2f}  {bar}")
    else:
        console.print("[yellow]Precision: too few samples (<3) to compute correlation[/yellow]")

    if len(judge_rec) >= 3:
        sr, prr = _spearman(judge_rec, human_rec)
        rr, _ = _pearson(judge_rec, human_rec)
        console.print(f"[bold]Recall[/bold]     Spearman ρ={sr:.3f} (p={prr:.4f})  Pearson r={rr:.3f}")

        console.print("  Judge → Human scatter (Recall):")
        for j, h, lbl in sorted(zip(judge_rec, human_rec, labels), key=lambda x: x[1]):
            bar = "█" * max(1, int(h * 20))
            console.print(f"    {lbl:>4s}  J={j:.2f}  H={h:.2f}  {bar}")

    console.print("\n[dim]ρ > 0.7: strong | ρ 0.4~0.7: moderate | ρ < 0.4: weak correlation[/dim]")
    console.print("[dim]Judge consistency threshold: Spearman ρ > 0.7[/dim]")


def _spearman(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Spearman rank correlation with p-value (manual implementation)."""
    n = len(x)
    if n < 3:
        return (0.0, 1.0)
    try:
        from scipy.stats import spearmanr
        res = spearmanr(x, y)
        return (float(res.statistic), float(res.pvalue))
    except ImportError:
        pass

    # Manual fallback
    def _rank(vals):
        sorted_vals = sorted(vals)
        return [sorted_vals.index(v) + 1 for v in vals]

    rx, ry = _rank(x), _rank(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    rho = 1.0 - (6.0 * d2) / (n * (n * n - 1))
    # Approximate p-value via t-distribution
    import math
    try:
        t_stat = rho * math.sqrt((n - 2) / (1 - rho * rho))
        from scipy.stats import t as t_dist
        p_val = 2.0 * t_dist.sf(abs(t_stat), df=n - 2)
    except Exception:
        p_val = 0.0
    return (round(rho, 4), round(p_val, 4))


def _pearson(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Pearson correlation coefficient (manual implementation)."""
    n = len(x)
    if n < 3:
        return (0.0, 1.0)
    if all(v == x[0] for v in x) or all(v == y[0] for v in y):
        return (0.0, 1.0)
    try:
        from scipy.stats import pearsonr
        res = pearsonr(x, y)
        return (float(res.statistic), float(res.pvalue))
    except ImportError:
        pass

    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    if den == 0:
        return (0.0, 1.0)
    r = num / den
    import math
    try:
        t_stat = r * math.sqrt((n - 2) / (1 - r * r))
        from scipy.stats import t as t_dist
        p_val = 2.0 * t_dist.sf(abs(t_stat), df=n - 2)
    except Exception:
        p_val = 0.0
    return (round(r, 4), round(p_val, 4))
