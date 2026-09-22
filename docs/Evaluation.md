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

## 公开基准（Benchmark Track）

与内置 60 题轨道（分块知识库 + Judge LLM）并行，`eval benchmark` 提供第二条**标准 IR 轨道**：公开语料 + qrels + doc-id 精确匹配，零 LLM 调用，用于与公开 leaderboard 对拍和检索器回归检测。

| 命令 | 说明 |
|------|------|
| `eval benchmark list` | 列出基准套件与数据集 |
| `eval benchmark run -s beir-lite` | 运行 BEIR-lite（NFCorpus / SciFact / ArguAna） |
| `eval benchmark run --mode hybrid` | RRF(dense+BM25) 项目自有口径 |
| `eval benchmark run -e <模型>` | 临时切换 embedding 模型 |
| `eval benchmark run --ci` | 报告写入 `traces/evals/benchmark-*.json` |
| `eval benchmark run-rgb --lang zh --task noise` | RGB 端到端（检索+生成+judge），`-t rejection` 测负样本拒答 |
| `eval benchmark run-rgb -m agnes/agnes-3.0-flash -n 50` | 指定生成模型、50 题冒烟 |
| `eval benchmark run-rgb -j <报告.json>` | 跳过生成，重判报告里的存盘答案（错峰跑 / 换 judge 对拍，输出新旧 judge 一致率） |
| `eval benchmark run-rgb -g` | 只生成存盘、judge 留待 `-j` 重判（如等 deepseek 低谷半价时段再判） |
| `eval benchmark run -e BAAI/bge-small-en-v1.5` | 按任务切 embedding（英文任务建议英文模型） |
| `eval benchmark run -s multihop` | MultiHop-RAG 多跳检索（609 篇语料/2556 查询，检索真正参与，dense vs hybrid 量化生产栈收益） |
| `eval benchmark gate -s multihop --min 0.66` | CI 门禁：最新报告指标低于下限 exit 1（自动排除手工基线文件） |

API:`GET /api/eval/benchmark/suites` / `POST /api/eval/benchmark/run` / `GET /api/eval/benchmark/reports`。

口径说明：

- `dense` 模式 = BEIR 官方协议（文档级建索引、单一稠密检索、NDCG@10 主指标），可与 leaderboard.mteb.org 对拍
- `hybrid` 模式 = 本项目 RRF 栈，并列展示，不混入官方口径
- 默认 embedding 为中文模型；对拍英文基准需 `-e BAAI/bge-small-en-v1.5`
- 数据缓存在 `~/.cache/agentnexus/benchmarks/`，支持 `--offline` 纯本地
- RGB 协议：每题 5 篇文档（positive+negative 按噪声率采样，官方 passage_num=5），官方指令模板生成；noise 任务用 LLM judge 判正确性（≥0.5 算对），rejection 任务用官方关键词规则判拒答，**无需 judge LLM**
- LLM 调用走单实例限速器（`--rpm`，默认 18），429 指数退避；免费档（Agnes RPM 20）建议不高于 18

## 生产层

| 命令 | 说明 |
|------|------|
| `eval ci [-d N]` | CI 模式，不达标 exit(1) |
| `eval calibrate` | Judge 校准，计算 Spearman/Pearson 一致率 |

## 内置数据集

`tests/evals/` 包含 9 个 JSONL 数据集：`agent_eval`, `tool_selection`, `hallucination`, `coherence`, `trajectory`, `humaneval`, `swebench`, `code_generation`, `code_retrieval`。
