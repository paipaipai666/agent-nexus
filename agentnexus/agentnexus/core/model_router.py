"""Model routing — route tasks to appropriate models by complexity + budget state."""

from __future__ import annotations


def classify_task(task: str) -> str:
    """Heuristic task complexity classification: 'simple', 'standard', or 'complex'."""
    task_lower = task.lower()
    if any(kw in task_lower for kw in ("计算", "代码", "分析", "报告", "可视化", "图表",
                                         "analyze", "code", "report", "visualize")):
        return "complex"
    if any(kw in task_lower for kw in ("搜索", "查询", "是什么", "解释", "search", "what", "explain")):
        return "standard"
    if len(task) < 30:
        return "simple"
    return "standard"


_MODEL_TIER = {
    "strong": "deepseek/deepseek-v4-pro",
    "standard": "deepseek/deepseek-v4-flash",
    "fast": "deepseek/deepseek-v4-flash",
}


def route_model(task: str, budget_state: str = "green") -> str:
    """Pick the model ID based on task complexity and budget state.

    GREEN:  strong model for complex, standard for others
    YELLOW: standard for all
    RED:    fast for all
    BREAK:  fast (will be terminated anyway)
    """
    complexity = classify_task(task)
    if budget_state == "green":
        return _MODEL_TIER["strong"] if complexity == "complex" else _MODEL_TIER["standard"]
    elif budget_state in ("yellow", "red", "break"):
        return _MODEL_TIER["fast"]
    return _MODEL_TIER["standard"]
