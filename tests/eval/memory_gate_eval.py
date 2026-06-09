"""Evaluation dataset for the memory gate prompt.

Each tuple is (question, answer, expected_label).
expected_label: "yes", "no", or "borderline" (excluded from metrics).

Run with: python -m pytest tests/eval/memory_gate_eval.py -v
"""

import json
from pathlib import Path

# ── Labeled samples ────────────────────────────────────────────────
SAMPLES: list[tuple[str, str, str]] = [
    # === Should extract (yes) ===
    ("我叫张伟", "好的，记住了", "yes"),
    ("我喜欢用 Neovim 写代码", "了解", "yes"),
    ("我不吃香菜", "记住了", "yes"),
    ("以后回答简短一点", "好的", "yes"),
    ("我在北京做后端开发", "了解你的情况", "yes"),
    ("我们决定用 PostgreSQL 作为主数据库", "好的", "yes"),
    ("我的项目用 Python 3.12", "明白", "yes"),
    ("我习惯用 Docker 部署", "了解", "yes"),
    ("记住我不喜欢 Tabs，只用 Spaces", "好的，已记住", "yes"),
    ("我的邮箱是 test@example.com", "已记录", "yes"),
    ("我们公司叫字节跳动", "了解", "yes"),
    ("我住在上海浦东", "好的", "yes"),
    ("以后都用 TypeScript 写前端", "明白", "yes"),
    ("我偏好函数式编程风格", "了解", "yes"),
    ("我的名字叫小明", "你好小明", "yes"),
    ("我喜欢 Go 的并发模型", "Go 的 goroutine 确实很优雅", "yes"),
    ("我们团队用 Kubernetes 管理集群", "了解你们的技术栈", "yes"),
    ("我不喜欢用 jQuery", "好的，以后不会推荐", "yes"),
    ("记住我是一个前端工程师", "好的", "yes"),
    ("我用 Mac 开发", "了解", "yes"),

    # === Should NOT extract (no) ===
    ("帮我把文件移一下", "好的，已移动", "no"),
    ("怎么用 grep", "grep -r 'pattern' .", "no"),
    ("Python 列表排序怎么做", "用 sorted() 或 list.sort()", "no"),
    ("帮我写一个快速排序", "def quicksort(arr): ...", "no"),
    ("运行一下测试", "python -m pytest 通过了", "no"),
    ("搜索一下 Redis 的用法", "Redis 是一个内存数据库...", "no"),
    ("帮我查一下这个 API 文档", "文档内容如下...", "no"),
    ("执行 npm install", "安装完成，共 42 个包", "no"),
    ("这个 bug 怎么修", "在第 15 行加一个空值检查", "no"),
    ("帮我重构这个函数", "已拆分为三个子函数", "no"),
    ("好的谢谢", "不客气", "no"),
    ("明白了", "好的", "no"),
    ("继续", "下一步是...", "no"),
    ("确认", "已执行", "no"),
    ("现在几点了", "现在是下午 3 点", "no"),
    ("今天天气怎么样", "今天晴天", "no"),
    ("帮我看看这段代码有没有问题", "代码没有问题", "no"),
    ("把缩进改成 4 个空格", "已修改", "no"),
    ("删掉第 10 行", "已删除", "no"),
    ("格式化这个 JSON 文件", "已格式化", "no"),

    # === Borderline (excluded from metrics) ===
    ("我在写一个排序算法", "用快速排序比较好", "borderline"),
    ("这个项目用的是 React", "了解", "borderline"),
    ("我觉得这个方案不太好", "那我们换一个方案", "borderline"),
    ("帮我看看这个报错", "是空指针异常，在第 5 行", "borderline"),
    ("这个函数的性能不太好", "可以用缓存优化", "borderline"),
]


def test_sample_count():
    """Ensure we have enough labeled samples."""
    non_borderline = [s for s in SAMPLES if s[2] != "borderline"]
    assert len(non_borderline) >= 30, f"Need >=30 evaluable samples, got {len(non_borderline)}"


