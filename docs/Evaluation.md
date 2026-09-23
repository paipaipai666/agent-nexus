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

## 记忆层（Memory Track）

| 命令 | 说明 |
|------|------|
| `eval memory [--judge]` | 确定性探针（`agentnexus/evaluation/memory_eval.py`）：LTM/STM/项目记忆的召回、保鲜、遗忘、隔离、写入完整性等 10 个维度，离线零 LLM；`--judge` 追加 LLM 质量探针 |
| `eval memory-bench list` | 列出内置会话记忆基准套件（3 段对话 × 5 题） |
| `eval memory-bench run -b nexus` | 端到端会话记忆 QA：回放对话 → 逐题回答 → char-F1 打分（可选 `--judge` 独立 judge 判分），报告写 `traces/evals/memory-bench-*.json` |
| `eval memory-bench run -b naive` | 全量上下文基线（无记忆，全部历史塞进 prompt），作为 nexus 后端的参照点 |
| `eval memory-bench run -d suite.jsonl` | 自定义套件（LOCOMO/LongMemEval 式 JSONL），`--min-f1` 可做 CI 门禁 |
| `eval memory-bench convert locomo -o suite.jsonl` | 下载并转换公开榜 LoCoMo10（CC BY-NC 4.0，限研究/内部评测） |
| `eval memory-bench convert longmemeval-s -o suite.jsonl` | 下载并转换 LongMemEval-S（500 题，ICLR 2025） |

数据格式：每行一段对话 `{"id", "turns": [{"role","content"}], "questions": [{"id","question","answer","qa_type"}]}`，`qa_type ∈ single_hop / multi_session / knowledge_update / temporal / distractor_filter / abstention / open_domain`。

口径说明：

- `nexus` 后端走生产写入路径（`append` → `conclude` 准入+提取 → LTM），每段对话跑在隔离的 MemorySandbox（临时 AGENTNEXUS_HOME + 真实 SQLite/Chroma）里
- 主指标 char-F1（标点/大小写不敏感的字多重集合 F1，中文公平）；abstention 题 gold 为「无法确定」
- **检索级指标（与模型无关）**：`retrieval_hit` = gold 是否出现在后端实际注入的记忆上下文中（gold 覆盖检查，LongMemEval answerability 口径）。abstention/open_domain 不适用记 None。这是跨底座模型/跨厂商对拍的公平口径——QA 分 = 记忆 × 生成器，retrieval 分只测记忆层
- `retrieval_hit` 的下钻：✓/✗ 逐题展示（Ctx 列）。写入侧失败（准入拒绝导致 LTM 没有该事实）与读取侧失败（检索没召回）在 ✗ 上表现相同，区分需 evidence 溯源——LoCoMo `evidence` dia_id 已透传进套件，等 LTM 写入带 provenance 后可算真正的 evidence Recall@k
- `temporal` / `knowledge_update` 覆盖短期状态跟踪与事实取代，对应调研结论里自有评测缺口的 WorkMemEval/StateMemBench 维度
- 与公开榜的关系：`convert` 子命令负责下载/缓存官方数据（hf-mirror，缓存于 `~/.cache/agentnexus/benchmarks/memory/`）并转成套件 JSONL；LoCoMo 题型映射：single-hop→single_hop、multi-hop→multi_session、temporal→temporal、adversarial→abstention（gold 规范为「无法确定」）、open-domain→open_domain

## 生产层

| 命令 | 说明 |
|------|------|
| `eval ci [-d N]` | CI 模式，不达标 exit(1) |
| `eval calibrate` | Judge 校准，计算 Spearman/Pearson 一致率 |

## 内置数据集

`tests/evals/` 包含 9 个 JSONL 数据集：`agent_eval`, `tool_selection`, `hallucination`, `coherence`, `trajectory`, `humaneval`, `swebench`, `code_generation`, `code_retrieval`。
