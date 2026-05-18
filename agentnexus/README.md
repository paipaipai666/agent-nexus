# AgentNexus — 多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个生产级 Multi-Agent Harness — 通过 LangGraph 状态机编排 + 统一工具治理 + 硬约束质量门禁 + 四层评估体系 + Token 预算控制 + Git 式对话版本控制，将 Agent 从 Demo 推向生产。遵循「Agent 负责局部智能，Harness 负责全局控制」原则。全部本地运行，`pip install` 即可使用。

---

## 系统架构

```
用户在终端输入 nexus run "任务描述"
              │
              ▼
┌──────────────────────────────────────────────────────────┐
│                    CLI 层（Typer + Rich）                 │
│         命令解析 / 阶段可视 / HITL 交互确认               │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│              Orchestrator（LangGraph FSM）                │
│                                                          │
│  START → plan → research → code → execute → analyst → END │
│              ↑        │          │     ↓     ↑              │
│              │        │          │  success   │              │
│              │        │          │     │      │              │
│              │        │          │     │   crit < 7.0        │
│              │        │          │     │      │              │
│              │        │          │  failure → code (重试)    │
│              │        │          │  ModuleNotFoundError       │
│              │        │          └──→ research (查文档)      │
│              │        │                                      │
│              └─ plan ← crit < 7.0 / hard_verdict             │
│                                                          │
│  硬终止: max_duration(180s) / max_tool_calls(20) / max_retries(5) │
│  Token Budget: GREEN → YELLOW → RED → BREAK 四级降级      │
│  模型路由: 简单任务→fast 模型 / 复杂任务→strong 模型      │
│                                                          │
│  ┌──────────┐   ┌──────┐   ┌──────────┐                 │
│  │ Research │   │ Coder│   │ Executor │                 │
│  │ 搜索+来源│   │ Schema│  │ 执行+验证│                 │
│  │ 引用强制 │   │ 门禁  │   │ 缺库安装 │                 │
│  └──────────┘   └──────┘   └──────────┘                 │
│       │              │            │                      │
│       ▼              ▼            │                      │
│   检索综合     代码生成 +         │                      │
│   + LLM 摘要   __main__ 自动追加   │                      │
│                          ┌────────┘                      │
│                          ▼                               │
│                   ┌──────────┐                           │
│                   │ Analyst  │                           │
│                   │ 综合分析 │                           │
│                   │ +确定性  │ ← 执行报告由系统硬生成    │
│                   │ 执行报告 │   LLM 只负责分析补充      │
│                   └──────────┘                           │
│                                                          │
│  HITL 机制: 首次 code → 确认 → execute                   │
│             重试 code → 自动 execute（不再询问）          │
│                                                          │
│  跨会话记忆: MemoryManager 注入 plan 节点，               │
│             任务结束自动 conclude 到 LTM                  │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│           工具治理层（Tool Registry 统一网关）            │
│  RBAC / Schema 校验 / 速率限制 / 超时控制                │
│  风险分级(L/M/H) / HITL 关卡 / 审计日志                  │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│                  本地基础设施层                           │
│  ChromaDB（向量库）│ SQLite（记忆+检查点+版本历史）│ JSONL（追踪）│
└──────────────────────────────────────────────────────────┘
```

## 核心特性

### Harness 基础

| 特性 | 说明 |
|------|------|
| **统一工具治理** | Tool Registry 作为唯一网关，每个工具登记 9 项元信息：名称/描述/参数 Schema/RBAC/风险等级/HITL/超时/速率限制/审计策略。调用时强制校验 |
| **硬终止条件** | `max_duration`(180s)、`max_tool_calls`(20)、`max_retries`(5) 三道硬闸，每节点入口检查 |
| **Token 预算控制** | BudgetTracker 四级降级（GREEN→YELLOW→RED→BREAK），按任务复杂度分配预算，超标强制收束 |
| **模型路由** | 按任务复杂度 + 预算状态自动切换模型：复杂任务→strong，简单任务→fast，RED 状态降级 |

### 编排与执行

