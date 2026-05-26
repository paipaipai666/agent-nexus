> **[中文](Evaluation.md) | [English](Evaluation.en.md)**

# 📊 评估体系

评估器全部从 JSONL Trace 读取数据（除 Coherence 外不依赖 LLM）。

## Agent 层

| 评估器 | 方法 | 指标 | 命令 |
|--------|------|------|------|
| **Agent** | Trace 提取成功率/延迟/截断率 | answer>85%, tool>80%, trunc<10% | `eval agent` |
| **Trajectory** | 5 项规则检查 | score≥6/10 | `eval trajectory` |
| **Component** | 按 Agent 角色检查 | score≥6.0 | `eval component` |
| **Hallucination** | 句子分割+关键词重叠 | rate<2% | `eval hallucination` |
| **Coherence** | 独立 Judge 模型评分 | score≥8.5/10 | `eval coherence` |
| **Tool Selection** | 关键词匹配 vs 实际 | accuracy≥92% | `eval tool-selection` |
| **HumanEval** | 隔离子进程运行测试 | pass@1 | `eval humaneval` |
| **SWE-bench** | 补丁应用测试 | resolve_rate | `eval swe-bench` |

## RAG 层

`eval run` 测试 12 种配置（3 分块策略 × 2 块大小 × 2 检索模式）：

| 指标 | 含义 |
|------|------|
| Faithfulness | 答案是否忠实于上下文 |
| AnsRelevancy | 答案与问题相关性 |
| AnsCorrectness | 答案与标准答案一致性 |
| Precision | 检索精确度 |
| Recall | 检索召回率 |
| ContextRelevancy | 上下文与问题相关性 |
| HitRate | 命中率 @k |
| MRR | 平均倒数排名 |

## 生产层

| 命令 | 说明 |
|------|------|
| `eval ci [-d N]` | CI 模式，不达标 exit(1) |
| `eval calibrate` | Judge 校准，计算 Spearman/Pearson 一致率 |

## 内置数据集

`tests/evals/` 包含 9 个 JSONL 数据集：`agent_eval`, `tool_selection`, `hallucination`, `coherence`, `trajectory`, `humaneval`, `swebench`, `code_generation`, `code_retrieval`。
