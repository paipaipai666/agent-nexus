"""Grader 接口体系 — 将现有评估器包装为统一的评分器接口。

对应 Anthropic 方法论中的三类 grader:
  - DeterministicGrader: code-based (快速、客观、可复现)
  - ModelGrader: model-based (灵活、有 nuance)
  - HumanGrader: human (金标准)
  - CompositeGrader: 组合多个 grader (weighted / binary / hybrid)
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from agentnexus.evaluation.task import GraderConfig, ScoringMode
from agentnexus.evaluation.trial import GraderScore


# ---------------------------------------------------------------------------
# Base Grader
# ---------------------------------------------------------------------------

class BaseGrader(ABC):
    """评分器基类。"""

    name: str = "base"
    grader_type: str = "deterministic"

    @abstractmethod
    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        """评分。

        Args:
            task_input: 任务输入 (prompt, context, tools 等)
            transcript: 完整执行 transcript
            outcome: 最终环境状态
            config: 评分器配置

        Returns:
            GraderScore
        """
        ...


# ---------------------------------------------------------------------------
# Deterministic Graders
# ---------------------------------------------------------------------------

class TranscriptGrader(BaseGrader):
    """转录约束评分器 — 检查轮次、token 等约束。"""

    name = "transcript"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()
        issues: list[str] = []
        score = 1.0

        llm_calls = [s for s in transcript if s.get("name") == "llm"]
        tool_calls = [s for s in transcript if s.get("name") == "tool"]

        n_turns = len(llm_calls)
        n_toolcalls = len(tool_calls)
        n_total_tokens = sum(
            s.get("metadata", {}).get("input_tokens", 0) +
            s.get("metadata", {}).get("output_tokens", 0)
            for s in llm_calls
        )

        if config.max_turns and n_turns > config.max_turns:
            issues.append(f"Exceeded max turns: {n_turns} > {config.max_turns}")
            score -= 0.5

        if config.max_tokens and n_total_tokens > config.max_tokens:
            issues.append(f"Exceeded max tokens: {n_total_tokens} > {config.max_tokens}")
            score -= 0.5

        score = max(0.0, score)
        elapsed = (time.monotonic() - start) * 1000

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=score >= (config.threshold or 0.5),
            details="; ".join(issues) if issues else "Within constraints",
            weight=config.weight,
            duration_ms=elapsed,
        )


class ToolCallsGrader(BaseGrader):
    """工具调用验证 — 检查是否使用了必需的工具。"""

    name = "tool_calls"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()
        tool_spans = [s for s in transcript if s.get("name") == "tool"]

        if not config.required_tools:
            return GraderScore(
                name=config.name or self.name,
                grader_type=self.grader_type,
                score=1.0,
                passed=True,
                details="No required tools specified",
                weight=config.weight,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        matched = 0
        details_list: list[str] = []

        for required in config.required_tools:
            req_tool = required.get("tool", "")
            req_params = required.get("params", {})
            found = False

            for span in tool_spans:
                input_data = span.get("input", {})
                span_tool = input_data.get("tool_name", "")
                if span_tool != req_tool:
                    continue
                if req_params:
                    span_params = input_data.get("params", {})
                    if all(span_params.get(k) == v for k, v in req_params.items()):
                        found = True
                        break
                else:
                    found = True
                    break

            if found:
                matched += 1
            else:
                details_list.append(f"Missing required tool: {req_tool}")

        total = len(config.required_tools)
        score = matched / total if total > 0 else 1.0

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=score >= (config.threshold or 1.0),
            details="; ".join(details_list) if details_list else f"All {total} required tools used",
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


class StateCheckGrader(BaseGrader):
    """环境状态验证 — 检查 outcome 是否符合期望。"""

    name = "state_check"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()

        if not config.expect_state:
            return GraderScore(
                name=config.name or self.name,
                grader_type=self.grader_type,
                score=1.0,
                passed=True,
                details="No expected state specified",
                weight=config.weight,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        issues: list[str] = []
        matched = 0
        total = 0

        for key, expected in config.expect_state.items():
            actual = outcome.get(key)
            if isinstance(expected, dict) and isinstance(actual, dict):
                for field, val in expected.items():
                    total += 1
                    if actual.get(field) == val:
                        matched += 1
                    else:
                        issues.append(f"{key}.{field}: expected {val}, got {actual.get(field)}")
            else:
                total += 1
                if actual == expected:
                    matched += 1
                else:
                    issues.append(f"{key}: expected {expected}, got {actual}")

        score = matched / total if total > 0 else 1.0

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=score >= (config.threshold or 1.0),
            details="; ".join(issues) if issues else "All state checks passed",
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


class StaticAnalysisGrader(BaseGrader):
    """静态分析评分器 — 运行 ruff/mypy/bandit 等工具。"""

    name = "static_analysis"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        import subprocess

        start = time.monotonic()

        if not config.commands:
            return GraderScore(
                name=config.name or self.name,
                grader_type=self.grader_type,
                score=1.0,
                passed=True,
                details="No static analysis commands specified",
                weight=config.weight,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        issues: list[str] = []
        all_passed = True

        for cmd in config.commands:
            try:
                result = subprocess.run(
                    cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=task_input.get("workdir"),
                )
                if result.returncode != 0:
                    all_passed = False
                    stderr = result.stderr.strip()[:200]
                    issues.append(f"{cmd} failed: {stderr}")
            except FileNotFoundError:
                issues.append(f"{cmd} not found")
                all_passed = False
            except subprocess.TimeoutExpired:
                issues.append(f"{cmd} timed out")
                all_passed = False

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=1.0 if all_passed else 0.0,
            passed=all_passed,
            details="; ".join(issues) if issues else "All static analysis passed",
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


# ---------------------------------------------------------------------------
# Model-Based Grader (LLM-as-Judge)
# ---------------------------------------------------------------------------

class LLMRubricGrader(BaseGrader):
    """LLM Rubric 评分器 — 使用独立 Judge LLM 评分。

    对应 Anthropic 方法论中的 model-based grader。
    使用结构化 rubric + assertions，支持 "Unknown" 返回。
    """

    name = "llm_rubric"
    grader_type = "model_based"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()

        # 提取答案
        answer = ""
        for span in reversed(transcript):
            if span.get("name") == "final_answer":
                answer = span.get("output", {}).get("answer", "")
                break

        if not answer:
            return GraderScore(
                name=config.name or self.name,
                grader_type=self.grader_type,
                score=0.0,
                passed=False,
                details="No final answer found in transcript",
                weight=config.weight,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # 构建 judge prompt
        prompt = self._build_prompt(task_input, answer, transcript, config)

        # 调用 judge LLM
        try:
            from agentnexus.core.judge_llm import get_judge_llm

            judge = get_judge_llm()
            response = judge.think([{"role": "user", "content": prompt}])
            score, details = self._parse_score(response, config)
        except Exception as e:
            return GraderScore(
                name=config.name or self.name,
                grader_type=self.grader_type,
                score=0.0,
                passed=False,
                details=f"Judge LLM error: {e}",
                weight=config.weight,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=score >= (config.threshold or 0.7),
            details=details,
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    def _build_prompt(
        self,
        task_input: dict[str, Any],
        answer: str,
        transcript: list[dict[str, Any]],
        config: GraderConfig,
    ) -> str:
        """构建 judge prompt。"""
        # 如果有自定义 prompt 文件，使用它
        if config.prompt_file:
            try:
                from agentnexus.prompts import format_prompt
                return format_prompt(
                    config.prompt_file,
                    question=task_input.get("prompt", ""),
                    answer=answer,
                    context=task_input.get("context", ""),
                )
            except Exception:
                pass

        # 默认 rubric prompt
        parts = [
            "你是一个评估专家。请根据以下标准对 AI agent 的回答进行评分。",
            f"\n## 任务\n{task_input.get('prompt', '')}",
            f"\n## Agent 回答\n{answer}",
        ]

        if config.rubric:
            parts.append(f"\n## 评分标准\n{config.rubric}")

        if config.assertions:
            parts.append("\n## 需要验证的断言")
            for i, assertion in enumerate(config.assertions, 1):
                parts.append(f"{i}. {assertion}")

        parts.extend([
            "\n## 输出要求",
            '请返回 JSON 格式: {"score": 0.0-1.0, "details": "评分说明", "passed": true/false}',
            '如果信息不足无法评分，返回: {"score": 0.0, "details": "Unknown: 信息不足", "passed": false}',
        ])

        return "\n".join(parts)

    def _parse_score(self, response: str, config: GraderConfig) -> tuple[float, str]:
        """解析 judge 响应。"""
        import json

        # 尝试 JSON 解析
        try:
            # 提取 JSON 块
            json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                score = float(data.get("score", 0.0))
                details = data.get("details", "")
                return score, details
        except (json.JSONDecodeError, ValueError):
            pass

        # 回退：提取数字
        score_match = re.search(r'(?:score|分数)[：:]\s*([\d.]+)', response)
        if score_match:
            try:
                score = min(1.0, max(0.0, float(score_match.group(1))))
                return score, response[:200]
            except ValueError:
                pass

        return 0.0, f"Failed to parse judge response: {response[:200]}"


# ---------------------------------------------------------------------------
# Composite Grader
# ---------------------------------------------------------------------------

class CompositeGrader(BaseGrader):
    """组合评分器 — 组合多个 grader 的分数。

    支持三种模式:
      - weighted: 加权求和
      - binary: 全部通过才算通过
      - hybrid: required 必须通过 + 加权其余
    """

    name = "composite"
    grader_type = "composite"

    def __init__(self, graders: list[tuple[BaseGrader, GraderConfig]], mode: str = ScoringMode.WEIGHTED.value):
        self._graders = graders
        self._mode = mode

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig | None = None,
    ) -> GraderScore:
        start = time.monotonic()

        scores: list[GraderScore] = []
        for grader, grader_config in self._graders:
            score = grader.grade(task_input, transcript, outcome, grader_config)
            scores.append(score)

        if self._mode == ScoringMode.BINARY.value:
            all_passed = all(s.passed for s in scores)
            return GraderScore(
                name=self.name,
                grader_type=self.grader_type,
                score=1.0 if all_passed else 0.0,
                passed=all_passed,
                details=f"Binary: {sum(1 for s in scores if s.passed)}/{len(scores)} passed",
                weight=1.0,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        if self._mode == ScoringMode.HYBRID.value:
            # 检查 required grader
            required_scores = [s for s in scores if s.weight < 0 or not s.passed]
            # 实际上我们需要从 config 判断 required
            # 简化：检查所有 grader，required 的必须通过
            required_passed = all(
                s.passed for s, (_, gc) in zip(scores, self._graders) if gc.required
            )

            # 加权计算非 required 的分数
            weighted_scores = [
                (s.score, s.weight) for s, (_, gc) in zip(scores, self._graders) if not gc.required
            ]
            if weighted_scores:
                total_weight = sum(w for _, w in weighted_scores)
                weighted_avg = sum(s * w for s, w in weighted_scores) / total_weight if total_weight > 0 else 0.0
            else:
                weighted_avg = 1.0

            final_passed = required_passed and weighted_avg >= 0.5

            return GraderScore(
                name=self.name,
                grader_type=self.grader_type,
                score=weighted_avg,
                passed=final_passed,
                details=f"Hybrid: required={'pass' if required_passed else 'fail'}, weighted={weighted_avg:.2f}",
                weight=1.0,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Default: weighted
        total_weight = sum(s.weight for s in scores)
        if total_weight > 0:
            weighted_score = sum(s.score * s.weight for s in scores) / total_weight
        else:
            weighted_score = 0.0

        all_required_passed = all(
            s.passed for s, (_, gc) in zip(scores, self._graders) if gc.required
        )

        return GraderScore(
            name=self.name,
            grader_type=self.grader_type,
            score=weighted_score,
            passed=weighted_score >= 0.5 and all_required_passed,
            details=f"Weighted: {weighted_score:.2f} ({len(scores)} graders)",
            weight=1.0,
            duration_ms=(time.monotonic() - start) * 1000,
        )


# ---------------------------------------------------------------------------
# Grader Registry — 将现有评估器包装为标准 Grader
# ---------------------------------------------------------------------------

class TrajectoryGraderAdapter(BaseGrader):
    """将现有 trajectory.py 包装为标准 grader。"""

    name = "trajectory"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()
        try:
            from agentnexus.evaluation.trajectory import TrajectoryEvaluator
            evaluator = TrajectoryEvaluator()
            report = evaluator.evaluate_transcript(transcript)
            score = report.score / 10.0  # 归一化到 0-1
            passed = report.score >= (config.threshold or 6.0)
            issues = [f"{check}: {count}" for check, count in report.issues.items() if count > 0]
        except Exception as e:
            score = 0.0
            passed = False
            issues = [f"Evaluation error: {e}"]

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=passed,
            details="; ".join(issues) if issues else "No trajectory issues",
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


class HallucinationGraderAdapter(BaseGrader):
    """将现有 hallucination.py 包装为标准 grader。"""

    name = "hallucination"
    grader_type = "deterministic"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()
        try:
            from agentnexus.evaluation.hallucination import HallucinationDetector
            detector = HallucinationDetector()
            report = detector.detect(transcript)
            # 幻觉率越低越好，score = 1 - hallucination_rate
            score = max(0.0, 1.0 - report.hallucination_rate)
            threshold = config.threshold or 0.98  # 默认 < 2% 幻觉率
            passed = score >= threshold
            details = f"Hallucination rate: {report.hallucination_rate:.1%}"
        except Exception as e:
            score = 0.0
            passed = False
            details = f"Evaluation error: {e}"

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=passed,
            details=details,
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


class CoherenceGraderAdapter(BaseGrader):
    """将现有 coherence.py 包装为标准 grader。"""

    name = "coherence"
    grader_type = "model_based"

    def grade(
        self,
        task_input: dict[str, Any],
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        config: GraderConfig,
    ) -> GraderScore:
        start = time.monotonic()
        try:
            from agentnexus.evaluation.coherence import CoherenceEvaluator
            evaluator = CoherenceEvaluator()
            report = evaluator.evaluate(transcript)
            score = report.coherence_score / 10.0  # 归一化到 0-1
            threshold = (config.threshold or 8.5) / 10.0
            passed = score >= threshold
            details = f"Coherence: {report.coherence_score:.1f}/10"
        except Exception as e:
            score = 0.0
            passed = False
            details = f"Evaluation error: {e}"

        return GraderScore(
            name=config.name or self.name,
            grader_type=self.grader_type,
            score=score,
            passed=passed,
            details=details,
            weight=config.weight,
            duration_ms=(time.monotonic() - start) * 1000,
        )


# ---------------------------------------------------------------------------
# Grader Factory
# ---------------------------------------------------------------------------

# 内置 grader 类型映射
_BUILTIN_GRADERS: dict[str, type[BaseGrader]] = {
    "transcript": TranscriptGrader,
    "tool_calls": ToolCallsGrader,
    "state_check": StateCheckGrader,
    "static_analysis": StaticAnalysisGrader,
    "llm_rubric": LLMRubricGrader,
    "trajectory": TrajectoryGraderAdapter,
    "hallucination": HallucinationGraderAdapter,
    "coherence": CoherenceGraderAdapter,
}


def create_grader(config: GraderConfig) -> BaseGrader:
    """根据配置创建 grader 实例。"""
    grader_cls = _BUILTIN_GRADERS.get(config.type)
    if grader_cls is None:
        raise ValueError(f"Unknown grader type: {config.type}. Available: {list(_BUILTIN_GRADERS.keys())}")
    return grader_cls()


def create_composite_grader(configs: list[GraderConfig], mode: str = ScoringMode.WEIGHTED.value) -> CompositeGrader:
    """创建组合 grader。"""
    graders = [(create_grader(c), c) for c in configs]
    return CompositeGrader(graders, mode=mode)