| 特性 | 说明 |
|------|------|
| **声明式计划** | Planner 输出声明式 `{intent, agent, input}`，Orchestrator 独占裁决权 |
| **升级式智能重试** | 9 类 ErrorType + 差异化重试上限 + 渐进升级策略 |
| **强 Schema 门禁** | 所有 Agent 输出通过 Pydantic 校验，不通过不进入后续阶段 |
| **独立执行验证** | fork 子进程隔离，缺库自动 `pip install` |
| **__main__ 自动追加** | AST 解析检测入口块，缺失时自动追加 |
| **确定性 fallback** | Critic/Analyst/Research 均有基于硬证据的降级策略 |
| **显式 AgentIO 契约** | `NODE_CONTRACTS` + `validate_state()` 入口完整性检查 |

### 记忆系统

| 特性 | 说明 |
|------|------|
| **两级记忆** | STM（deque）+ LTM（SQLite + ChromaDB 向量检索）+ PII 过滤 |
| **跨会话记忆管线** | plan 节点注入历史记忆，任务结束自动提取偏好/事实/结论 |
| **遗忘机制** | 衰减评分 + 按重要性淘汰 + 过期 TTL + 中分记忆压缩为摘要 |
| **memory_search 工具** | Agent 可在推理时主动检索长期记忆，混合注入模式 |
| **Git 式对话版本控制** | 每轮自动 checkpoint，支持 undo/redo/branch/checkout/log/diff |

### 评估体系（四层 12 指标）

| 层 | 指标 | 命令 |
|------|------|------|
| **检索层** | Context Relevance / Recall / Precision / Latency | `nexus eval run` |
| **生成层** | Faithfulness / Relevance / Hallucination Rate | `nexus eval hallucination` |
| **Agent 层** | Tool Selection / Tool Success / Coherence | `nexus eval tool-selection` `nexus eval coherence` |
| **生产层** | Cost per Query / P99 Latency | `nexus stats` `nexus eval ci` |

**设计原则**：Judge 模型和生成模型分离（不同模型家族），避免自己评自己导致分数虚高。默认 GLM-4.7-Flash 做独立 Judge。

### 可观测性

| 特性 | 说明 |
|------|------|
| **JSONL Trace** | 逐 span 即时刷盘，崩溃不丢数据，Rich Tree 渲染 |
| **Trace 留存策略** | 自动清理过期 JSONL（`trace_retention_days`） |
| **Token 成本统计** | `nexus stats` — 按模型/日期聚合成本 + 延迟分析 |
| **工具审计日志** | `nexus audit` — 查看所有工具调用记录（调用者/参数/耗时/HITL） |
| **阶段全可视** | 每阶段 spinner + 进度指示 + research claims URL 详情 |

## 快速安装

```bash
git clone https://github.com/agentnexus/agentnexus.git
cd agentnexus
pip install -e .
```

## 快速开始

```bash
nexus init                                    # 配置 API Key
nexus run "搜索 AI 趋势并写分析报告"           # 多 Agent 编排
nexus chat                                     # 交互对话模式
nexus kb add ./docs/                           # 添加知识库
nexus logs list                                # 查看 trace 历史
```

## 命令参考

### 任务执行

| 命令 | 描述 |
|------|------|
| `nexus run <task> [-n]` | 多 Agent 编排执行复杂任务，`-n` 跳过交互确认 |
| `nexus tui` | 终端原生对话界面（Textual TUI，Catppuccin 主题） |
| `nexus chat [--no-memory]` | 交互式对话（ReAct + 联网搜索 + 记忆检索 + 版本控制） |
| `nexus init` | 首次初始化（配置 API Key） |
| `nexus config [--set KEY --value VAL]` | 查看或修改配置 |

### Chat 版本控制（在 chat 中使用）

| 命令 | 描述 |
|------|------|
| `/undo` | 回退到上一轮对话（STM + LTM 同步恢复） |
| `/redo` | 重做被撤销的对话 |
| `/log [--all]` | 查看对话历史 checkpoint 树 |
| `/branch <name>` | 从当前位置创建分支并切换 |
| `/checkout <ref>` | 切换到 checkpoint ID 或分支名 |
| `/diff [ref1] [ref2]` | 对比两个 checkpoint 的 STM/LTM 差异 |
| `/status` | 显示当前分支、HEAD、可回退/可重做状态 |
| `/clear` | 重置短期记忆和版本历史 |
| `/clear --all` | 清除所有记忆（STM + LTM + 版本历史） |

### 知识库 & 记忆

| 命令 | 描述 |
|------|------|
| `nexus kb add <path>` | 添加文档到知识库 |
| `nexus kb list` | 查看知识库状态 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |

### 评估（12 指标）

