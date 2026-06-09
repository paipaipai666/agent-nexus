"""统一 Task 定义模型 — 符合 Anthropic 评估方法论。

定义 EvalTask（单一测试）、GraderConfig（评分器配置）、EvalSuite（评估套件）
作为所有评估流程的标准化输入格式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskCategory(str, Enum):
    """任务能力分类。"""
    CODING = "coding"
    CONVERSATION = "conversation"
    RESEARCH = "research"
    TOOL_USE = "tool_use"
    REASONING = "reasoning"
    RAG = "rag"
    MEMORY = "memory"
    GENERAL = "general"


class TaskDifficulty(str, Enum):
    """任务难度。"""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class EvalType(str, Enum):
    """评估类型 — capability 用于爬坡，regression 用于防回退。"""
    CAPABILITY = "capability"
    REGRESSION = "regression"


class GraderType(str, Enum):
    """评分器类型。"""
    DETERMINISTIC = "deterministic"          # 代码测试、字符串匹配
    LLM_RUBRIC = "llm_rubric"              # LLM-as-judge
    STATIC_ANALYSIS = "static_analysis"      # ruff / mypy / bandit
    STATE_CHECK = "state_check"              # 环境状态验证
    TOOL_CALLS = "tool_calls"               # 工具调用验证
    CODE_EXECUTION = "code_execution"        # 代码执行测试
    TRANSCRIPT = "transcript"                # 转录约束
    HUMAN = "human"                          # 人工评分
    COMPOSITE = "composite"                  # 组合评分


class ScoringMode(str, Enum):
    """组合评分模式。"""
    WEIGHTED = "weighted"    # 加权求和
    BINARY = "binary"        # 全部通过才算通过
    HYBRID = "hybrid"        # required 必须通过 + 加权其余


# ---------------------------------------------------------------------------
# Grader Config
# ---------------------------------------------------------------------------

@dataclass
class GraderConfig:
    """单个评分器的配置。

    Attributes:
        type: 评分器类型 (deterministic / llm_rubric / static_analysis / ...)
        name: 评分器名称 (用于报告展示)
        weight: 权重 (用于加权合成)
        threshold: 通过阈值 (二元判定)
        rubric: LLM rubric prompt 文件名 (llm_rubric 类型)
        assertions: LLM 断言列表 (llm_rubric 类型)
        required_tools: 必须使用的工具列表 [{tool, params}]
        commands: 静态分析命令 (static_analysis 类型)
        expect_state: 期望的环境状态 {table: {field: value}}
        test_files: 测试文件列表 (deterministic / code_execution 类型)
        max_turns: 最大轮次约束 (transcript 类型)
        max_tokens: 最大 token 约束 (transcript 类型)
        required: 是否为必须通过的评分器 (hybrid 模式)
        judge_model: 指定的 judge 模型 (覆盖默认)
        prompt_file: 自定义 prompt 文件名
    """

    type: str
    name: str = ""
    weight: float = 1.0
    threshold: float | None = None
    rubric: str | None = None
    assertions: list[str] | None = None
    required_tools: list[dict[str, Any]] | None = None
    commands: list[str] | None = None
    expect_state: dict[str, Any] | None = None
    test_files: list[str] | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    required: bool = False
    judge_model: str | None = None
    prompt_file: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.type

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，排除 None 值。"""
        result: dict[str, Any] = {"type": self.type}
        for k, v in self.__dict__.items():
            if k == "type" or v is None:
                continue
            result[k] = v
        return result


# ---------------------------------------------------------------------------
# Tracked Metrics
# ---------------------------------------------------------------------------

DEFAULT_TRACKED_METRICS = [
    "n_turns",
    "n_toolcalls",
    "n_total_tokens",
    "latency",
]


# ---------------------------------------------------------------------------
# Task Definition
# ---------------------------------------------------------------------------

