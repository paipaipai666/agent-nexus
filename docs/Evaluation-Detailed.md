> **[中文](Evaluation-Detailed.md) | [English](Evaluation-Detailed.en.md)**

# 📈 Evaluation 评估模块（详细版）

## 概述

`evaluation` 模块实现了 AgentNexus 的全面评估系统，包括 RAG 评估、轨迹评估、组件评估、幻觉检测、工具选择评估、连贯性评估等 8 个评估器。

## 评估器架构

```
┌─────────────────────────────────────────────────────────────┐
│                    EvalService                               │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ RAG Eval │  │Trajectory│  │Component │  │Hallucin. │   │
│  │ RAG 评估 │  │ 轨迹评估 │  │ 组件评估 │  │ 幻觉检测 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ToolSelect│  │Coherence │  │Agent Eval│  │Code Bench│   │
│  │工具选择  │  │ 连贯性   │  │ 代理评估 │  │ 代码评估 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 评估器详情

| 评估器 | 文件 | 说明 |
| --- | --- | --- |
| RAG 评估 | `rag/evaluator.py` | 检索质量、相关性、覆盖率 |
| 轨迹评估 | `trajectory.py` | 决策路径质量（确定性规则） |
| 组件评估 | `component.py` | Coder/Researcher/Executor/Analyst 组件 |
| 幻觉检测 | `hallucination.py` | 从答案提取声明并对照上下文验证 |
| 工具选择 | `tool_selection.py` | 工具选择准确率 |
| 连贯性 | `coherence.py` | 多步推理连贯性（独立 Judge 模型） |
| 代理评估 | `agent_eval.py` | 单 Agent 执行质量 |
| 代码评估 | `humaneval.py`, `swebench.py` | HumanEval/SWE-bench 代码质量 |

## 评估任务系统

### 任务分类

| 类别 | 目录 | 说明 |
| --- | --- | --- |
| `coding` | `eval_tasks/coding/` | 代码生成和修复 |
| `conversation` | `eval_tasks/conversation/` | 对话质量 |
| `rag` | `eval_tasks/rag/` | RAG 检索质量 |
| `reasoning` | `eval_tasks/reasoning/` | 推理能力 |
| `regression` | `eval_tasks/regression/` | 回归测试 |
| `tool_use` | `eval_tasks/tool_use/` | 工具使用能力 |

### 任务数据格式

```yaml
id: task_001
category: coding
difficulty: medium
type: single_turn
input: "Write a function to calculate fibonacci numbers"
expected_output: "def fibonacci(n):..."
evaluation_criteria:
  - correctness
  - efficiency
```

## 评估报告

### 报告结构

```json
{
  "eval_id": "eval_20260608",
  "dataset": "coding_v1",
  "metrics": {
    "pass_rate": 0.85,
    "avg_latency_ms": 2500,
    "avg_tokens": 1500
  },
  "results": [...]
}
```

## 模块依赖关系

```
EvalService (services/eval.py)
    ├── RAG Evaluator (rag/evaluator.py)
    │       └── RAG Retriever (rag/retriever.py)
    ├── Trajectory Evaluator (evaluation/trajectory.py)
    │       └── TraceManager (observability/tracer.py)
    ├── Component Evaluator (evaluation/component.py)
    ├── Hallucination Detector (evaluation/hallucination.py)
    │       └── JudgeLLM (core/judge_llm.py)
    ├── Tool Selection Evaluator (evaluation/tool_selection.py)
    ├── Coherence Evaluator (evaluation/coherence.py)
    │       └── JudgeLLM
    ├── Agent Evaluator (evaluation/agent_eval.py)
    └── Code Benchmarks (evaluation/humaneval.py, swebench.py)
```
