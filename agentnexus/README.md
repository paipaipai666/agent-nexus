# AgentNexus — 多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个 Harness Engineering 驱动的高可靠多智能体任务协同 CLI 工具。通过 LangGraph 状态机编排 + 硬约束质量门禁 + 确定性 fallback + 全链路可观测性 + **Git 式对话版本控制**，让复杂任务可靠地自动化执行。全部本地运行，`pip install` 即可使用。

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
│                  本地基础设施层                           │
│  ChromaDB（向量库）│ SQLite（记忆+检查点+版本历史）│ JSONL（追踪）│
└──────────────────────────────────────────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **Git 式对话版本控制** | 每轮对话自动创建 checkpoint，支持 `/undo` `/redo` `/branch` `/checkout` `/log` `/diff`。STM 全量快照 + LTM 增量引用，回退时完整恢复模型上下文 |
| **跨会话记忆管线** | `nexus run` 的 plan 节点注入历史记忆上下文，任务结束后自动提取偏好/事实/结论持久化 |
| **记忆生命周期管理** | LTM 按重要性+时间自动淘汰（`max_memories`），过期记忆 TTL 清理（`memory_ttl_days`） |
| **全链路可观测性** | JSONL Trace 逐节点即时刷盘（崩溃不丢数据），自动清理过期 trace（`trace_retention_days`） |
| **强 Schema 门禁** | 所有 Agent 输出通过 Pydantic 模型校验（CodeOutput / ResearchOutput / ExecutionResult），不通过不允许进入后续阶段 |
| **独立执行验证** | fork 子进程隔离执行，捕获 stdout/stderr/异常。缺库自动 `pip install`。执行前添加 HITL 确认，重试时自动跳过 |
| **升级式智能重试** | 9 类错误 + 每种独立的差异化重试上限。同一错误重复出现时自动升级策略（提示 → 限制 → 强制标准库），打破死循环 |
| **__main__ 自动追加** | AST 解析检测入口块——缺失时自动追加 `if __name__ == '__main__':` 调用所有顶层函数 |
| **Token 截断检测** | 捕获 `finish_reason=="length"`，触发 TRUNCATION 策略（精简/拆分） |
| **确定性 fallback** | 所有 LLM 调用点都有基于硬证据的降级策略——Critic 基于来源和代码完整性评分，Analyst 生成结构化降级答案，Research 保守续搜 |
| **关注点分离 Harness** | `analyst_node` 拆分为 7 个独立 Harness 组件，每组件单一职责 |
| **显式 AgentIO 契约** | `NODE_CONTRACTS` 定义每个 FSM 节点的输入/输出 state key，`validate_state()` 做完整性检查 |
| **Tavily 深度集成** | 按查询复杂度自适应搜索深度（basic/advanced），URL 去重 + score 过滤 + raw_content |
| **Web 搜索 + RAG 融合** | 结构化搜索结果直接送入 ResearchAgent，`SourceClaim` 带 URL 字段实现精确引用 |
| **阶段全可视** | plan 显示执行计划，research 展示每条来源的 URL 和置信度，code 预览生成结果 |
| **双模式运行** | `nexus run` 多 Agent 编排；`nexus chat` 单 Agent ReAct 对话 + 版本控制 |
| **RAG 双路由检索** | 稠密向量 + BM25 + Reranker，自动 Grep/RAG fallback |
| **两级记忆系统** | STM（deque）+ LTM（SQLite + ChromaDB 向量检索）+ 自动去重 + PII 过滤 |
| **API 容错** | LLM 调用 3 次指数退避重试（瞬时错误自动恢复） |

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
| `nexus chat [--no-memory]` | 交互式对话（ReAct + 联网搜索 + 代码执行 + 版本控制） |
| `nexus init` | 首次初始化（配置 API Key） |
| `nexus config [--set KEY --value VAL]` | 查看或修改配置 |

### Chat 版本控制命令（在 chat 中使用）

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

### 可观测性

| 命令 | 描述 |
|------|------|
| `nexus logs list [--days N]` | 列出历史 Trace |
| `nexus logs view --trace-id <id>` | 查看 Trace Span 树 |
| `nexus stats [--days N]` | Token 成本统计 |
| `nexus eval run` | 运行 RAG 质量评估 |
| `nexus version` | 显示版本 |

## 项目结构