@dataclass
class EvalTask:
    """单一评估任务定义 — 文章中的 "task"。

    每个 task 定义了输入、期望行为（通过 graders 验证）、和执行约束。
    两个领域专家应能独立对同一 task 给出相同的 pass/fail 判定。

    Attributes:
        id: 唯一标识符
        description: 人类可读描述
        category: 能力分类
        difficulty: 难度
        eval_type: capability 或 regression
        input: 输入内容 {prompt, context, tools, environment}
        graders: 评分器配置列表
        reference_solution: 参考答案 (证明 task 可解)
        environment: 环境配置 {type, setup_cmd, teardown_cmd}
        tags: 标签列表
        max_turns: 最大轮次
        timeout_sec: 超时秒数
        tracked_metrics: 需要追踪的指标
    """

    id: str
    description: str
    category: str = TaskCategory.GENERAL.value
    difficulty: str = TaskDifficulty.MEDIUM.value
    eval_type: str = EvalType.CAPABILITY.value
    input: dict[str, Any] = field(default_factory=dict)
    graders: list[GraderConfig] = field(default_factory=list)
    reference_solution: str | None = None
    environment: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)
    max_turns: int | None = None
    timeout_sec: int = 120
    tracked_metrics: list[str] = field(default_factory=lambda: list(DEFAULT_TRACKED_METRICS))

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        result: dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "category": self.category,
            "difficulty": self.difficulty,
            "eval_type": self.eval_type,
            "input": self.input,
            "graders": [g.to_dict() for g in self.graders],
            "tags": self.tags,
            "timeout_sec": self.timeout_sec,
            "tracked_metrics": self.tracked_metrics,
        }
        if self.reference_solution is not None:
            result["reference_solution"] = self.reference_solution
        if self.environment is not None:
            result["environment"] = self.environment
        if self.max_turns is not None:
            result["max_turns"] = self.max_turns
        return result

    def get_required_graders(self) -> list[GraderConfig]:
        """返回必须通过的评分器。"""
        return [g for g in self.graders if g.required]

    def get_weighted_graders(self) -> list[GraderConfig]:
        """返回有权重的评分器。"""
        return [g for g in self.graders if g.weight > 0]

    def validate(self) -> list[str]:
        """验证 task 定义的完整性。返回错误列表（空 = 有效）。"""
        errors: list[str] = []
        if not self.id:
            errors.append("Task ID is required")
        if not self.description:
            errors.append(f"Task {self.id}: description is required")
        if not self.input.get("prompt"):
            errors.append(f"Task {self.id}: input.prompt is required")
        if not self.graders:
            errors.append(f"Task {self.id}: at least one grader is required")
        for i, g in enumerate(self.graders):
            if not g.type:
                errors.append(f"Task {self.id}: grader[{i}] type is required")
        if self.timeout_sec <= 0:
            errors.append(f"Task {self.id}: timeout_sec must be positive")
        return errors


# ---------------------------------------------------------------------------
# Suite Definition
# ---------------------------------------------------------------------------

@dataclass
class SuiteThresholds:
    """套件级别的阈值配置。

    Attributes:
        min_pass_rate: 最低通过率 (capability 从低开始, regression ~100%)
        max_regression_delta: 相对于 baseline 的最大允许下降
        min_pass_at_k: pass@k 最低要求 {k: threshold}
        max_saturation: 饱和度上限 (超过则建议升级难度)
    """

    min_pass_rate: float = 0.0
    max_regression_delta: float = 0.05
    min_pass_at_k: dict[int, float] | None = None
    max_saturation: float = 0.95

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "min_pass_rate": self.min_pass_rate,
            "max_regression_delta": self.max_regression_delta,
            "max_saturation": self.max_saturation,
        }
        if self.min_pass_at_k:
            result["min_pass_at_k"] = {str(k): v for k, v in self.min_pass_at_k.items()}
        return result


