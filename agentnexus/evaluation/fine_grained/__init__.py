"""精细化评测 — 《AI Agent 应用精细化评测》框架的 AgentNexus 实现。

按架构同构拆模块：感知（技能路由）/ 规划（工具决策）/ 记忆（探针套件）/
工具（参数映射）/ 端到端（任务完成+指令遵循+忠实性+异常输入+多轮）。
报告数据优先：每个指标给出具体数值与样本量，不用 PASS/FAIL 当结论。
"""

from agentnexus.evaluation.fine_grained.cases import (
    DATASET_PRIMARY_METRIC,
    SCOPE_DATASETS,
    EvalCase,
    cases_for_scope,
)
from agentnexus.evaluation.fine_grained.report import (
    CaseOutcome,
    FineGrainedReport,
    RunMetrics,
)
from agentnexus.evaluation.fine_grained.runner import FineGrainedRunner

__all__ = [
    "EvalCase", "cases_for_scope", "SCOPE_DATASETS", "DATASET_PRIMARY_METRIC",
    "CaseOutcome", "FineGrainedReport", "RunMetrics", "FineGrainedRunner",
]