| 命令 | 描述 |
|------|------|
| `nexus eval run` | RAG 质量评估（12 种策略组合，含 context_relevancy） |
| `nexus eval trajectory [-t ID] [-d N]` | 轨迹质量评估（5 项规则检查） |
| `nexus eval component` | 组件级评估（单 Agent + 按工具分解成功率） |
| `nexus eval hallucination [-t ID]` | 幻觉率检测（声明提取 + 上下文验证） |
| `nexus eval tool-selection` | 工具选择准确率（标注 eval set 对比） |
| `nexus eval coherence [-t ID]` | 多步推理连贯性（独立 Judge 模型评分） |
| `nexus eval ci [-d N]` | CI 模式：全量评估，不达标 exit(1) |

### 可观测性

| 命令 | 描述 |
|------|------|
| `nexus logs list [--days N]` | 列出历史 Trace |
| `nexus logs view --trace-id <id>` | 查看 Trace Span 树 |
| `nexus stats [--days N]` | Token 成本统计 |
| `nexus audit [-n N] [-t tool]` | 查看工具调用审计日志 |
| `nexus version` | 显示版本 |

## 项目结构

```
agentnexus/
├── agentnexus/
│   ├── cli/
│   │   ├── __init__.py           # Typer app 定义 + 子应用注册
│   │   ├── run.py                # nexus run（多 Agent 编排入口 + Budget 注入）
│   │   ├── chat.py               # nexus chat（交互对话 + 版本控制 + 3 工具注册）
│   │   ├── config.py             # nexus config（配置管理）
│   │   ├── kb.py                 # nexus kb（知识库增删）
│   │   ├── logs.py               # nexus logs（Trace 列表/树状查看）
│   │   ├── stats.py              # nexus stats（Token 成本统计）
│   │   ├── eval_cmd.py           # nexus eval（component/trajectory/ci）
│   │   ├── audit.py              # nexus audit（工具调用审计日志）
│   │   └── memory_cmd.py         # nexus memory（记忆管理）
│   ├── agents/
│   │   ├── schema.py             # TaskOutput / CodeOutput / ExecutionResult / ErrorType（9 类）
│   │   ├── coder_agent.py        # 代码生成 + Schema 校验 + AST 完整性检查
│   │   ├── executor_agent.py     # fork 子进程隔离执行 + 缺库自动安装
│   │   ├── research_agent.py     # RAG + Web 搜索 + 来源强制引用
│   │   ├── critic_agent.py       # 质量评分 + 硬规则检查 + 确定性 fallback
│   │   ├── critic_rules.py       # 硬规则检查器
│   │   ├── re_act_agent.py       # ReAct 循环 Agent（chat 模式使用）
│   │   └── multi_agent/
│   │       ├── orchestrator.py   # LangGraph FSM + 硬终止 + Budget 检查 + 模型路由
│   │       └── state.py          # AgentState(38 字段) + NODE_CONTRACTS + validate_state
│   ├── memory/
│   │   ├── manager.py            # MemoryManager（STM + LTM 协调器 + PII 过滤）
│   │   ├── short_term.py         # ShortTermMemory（deque + JSON 序列化）
│   │   ├── long_term.py          # LongTermMemory（SQLite + ChromaDB + 淘汰 + 压缩）
│   │   └── versioned.py          # ConversationVersionManager（Git 式对话版本控制）
│   ├── evaluation/
│   │   ├── trajectory.py         # TrajectoryEvaluator（5 项确定性规则检查）
│   │   ├── component.py          # ComponentEvaluator（单 Agent + 按工具分解）
│   │   ├── hallucination.py      # HallucinationDetector（声明提取 + 上下文验证）
│   │   ├── tool_selection.py     # ToolSelectionEvaluator（标注对比 + 按工具分解）
│   │   └── coherence.py          # CoherenceEvaluator（独立 Judge LLM 连贯性评分）
│   ├── rag/                      # 文档摄取 / ChromaDB / 双路由检索 / RAGAS 评估
│   │   ├── chroma_client.py      # ChromaDB PersistentClient + 嵌入模型
│   │   ├── ingestion.py          # PDF/MD/TXT 加载 + 清洗 + 分块 + 上下文富化
│   │   ├── retriever.py          # HybridRetriever（BM25 + 向量 + RRF + Reranker）
│   │   ├── grep_search.py        # ripgrep 代码搜索集成
│   │   ├── router.py             # 查询路由（代码 vs 自然语言）
│   │   ├── evaluator.py          # RAGEvaluator（4 个 RAGAS 指标）
│   │   └── eval_dataset.py       # 15 篇文档 + 30 个评估样本
│   ├── observability/
│   │   ├── tracer.py             # TraceManager（线程安全单例 + 即时刷盘 + 留存策略）
│   │   └── stats.py              # TokenStats（成本聚合 + 延迟分析）
│   ├── tools/
│   │   ├── registry.py           # ToolRegistry（RBAC/Schema/超时/速率/审计）
│   │   ├── code_executor.py      # 本地 + E2B 双执行器
│   │   ├── web_search.py         # Tavily API — 结构化搜索 + URL 去重 + score 过滤
│   │   ├── memory_search.py      # memory_search — Agent 主动检索 LTM
│   │   ├── tool_executor.py      # 工具注册调度（兼容包装）
│   │   └── tool_wrapper.py       # safe_call + fallback 系统
│   ├── prompts/                  # 提示词模板（txt 文件）
│   └── core/
│       ├── config.py             # Pydantic Settings（含 judge 模型配置）
│       ├── llm.py                # LLM 流式调用 + 指数退避重试 + 截断检测
│       ├── judge_llm.py          # 独立 Judge LLM（GLM-4.7-Flash，不同模型家族）
│       ├── budget.py             # BudgetTracker（四级降级 + 按复杂度分配）
│       └── model_router.py       # 模型路由（复杂度分类 + 预算状态联动）
├── tests/
│   ├── unit/
│   │   ├── test_long_term.py     # LTM 单元测试
│   │   ├── test_short_term.py    # STM 单元测试
│   │   ├── test_versioned.py     # 对话版本控制系统单元测试
│   │   ├── test_trace.py         # Trace 系统单元测试
│   │   ├── test_router.py        # 查询路由单元测试
│   │   ├── test_config.py        # 配置加载单元测试
│   │   └── test_tool_executor.py # 工具执行单元测试
│   ├── integration/              # 集成测试
│   └── regression/               # 全工作流回归测试
├── pyproject.toml
└── README.md
```

