# AgentNexus — 多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个 Harness Engineering 驱动的高可靠多智能体任务协同 CLI 工具。通过 LangGraph 状态机编排 + 硬约束质量门禁 + 确定性 fallback + 全链路可观测性，让复杂任务可靠地自动化执行。全部本地运行，`pip install` 即可使用。

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
| **独立执行验证** | fork 子进程隔离执行，捕获 stdout/stderr/异常。缺库自动 `pip install`。执行前添加 HITL 确认，重试时自动跳过 |
| **升级式智能重试** | 9 类错误 + 每种独立的差异化重试上限。同一错误重复出现时自动升级策略（提示 → 限制 → 强制标准库），打破死循环 |
| **__main__ 自动追加** | LLM 生成代码后，AST 解析检测入口块——缺失时自动追加 `if __name__ == '__main__':` 调用所有顶层函数（防御 LLM 忽略 prompt 指令） |
| **Token 截断检测** | 捕获 `finish_reason=="length"`，触发 TRUNCATION 策略（精简/拆分），不再盲目 force_code_only |
| **确定性执行报告** | 代码执行后系统硬生成状态报告（✅ 成功 / ❌ 失败 + stdout预览），Analyst 的 LLM 无权篡改 |
| **确定性 fallback** | 所有 LLM 调用点都有基于硬证据的降级策略——Critic 基于来源和代码完整性评分（0.0~8.0），Analyst 生成结构化降级答案，Research 保守续搜 |
| **关注点分离 Harness** | `analyst_node` 拆分为 7 个独立 Harness 组件（执行报告、LLM 合成、数据编造检测、状态修正、源码拼接、Critic 路由、降级兜底），每组件单一职责 |
| **显式 AgentIO 契约** | `NODE_CONTRACTS` 定义每个 FSM 节点的输入/输出 state key，`validate_state()` 在节点入口做完整性检查，缺失必填 key 立即报错 |
| **Tavily 深度集成** | 按查询复杂度自适应搜索深度（basic/advanced），advanced 模式自动获取原文全文，URL 去重 + score 过滤（<0.3 丢弃），域名过滤参数透传 |
| **Web 搜索 + RAG 融合** | 结构化搜索结果（title/URL/content/score/date）直接送入 ResearchAgent，`SourceClaim` 带 URL 字段实现精确引用 |
| **阶段全可视** | 每个阶段带 spinner 旋转器 + 进度指示，plan 显示执行计划，research 展示每条来源的 URL 和置信度，code 预览生成结果，execute 显示输出 |
| **双模式运行** | `nexus run` 多 Agent 编排；`nexus chat` 单 Agent ReAct 对话 |
| **RAG 双路由检索** | 稠密向量 + BM25 + Reranker，自动 Grep/RAG fallback |
| **两级记忆系统** | 短期 deque + 长期 SQLite + 向量检索（含自动迁移逻辑） |
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
│   │   ├── schema.py              # TaskOutput / CodeOutput / ExecutionResult / ErrorType (9 类)
│   │   ├── coder_agent.py         # 代码生成 + Schema 校验 + AST 完整性检查
│   │   ├── executor_agent.py      # fork 子进程隔离执行 + 缺库自动安装 + Python 3.13 compat
│   │   ├── research_agent.py      # RAG + Web 搜索 + 来源强制引用
│   │   ├── analyst_agent.py       # 综合分析（LLM，已废弃——逻辑已迁入 orchestrator 的 7 个 Harness 组件）
│   │   ├── critic_agent.py        # 质量评分 + 硬规则检查 + 确定性 fallback（analyst_node 内部调用）
│   │   ├── critic_rules.py        # 硬规则检查器（critic_agent 依赖）
│   │   └── multi_agent/
│   │       ├── orchestrator.py    # LangGraph FSM + 升级策略 + 7 个 Harness 组件
│   │       └── state.py           # AgentState + NODE_CONTRACTS + validate_state 契约校验
│   ├── tools/
│   │   ├── code_executor.py       # 本地 + E2B 双执行器
│   │   ├── web_search.py          # Tavily API — structured results + URL dedup + score filter + raw_content
│   │   ├── tool_executor.py       # 工具注册调度
│   │   └── tool_wrapper.py        # safe_call + fallback 系统
│   ├── rag/                       # 文档摄取 / ChromaDB / 双路由检索 / RAGAS 评估
│   ├── memory/                    # 短期 deque + 长期 SQLite + 向量检索（含自动迁移）
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
| `llm_model_id` | `deepseek/deepseek-v4-flash` | 模型 ID（含 provider 前缀） |
| `llm_base_url` | `https://api.deepseek.com` | API 地址 |
| `llm_timeout` | `60` | 请求超时（秒） |
| `tavily_api_key` | — | 搜索引擎 API Key（可选） |
| `e2b_api_key` | — | 代码沙箱 API Key（可选） |

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
| 可观测性 | 自研 JSONL Trace + `nexus logs` Rich Tree 渲染 |
| 打包 | PyInstaller + GitHub Actions CI/CD |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
