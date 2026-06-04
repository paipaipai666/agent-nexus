"""AgentNexus 评估框架 — 符合 Anthropic 评估方法论。

核心组件:
  - task: 统一 Task/Suite 定义模型
  - trial: Trial 运行结果模型
  - graders: Grader 接口体系 (deterministic / model_based / human / composite)
  - harness: 端到端评估 Harness
  - statistics: pass@k / pass^k / bootstrap CI
  - baseline: Baseline 管理与回归检测
  - dataset: 数据集管理
"""

from agentnexus.evaluation.task import (
    EvalTask,
    EvalSuite,
    GraderConfig,
    SuiteThresholds,
    TaskCategory,
    TaskDifficulty,
    EvalType,
    GraderType,
    ScoringMode,
)
from agentnexus.evaluation.trial import (
    TrialResult,
    TaskReport,
    GraderScore,
    AggregateMetrics,
)
from agentnexus.evaluation.graders import (
    BaseGrader,
    CompositeGrader,
    LLMRubricGrader,
    TranscriptGrader,
    ToolCallsGrader,
    StateCheckGrader,
    StaticAnalysisGrader,
    create_grader,
    create_composite_grader,
)
from agentnexus.evaluation.harness import (
    EvalHarness,
    HarnessConfig,
    SuiteReport,
)
from agentnexus.evaluation.statistics import (
    pass_at_k,
    pass_hat_k,
    bootstrap_ci,
    compute_pass_metrics,
    compute_trial_consistency,
    compute_saturation_score,
)
from agentnexus.evaluation.baseline import (
    BaselineManager,
    RegressionReport,
)
from agentnexus.evaluation.dataset import (
    EvalDataset,
    migrate_jsonl_to_yaml,
)

__all__ = [
    # Task
    "EvalTask", "EvalSuite", "GraderConfig", "SuiteThresholds",
    "TaskCategory", "TaskDifficulty", "EvalType", "GraderType", "ScoringMode",
    # Trial
    "TrialResult", "TaskReport", "GraderScore", "AggregateMetrics",
    # Graders
    "BaseGrader", "CompositeGrader", "LLMRubricGrader",
    "TranscriptGrader", "ToolCallsGrader", "StateCheckGrader", "StaticAnalysisGrader",
    "create_grader", "create_composite_grader",
    # Harness
    "EvalHarness", "HarnessConfig", "SuiteReport",
    # Statistics
    "pass_at_k", "pass_hat_k", "bootstrap_ci",
    "compute_pass_metrics", "compute_trial_consistency", "compute_saturation_score",
    # Baseline
    "BaselineManager", "RegressionReport",
    # Dataset
    "EvalDataset", "migrate_jsonl_to_yaml",
]
