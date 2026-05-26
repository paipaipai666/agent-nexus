> **[中文](Evaluation.md) | [English](Evaluation.en.md)**

# 📊 Evaluation

All evaluators read from JSONL Traces (except Coherence, which does not depend on LLM).

## Agent Layer

| Evaluator | Method | Target | Command |
|--------|------|------|------|
| **Agent** | Trace extraction: success rate/latency/truncation rate | answer>85%, tool>80%, trunc<10% | `eval agent` |
| **Trajectory** | 5 rule checks | score≥6/10 | `eval trajectory` |
| **Component** | Check by Agent role | score≥6.0 | `eval component` |
| **Hallucination** | Sentence split + keyword overlap | rate<2% | `eval hallucination` |
| **Coherence** | Independent Judge model scoring | score≥8.5/10 | `eval coherence` |
| **Tool Selection** | Keyword match vs actual | accuracy≥92% | `eval tool-selection` |
| **HumanEval** | Isolated subprocess test run | pass@1 | `eval humaneval` |
| **SWE-bench** | Patch application test | resolve_rate | `eval swe-bench` |

## RAG Layer

`eval run` tests 12 configurations (3 chunk strategies × 2 chunk sizes × 2 retrieval modes):

| Metric | Description |
|------|------|
| Faithfulness | Whether answer is faithful to context |
| AnsRelevancy | Answer relevance to question |
| AnsCorrectness | Answer consistency with ground truth |
| Precision | Retrieval precision |
| Recall | Retrieval recall |
| ContextRelevancy | Context relevance to question |
| HitRate | Hit rate @k |
| MRR | Mean reciprocal rank |

## Production Layer

| Command | Description |
|------|------|
| `eval ci [-d N]` | CI mode, exit(1) on failure |
| `eval calibrate` | Judge calibration, compute Spearman/Pearson agreement |

## Built-in Datasets

`tests/evals/` contains 9 JSONL datasets: `agent_eval`, `tool_selection`, `hallucination`, `coherence`, `trajectory`, `humaneval`, `swebench`, `code_generation`, `code_retrieval`.