```
agentnexus/
├── agentnexus/
│   ├── cli/
│   │   ├── __init__.py           # Typer app 定义 + 子应用注册
│   │   ├── run.py                # nexus run（多 Agent 编排入口）
│   │   ├── chat.py               # nexus chat（交互对话 + 版本控制命令）
│   │   ├── config.py             # nexus config（配置管理）
│   │   ├── kb.py                 # nexus kb（知识库增删）
│   │   ├── logs.py               # nexus logs（Trace 列表/树状查看）
│   │   ├── stats.py              # nexus stats（Token 成本统计）
│   │   ├── eval_cmd.py           # nexus eval（RAG 评估）
│   │   └── memory_cmd.py         # nexus memory（记忆管理）
│   ├── agents/
│   │   ├── schema.py             # TaskOutput / CodeOutput / ExecutionResult / ErrorType（9 类）
│   │   ├── coder_agent.py        # 代码生成 + Schema 校验 + AST 完整性检查
│   │   ├── executor_agent.py     # fork 子进程隔离执行 + 缺库自动安装
│   │   ├── research_agent.py     # RAG + Web 搜索 + 来源强制引用
│   │   ├── critic_agent.py       # 质量评分 + 硬规则检查 + 确定性 fallback
│   │   ├── critic_rules.py       # 硬规则检查器
│   │   ├── re_act_agent.py       # ReAct 循环 Agent（chat 模式使用）
│   │   ├── exceptions.py         # 异常定义
│   │   └── multi_agent/
│   │       ├── orchestrator.py   # LangGraph FSM + 升级策略 + 7 个 Harness 组件
│   │       └── state.py          # AgentState + NODE_CONTRACTS + validate_state
│   ├── memory/
│   │   ├── manager.py            # MemoryManager（STM + LTM 协调器）
│   │   ├── short_term.py         # ShortTermMemory（deque + JSON 序列化）
│   │   ├── long_term.py          # LongTermMemory（SQLite + ChromaDB + 淘汰策略）
│   │   └── versioned.py          # ConversationVersionManager（Git 式对话版本控制）
│   ├── rag/                      # 文档摄取 / ChromaDB / 双路由检索 / RAGAS 评估
│   │   ├── chroma_client.py      # ChromaDB PersistentClient + 嵌入模型
│   │   ├── ingestion.py          # PDF/MD/TXT 加载 + 清洗 + 分块 + 上下文富化
│   │   ├── retriever.py          # HybridRetriever（BM25 + 向量 + RRF + Reranker）
│   │   ├── grep_search.py        # ripgrep 代码搜索集成
│   │   ├── router.py             # 查询路由（代码 vs 自然语言）
│   │   ├── evaluator.py          # RAGEvaluator（4 个 RAGAS 指标）
│   │   └── eval_dataset.py       # 15 篇文档 + 30 个评估样本
│   ├── observability/
│   │   ├── tracer.py             # TraceManager（线程安全单例 + JSONL 刷盘 + 留存策略）
│   │   └── stats.py              # TokenStats（成本聚合 + 延迟分析）
│   ├── prompts/                  # 提示词模板（txt 文件）
│   ├── tools/
│   │   ├── code_executor.py      # 本地 + E2B 双执行器
│   │   ├── web_search.py         # Tavily API — 结构化搜索 + URL 去重 + score 过滤
│   │   ├── tool_executor.py      # 工具注册调度
│   │   └── tool_wrapper.py       # safe_call + fallback 系统
│   └── core/
│       ├── config.py             # Pydantic Settings（含 max_memories / ttl / retention）
│       └── llm.py                # LLM 流式调用 + 指数退避重试 + 截断检测
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
| LLM 接口 | OpenAI SDK（兼容 DeepSeek / 通义千问 / GPT-4o）+ 3 次指数退避重试 |
| 数据模型 | Pydantic v2（强 Schema 校验） |
| 向量数据库 | ChromaDB（纯本地 PersistentClient） |
| CLI 框架 | Typer + Rich + prompt_toolkit |
| 检索 | Sentence Transformers + rank-bm25 + BGE-Reranker |
| 可观测性 | 自研 JSONL Trace（即时刷盘 + 留存策略）+ `nexus logs` Rich Tree 渲染 |
| 对话版本控制 | SQLite DAG（STM 快照 + LTM 增量引用） |
| 打包 | PyInstaller + GitHub Actions CI/CD |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
