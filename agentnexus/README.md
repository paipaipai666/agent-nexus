# AgentNexus — 多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个工程级多智能体任务协同 CLI 工具。通过 LangGraph 状态机编排 + 本地 RAG 知识库 + 两级记忆系统 + 全链路可观测性，让复杂任务可靠地自动化执行。全部本地运行，`pip install` 即可使用。

---

## 系统架构

```
用户在终端输入 nexus run "任务描述"
              │
              ▼
┌──────────────────────────────────────────────────────────┐
│                    CLI 层（Typer + Rich）                 │
│         命令解析 / 流式渲染 / HITL 交互确认               │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│              Orchestrator（LangGraph FSM）                │
│                                                          │
│  START → plan → [research ∥ code] → execute              │
│                → analyze → critique → (retry | END)      │
│                                                          │
│  ┌──────────┐  ┌──────┐  ┌──────────┐  ┌──────────┐    │
│  │ Research │  │ Coder│  │ Executor │  │  Critic  │    │
│  │ 搜索+来源│  │ 代码 │  │ 执行+验证│  │ 硬规则+  │    │
│  │ 引用强制 │  │ Schema│  │ 自动安装 │  │ LLM 评分 │    │
│  └──────────┘  └──────┘  └──────────┘  └──────────┘    │
│       │           │           │              │          │
│       └───────────┴─────┬─────┘              │          │
│                         │                    │          │
│                         ▼                    │          │
│                  ┌──────────┐               │          │
│                  │ Analyst  │ ◄─────────────┘          │
│                  │ 综合分析 │                           │
│                  │ +确定性  │  ← 执行报告由系统硬生成   │
│                  │ 执行报告 │      LLM 只负责分析补充   │
│                  └──────────┘                           │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│                  本地基础设施层                           │
│   ChromaDB（向量库） │ SQLite（记忆+检查点）│ JSONL（追踪）│
└──────────────────────────────────────────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **强 Schema 门禁** | 所有 Agent 输出通过 Pydantic 模型校验（CodeOutput / ResearchOutput / ExecutionResult），不通过不允许进入后续阶段 |
| **独立执行验证** | Executor Agent 捕获 stdout/stderr/异常 + 智能输出比对（检测数据、图表、主题合规性）。缺库自动 `pip install` |
| **硬规则 + LLM 评估** | Critic 先跑 5 条硬规则（缺代码/缺来源/运行时异常/无输出/空结果），通过后才让 LLM 打质量分 |
| **8 类错误 + 分级 retry** | 错误分 MISSING_CODE / RUNTIME_ERROR / HALLUCINATION / TOOL_FAILURE / TRUNCATION 等 8 类，每类有独立策略和 checklist 式 escalating 指令 |
| **Token 截断检测** | 捕获 `finish_reason=="length"`，触发 TRUNCATION 策略（精简/拆分），不再盲目 force_code_only |
| **Planner 动态拆分** | 检测到上次代码截断时，强制 Planner 将复杂任务拆为多个独立 code 步骤 |
| **确定性执行报告** | 代码执行后系统硬生成状态报告（✅ 成功 / ❌ 失败），Analyst 的 LLM 无权篡改 |
| **双模式运行** | `nexus run` 多 Agent 编排；`nexus chat` 单 Agent ReAct 对话 |
| **RAG 双路由检索** | 稠密向量 + BM25 + Reranker，自动 Grep/RAG fallback |
| **两级记忆系统** | 短期 deque + 长期 SQLite + 向量检索 |
| **全链路可观测性** | JSONL Trace + `nexus logs` Rich Tree 渲染 |
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

| 命令 | 描述 |
|------|------|
| `nexus run <task>` | 多 Agent 编排执行复杂任务 |
| `nexus chat` | 交互式对话（ReAct + 联网搜索 + 代码执行） |
| `nexus init` | 首次初始化（配置 API Key） |
| `nexus config [--set KEY --value VAL]` | 查看或修改配置 |
| `nexus kb add <path>` | 添加文档到知识库 |
| `nexus kb list` | 查看知识库状态 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |
| `nexus logs list [--days N]` | 列出历史 Trace |
| `nexus logs view --trace-id <id>` | 查看 Trace Span 树 |
| `nexus eval run` | 运行 RAG 评估 |
| `nexus stats [--days N]` | Token 成本统计 |

## 项目结构

```
agentnexus/
├── agentnexus/
│   ├── cli/
│   │   ├── run.py / chat.py / config.py / kb.py / logs.py / eval_cmd.py / stats.py
│   ├── agents/
│   │   ├── schema.py              # TaskOutput / CodeOutput / ExecutionResult / ErrorType (8 类) / RETRY_STRATEGIES
│   │   ├── coder_agent.py         # 代码生成 + Schema 校验 + AST 完整性检查
│   │   ├── executor_agent.py      # 独立执行验证 + 缺库自动安装
│   │   ├── research_agent.py      # RAG + Web 搜索 + 来源强制引用
│   │   ├── analyst_agent.py       # 综合分析（LLM）
│   │   ├── critic_agent.py        # 硬规则先行 + LLM 质量评分
│   │   ├── critic_rules.py        # 5 条确定性硬规则
│   │   ├── retry_manager.py       # 错误分类 + 分级 escalating 策略
│   │   └── multi_agent/
│   │       ├── orchestrator.py    # LangGraph FSM + 确定性执行报告
│   │       └── state.py           # AgentState 定义
│   ├── tools/
│   │   ├── code_executor.py       # 本地 + E2B 双执行器
│   │   ├── web_search.py          # Tavily 搜索
│   │   ├── tool_executor.py       # 工具注册调度
│   │   └── tool_wrapper.py        # safe_call + fallback 系统
│   ├── rag/                       # 文档摄取 / ChromaDB / 双路由检索 / RAGAS 评估
│   ├── memory/                    # 短期 deque + 长期 SQLite + 向量检索
│   ├── observability/             # JSONL Trace + Token 成本统计
│   ├── prompts/                   # 提示词模板（txt + 动态注入）
│   └── core/
│       ├── config.py              # Pydantic Settings
│       └── llm.py                 # LLM 流式调用 + 指数退避重试 + 截断检测
├── pyproject.toml
└── README.md
```

## 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm_api_key` | — | LLM API Key（必填） |
| `llm_model_id` | `deepseek-v4-flash` | 模型 ID |
| `llm_base_url` | `https://api.deepseek.com` | API 地址 |
| `llm_timeout` | `60` | 请求超时（秒） |
| `tavily_api_key` | — | 搜索引擎 API Key（可选） |
| `e2b_api_key` | — | 代码沙箱 API Key（可选） |

## 开发

```bash
pip install -e ".[dev,eval]"
ruff check agentnexus/
python test_all.py
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
| 可观测性 | 自研 JSONL Trace + `nexus logs` Rich Tree 渲染 |
| 打包 | PyInstaller + GitHub Actions CI/CD |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