@dataclass
class EvalSuite:
    """评估套件 — 一组共享目标的 task。

    Attributes:
        name: 套件名称
        eval_type: capability 或 regression
        description: 套件描述
        tasks: 任务列表 (可延迟加载)
        task_ids: 任务 ID 列表 (从 YAML 文件引用)
        thresholds: 套件阈值
        tags: 套件标签
        version: 版本号
    """

    name: str
    eval_type: str = EvalType.CAPABILITY.value
    description: str = ""
    tasks: list[EvalTask] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    thresholds: SuiteThresholds = field(default_factory=SuiteThresholds)
    tags: list[str] = field(default_factory=list)
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "eval_type": self.eval_type,
            "description": self.description,
            "tags": self.tags,
            "version": self.version,
            "thresholds": self.thresholds.to_dict(),
        }
        if self.tasks:
            result["tasks"] = [t.to_dict() for t in self.tasks]
        elif self.task_ids:
            result["task_ids"] = self.task_ids
        return result


# ---------------------------------------------------------------------------
# YAML Loader
# ---------------------------------------------------------------------------

def _parse_grader(data: dict[str, Any]) -> GraderConfig:
    """从 dict 解析 GraderConfig。"""
    return GraderConfig(
        type=data["type"],
        name=data.get("name", ""),
        weight=data.get("weight", 1.0),
        threshold=data.get("threshold"),
        rubric=data.get("rubric"),
        assertions=data.get("assertions"),
        required_tools=data.get("required_tools"),
        commands=data.get("commands"),
        expect_state=data.get("expect_state"),
        test_files=data.get("test_files"),
        max_turns=data.get("max_turns"),
        max_tokens=data.get("max_tokens"),
        required=data.get("required", False),
        judge_model=data.get("judge_model"),
        prompt_file=data.get("prompt_file"),
    )


def _parse_task(data: dict[str, Any]) -> EvalTask:
    """从 dict 解析 EvalTask。"""
    graders = [_parse_grader(g) for g in data.get("graders", [])]
    return EvalTask(
        id=data["id"],
        description=data.get("description", ""),
        category=data.get("category", TaskCategory.GENERAL.value),
        difficulty=data.get("difficulty", TaskDifficulty.MEDIUM.value),
        eval_type=data.get("eval_type", EvalType.CAPABILITY.value),
        input=data.get("input", {}),
        graders=graders,
        reference_solution=data.get("reference_solution"),
        environment=data.get("environment"),
        tags=data.get("tags", []),
        max_turns=data.get("max_turns"),
        timeout_sec=data.get("timeout_sec", 120),
        tracked_metrics=data.get("tracked_metrics", list(DEFAULT_TRACKED_METRICS)),
    )


def _parse_suite(data: dict[str, Any]) -> EvalSuite:
    """从 dict 解析 EvalSuite。"""
    thresh_data = data.get("thresholds", {})
    thresholds = SuiteThresholds(
        min_pass_rate=thresh_data.get("min_pass_rate", 0.0),
        max_regression_delta=thresh_data.get("max_regression_delta", 0.05),
        min_pass_at_k={int(k): v for k, v in thresh_data.get("min_pass_at_k", {}).items()} if thresh_data.get("min_pass_at_k") else None,
        max_saturation=thresh_data.get("max_saturation", 0.95),
    )
    return EvalSuite(
        name=data["name"],
        eval_type=data.get("eval_type", EvalType.CAPABILITY.value),
        description=data.get("description", ""),
        task_ids=data.get("task_ids", []),
        thresholds=thresholds,
        tags=data.get("tags", []),
        version=data.get("version", "1.0"),
    )


def load_task_from_yaml(path: str | Path) -> EvalTask:
    """从 YAML 文件加载单个 task。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _parse_task(data)


def load_suite_from_yaml(path: str | Path) -> EvalSuite:
    """从 YAML 文件加载 suite 定义。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _parse_suite(data)


def load_tasks_from_dir(directory: str | Path) -> list[EvalTask]:
    """从目录中加载所有 YAML task 文件（递归）。"""
    tasks: list[EvalTask] = []
    dir_path = Path(directory)
    if not dir_path.exists():
        return tasks
    for yaml_file in sorted(dir_path.rglob("*.yaml")):
        if yaml_file.name.startswith("_"):
            continue  # 跳过 _suite.yaml 等元文件
        try:
            task = load_task_from_yaml(yaml_file)
            tasks.append(task)
        except Exception:
            continue  # 跳过无效文件
    return tasks
