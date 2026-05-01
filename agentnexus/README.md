# AgentNexus — 多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个多智能体任务协同 CLI 工具。通过多 Agent 协作 + 本地知识库（RAG）+ 两级记忆系统 + 全链路可观测性，让复杂任务可靠地自动化执行。所有依赖本地运行，零部署成本，`pip install` 即可使用。

---

## 系统架构

```
用户在终端输入命令
        │
        ▼
┌───────────────────────────────────┐
│         CLI 层（Typer + Rich）     │
│  命令解析 / 流式渲染 / 交互确认    │
└───────────────┬───────────────────┘
                │
       ┌────────┴────────┐
       ▼                 ▼
  nexus run          nexus chat
  ─────────          ─────────
  Orchestrator       ReAct Agent
  (LangGraph)        (ReAct 循环)
   │  │  │            │
   ▼  ▼  ▼            ▼
 Research Coder     web_search
 Analyst Critic     python_execute
       │                 │
       └────────┬────────┘
                ▼
┌───────────────────────────────────┐
│           本地基础设施层           │
│  ChromaDB  │  SQLite  │  JSONL   │
│  （向量库） │ （记忆）  │ （追踪）  │
└───────────────────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **双模式运行** | `nexus run` 多 Agent 编排（plan→fan-out→analyze→critique→retry）；`nexus chat` 单 Agent 交互对话（ReAct 循环 + 工具调用） |
| **RAG 双路由检索** | 稠密向量 + BM25 混合检索 + Reranker 精排，自动判断走 Grep 精确匹配还是语义检索，命中率不足自动 fallback |
| **两级记忆系统** | 短期：进程内滚动窗口（deque，最多 50 条）；长期：SQLite 持久化 + 向量检索，跨会话上下文保持 |
| **全链路可观测性** | 每个 Agent 节点/LMM 调用自动创建 trace span，JSONL 写入 `~/.agentnexus/traces/`，`nexus logs view` 终端渲染 span 树 |
| **质量保障** | `nexus run` 内置 Critic Agent 评分（0-10），低于 7 分自动重试（最多 3 次）；`nexus eval` 跑 RAGAS 12 组策略组合评估 |
| **纯本地运行** | ChromaDB 向量库 + SQLite 记忆 + JSONL 日志，全部存于 `~/.agentnexus/`，无需外部服务 |
| **开箱即用** | `pip install -e .` → `nexus init` → 开始使用，PyInstaller 打包为单文件可执行文件 |

## 快速安装

```bash
# 从源码安装（开发模式）
git clone https://github.com/agentnexus/agentnexus.git
cd agentnexus
pip install -e .

# 安装全部依赖（含评估和打包工具）
pip install -e ".[dev,eval]"

# prompt_toolkit（chat 模式需要，已包含在 pip install -e . 中）
pip install prompt-toolkit
```

## 快速开始

```bash
# 1. 首次初始化（配置 API Key）
nexus init

# 2. 执行复杂任务（多 Agent 编排）
nexus run "分析特斯拉最新财报并写投资简报"

# 3. 交互式对话（单 Agent + 工具）
nexus chat

# 4. 添加知识库文档
nexus kb add ./docs/

# 5. 查看历史 trace
nexus logs list
nexus logs view --trace-id <trace_id>
```

## 命令参考

| 命令 | 描述 |
|------|------|
| `nexus run <task>` | 多 Agent 编排执行复杂任务 |
| `nexus chat` | 进入交互式对话模式（ReAct + 联网搜索 + 代码执行） |
| `nexus version` | 显示版本信息 |
| `nexus init` | 首次初始化引导（配置 API Key） |
| `nexus config [--set KEY --value VAL]` | 查看或修改配置 |
| `nexus kb add <path>` | 添加文档到知识库（PDF / Markdown / TXT） |
| `nexus kb list` | 查看知识库文档块数量 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |
| `nexus logs list [--days N]` | 列出历史 Trace 记录 |
| `nexus logs view --trace-id <id>` | 查看指定 Trace 的完整 Span 树 |
| `nexus eval list` | 列出评估数据集样本 |
| `nexus eval run` | 运行 RAG 评估（12 组策略组合） |
| `nexus stats [--days N]` | 查看 Token 成本统计 |

## 数据存储

所有数据存放在 `~/.agentnexus/`（可通过 `AGENTNEXUS_HOME` 环境变量修改）：

```
~/.agentnexus/
├── config.yaml        # 配置文件（API Key、模型等）
├── memory.db           # 长期记忆（SQLite）
├── chroma/             # 知识库向量（ChromaDB）
└── traces/             # Trace 日志（JSONL，按日期分文件）
    ├── 2026-05-01.jsonl
    └── evals/          # 评估报告（JSON）
```

## 配置

```bash
# 交互式配置向导
nexus init

# 查看当前配置（API Key 掩码显示）
nexus config