## 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm_api_key` | — | LLM API Key（必填） |
| `llm_model_id` | `deepseek/deepseek-v4-flash` | 模型 ID（含 provider 前缀） |
| `llm_base_url` | `https://api.deepseek.com` | API 地址 |
| `llm_timeout` | `60` | 请求超时（秒） |
| `tavily_api_key` | — | 搜索引擎 API Key（可选） |
| `e2b_api_key` | — | 代码沙箱 API Key（可选） |
| `judge_model_id` | `zhipu/glm-4.7-flash` | Judge 模型（评估专用，不同模型家族） |
| `judge_api_key` | — | Judge 模型 API Key（可选，回退主 Key） |
| `judge_base_url` | `https://open.bigmodel.cn/api/paas/v4/` | Judge 模型 API 地址 |
| `max_memories` | `1000` | LTM 最大记忆数（超出按重要性+时间淘汰） |
| `memory_ttl_days` | `90` | LTM 记忆过期天数 |
| `trace_retention_days` | `30` | Trace JSONL 留存天数 |

## 开发

```bash
pip install -e ".[dev,eval]"
ruff check agentnexus/ tests/
python -m pytest tests/ -v
```

## 技术栈

| 类别 | 技术 |
|------|------|
| Agent 编排 | LangGraph（FSM + SQLite Checkpointer） |
| LLM 接口 | OpenAI SDK（兼容 DeepSeek / 通义千问 / GPT-4o）+ 模型路由 + 指数退避重试 |
| 数据模型 | Pydantic v2（强 Schema 校验） |
| 向量数据库 | ChromaDB（纯本地 PersistentClient） |
| CLI 框架 | Typer + Rich + prompt_toolkit |
| 检索 | Sentence Transformers + rank-bm25 + BGE-Reranker |
| 可观测性 | 自研 JSONL Trace（即时刷盘 + 留存策略）+ 工具审计日志 |
| 工具治理 | 自研 ToolRegistry（RBAC + Schema + 超时 + 速率 + 审计） |
| 对话版本控制 | SQLite DAG（STM 快照 + LTM 增量引用） |
| 评估 | 四层评估体系（Component / Trajectory / Task / CI） |
| 成本控制 | 自研 BudgetTracker（四级降级 + 模型路由 + 上下文压缩） |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