def _run_gate_dry_run() -> dict[str, str]:
    """Run the rule-level filter on all samples (no LLM calls).

    Returns {question+answer: predicted_label}.
    This is a baseline smoke test — the full eval also tests the LLM gate.
    """
    from agentnexus.memory.manager import MemoryManager

    # Create a minimal instance just for rule testing
    class _FakeLLM:
        def think(self, *a, **kw):
            return "no"

    mgr = MemoryManager.__new__(MemoryManager)
    mgr._llm = _FakeLLM()
    mgr._gate_state = None  # won't be used in rules-only path

    predictions = {}
    for q, a, _ in SAMPLES:
        rule_result = mgr._should_extract_rules(q, a)
        if rule_result == "yes":
            predictions[q + "|" + a] = "yes"
        elif rule_result == "no":
            predictions[q + "|" + a] = "no"
        else:
            predictions[q + "|" + a] = "uncertain"
    return predictions


def test_rules_precision_on_no():
    """Rule filter should correctly reject all clear 'no' samples."""
    predictions = _run_gate_dry_run()
    false_positives = []
    for q, a, label in SAMPLES:
        if label != "no":
            continue
        pred = predictions.get(q + "|" + a, "uncertain")
        if pred == "yes":
            false_positives.append(f"Q: {q} A: {a}")
    assert not false_positives, f"Rules falsely passed these as 'yes': {false_positives}"


def test_rules_recall_on_yes_with_signals():
    """Rule filter should catch all 'yes' samples that contain strong signals."""
    predictions = _run_gate_dry_run()
    signal_samples = [
        (q, a, l) for q, a, l in SAMPLES
        if l == "yes" and any(
            sig in q + a
            for sig in ["记住", "我叫", "我的名字", "我喜欢", "我不喜欢", "以后都", "偏好"]
        )
    ]
    missed = []
    for q, a, label in signal_samples:
        pred = predictions.get(q + "|" + a, "uncertain")
        if pred != "yes":
            missed.append(f"Q: {q} A: {a} (pred={pred})")
    assert not missed, f"Rules missed these strong-signal 'yes' samples: {missed}"


def _compute_metrics(predictions: dict[str, str]) -> dict:
    """Compute accuracy, precision, recall from predictions."""
    tp = fp = fn = tn = 0
    for q, a, label in SAMPLES:
        if label == "borderline":
            continue
        pred = predictions.get(q + "|" + a, "uncertain")
        # Map uncertain to "no" for metric computation (conservative)
        pred_label = pred if pred != "uncertain" else "no"

        if label == "yes" and pred_label == "yes":
            tp += 1
        elif label == "no" and pred_label == "no":
            tn += 1
        elif label == "no" and pred_label == "yes":
            fp += 1
        elif label == "yes" and pred_label == "no":
            fn += 1

    total = tp + tn + fp + fn
    return {
        "accuracy": round((tp + tn) / total, 3) if total else 0,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else 0,
        "recall": round(tp / (tp + fn), 3) if (tp + fn) else 0,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "sample_size": total,
    }


def test_save_baseline():
    """Save baseline metrics to JSON for CI comparison.

    This test runs the rule-level filter only (no LLM calls) to establish
    a deterministic baseline. The LLM gate adds additional recall on top.
    """
    predictions = _run_gate_dry_run()
    metrics = _compute_metrics(predictions)

    baseline_path = Path(__file__).parent / "memory_gate_baseline.json"
    baseline = {
        **metrics,
        "evaluated_at": "2026-06-09",
        "note": "Rule-level filter only. LLM gate improves recall on uncertain cases.",
    }
    baseline_path.write_text(json.dumps(baseline, indent=2, ensure_ascii=False))

    # Sanity checks
    assert metrics["precision"] >= 0.8, f"Precision too low: {metrics['precision']}"
    assert metrics["recall"] >= 0.5, f"Recall too low: {metrics['recall']} (rules are conservative)"