# 设置单项配置
nexus config --set llm_model_id --value deepseek-v4-flash
nexus config --set max_agent_steps --value 10
```

配置项与默认值：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm_api_key` | — | LLM API Key（必填） |
| `llm_model_id` | `deepseek-v4-flash` | 模型 ID |
| `llm_base_url` | `https://api.deepseek.com` | API 地址 |
| `llm_timeout` | `60` | 请求超时（秒） |
| `max_agent_steps` | `5` | ReAct 最大步数 |
| `tavily_api_key` | — | 搜索引擎 API Key（Tavily Search，可选） |
| `e2b_api_key` | — | 代码沙箱 API Key（可选） |

## 评估

```bash
# 列出 15 个评估样本
nexus eval list

# 运行全量评估（自动对比 12 种策略组合）
nexus eval run
```

评估指标：

| 指标 | 说明 |
|------|------|
| **Faithfulness** | 生成答案是否忠实于检索上下文 |
| **Answer Relevancy** | 答案与问题的语义相关度 |
| **Context Precision** | 检索上下文中相关文档的占比 |
| **Context Recall** | 检索上下文覆盖标准答案的程度 |

## 项目结构

```
agentnexus/
├── agentnexus/
│   ├── __main__.py             # python -m agentnexus 入口
│   ├── cli/
│   │   ├── __init__.py         # Typer app 注册 + 子命令挂载
│   │   ├── run.py              # nexus run / version
│   │   ├── chat.py             # nexus chat
│   │   ├── stats.py            # nexus stats
│   │   ├── config.py           # nexus config / init
│   │   ├── kb.py               # nexus kb add / list
│   │   ├── memory_cmd.py       # nexus memory list / clear
│   │   ├── logs.py             # nexus logs list / view
│   │   └── eval_cmd.py         # nexus eval list / run
│   ├── agents/
│   │   ├── re_act_agent.py     # ReAct 单 Agent 循环
│   │   ├── research_agent.py   # 信息检索 Agent
│   │   ├── coder_agent.py      # 代码生成执行 Agent
│   │   ├── analyst_agent.py    # 综合分析 Agent
│   │   ├── critic_agent.py     # 质量评分 Agent
│   │   └── multi_agent/
│   │       ├── orchestrator.py # LangGraph 主状态机
│   │       └── state.py        # AgentState 定义
│   ├── tools/
│   │   ├── tool_executor.py    # 工具注册与调度
│   │   ├── web_search.py       # 互联网搜索
│   │   └── code_executor.py    # 本地沙箱代码执行
│   ├── rag/
│   │   ├── ingestion.py        # 文档摄取（PDF/MD/TXT）
│   │   ├── chroma_client.py    # ChromaDB 封装
│   │   ├── retriever.py        # 混合检索（稠密 + BM25 + Reranker）
│   │   ├── router.py           # 双路由策略（Grep / RAG）
│   │   ├── grep_search.py      # ripgrep 精确匹配
│   │   ├── evaluator.py        # RAGAS 评估器
│   │   └── eval_dataset.py     # 评估样本数据集
│   ├── memory/
│   │   ├── short_term.py       # 短期记忆（deque）
│   │   ├── long_term.py        # 长期记忆（SQLite + 向量检索）
│   │   └── manager.py          # 记忆管理器（短+长期桥接）
│   ├── observability/
│   │   ├── tracer.py           # Trace Span / TraceContext / JSONL 写入
│   │   └── stats.py            # Token 成本聚合
│   └── core/
│       ├── config.py           # Pydantic Settings + 路径管理
│       └── llm.py              # OpenAI SDK 流式封装
├── .github/workflows/
│   ├── ci.yml                  # PR 触发 lint + test + eval
│   └── release.yml             # Tag 触发 PyInstaller 打包 + Release
├── agentnexus.spec             # PyInstaller 打包配置
├── pyproject.toml
├── test_all.py                 # 集成测试
├── test_eval_routes.py         # 检索策略对比测试
└── README.md
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev,eval]"

# 代码检查
ruff check agentnexus/

# 运行测试
python test_all.py

# 打包为单文件可执行文件
pip install pyinstaller
pyinstaller agentnexus.spec --noconfirm
# 产物: dist/agentnexus（Linux） / dist/agentnexus.exe（Windows）
```

## 技术栈

| 类别 | 技术 |
|------|------|
| Agent 编排 | LangGraph（状态机 + SQLite Checkpointer） |
| 单 Agent | ReAct 循环 + ToolExecutor |
| LLM 接口 | OpenAI SDK（兼容 DeepSeek / 通义千问 / GPT-4o） |
| 向量数据库 | ChromaDB（纯本地 PersistentClient） |
| 记忆存储 | SQLite（短期 deque + 长期持久化 + 向量检索） |
| CLI 框架 | Typer + Rich + prompt_toolkit |
| 检索 | Sentence Transformers（稠密）+ rank-bm25（稀疏）+ BGE-Reranker |
| 文档解析 | PyMuPDF（PDF）+ jieba（中文分词） |
| 可观测性 | 自研 JSONL Trace + `nexus logs` Rich Tree 渲染 |
| 评估 | 自研 RAGEvaluator（LLM-as-Judge，4 指标） |
| 打包 | PyInstaller + GitHub Actions CI/CD |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
