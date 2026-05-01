# AgentNexus — 企业级多智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![CI](https://github.com/agentnexus/agentnexus/actions/workflows/ci.yml/badge.svg)](https://github.com/agentnexus/agentnexus/actions/workflows/ci.yml)

**AgentNexus** 是一个工程级多智能体任务协同 CLI 工具。通过多 Agent 协作 + 本地知识库（RAG）+ 记忆系统 + 全链路可观测性，让复杂任务可靠地自动化执行。所有依赖本地运行，零部署成本，`pip install` 即可使用。

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
                ▼
┌───────────────────────────────────┐
│       Orchestrator Agent          │  ← LangGraph 状态机
│       任务规划 + 路由调度          │
└──────┬──────┬──────┬──────────────┘
       │      │      │
       ▼      ▼      ▼
  Research  Coder  Analyst      ← 专业 Agent
  搜索抓取  代码执行  分析报告
       │      │      │
       └──────┴──────┘
              │
              ▼
        Critic Agent             ← 质量评估 + 触发重试
              │
              ▼
┌───────────────────────────────────┐
│           本地基础设施层           │
│  ChromaDB  │  SQLite  │  日志文件 │
│  （向量库） │ （记忆）  │ （追踪）  │
└───────────────────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **Multi-Agent 编排** | LangGraph 状态机驱动，Orchestrator 分解任务 → 并行 Research/Coder → Analyst 合成 → Critic 评分重试 |
| **RAG 双路由检索** | 稠密向量 + BM25 混合检索 + Reranker 精排，自动 fallback，RAGAS 量化评估 |
| **记忆系统** | 短期滚动摘要 + 长期 SQLite 持久化，跨会话上下文保持 |
| **全链路可观测性** | 结构化 JSONL trace，`nexus logs` 命令终端渲染 span 树 |
| **纯本地运行** | ChromaDB 向量库 + SQLite 记忆 + JSONL 日志，全部存于 `~/.agentnexus/`，无需外部服务 |
| **单文件分发** | PyInstaller 打包为独立可执行文件，用户无需安装 Python |

## 快速安装

```bash
# pip 安装（推荐）
pip install agentnexus

# 从源码安装（开发模式）
git clone https://github.com/agentnexus/agentnexus.git
cd agentnexus
pip install -e .

# 安装全部依赖（含评估工具）
pip install -e ".[dev,eval]"
```

## 快速开始

```bash
# 1. 首次初始化（生成配置）
nexus init

# 2. 执行任务
nexus run "分析特斯拉最新财报并写投资简报"

# 3. 交互式对话
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
| `nexus run <task>` | 执行一个多步骤任务 |
| `nexus chat` | 进入交互式对话模式 |
| `nexus version` | 显示版本信息 |
| `nexus init` | 首次初始化引导（生成配置文件和目录） |
| `nexus config` | 修改配置（模型、API Key 等） |
| `nexus kb add <path>` | 添加文档到知识库（支持 PDF / Markdown / 文本） |
| `nexus kb list` | 查看知识库状态和文档块数量 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |
| `nexus logs list [--days N]` | 列出历史 Trace 记录 |
| `nexus logs view --trace-id <id>` | 查看指定 Trace 的完整 Span 树 |
| `nexus eval list` | 列出可用的评估数据集 |
| `nexus eval run` | 运行 RAG 评估并输出指标报告 |
| `nexus stats [--days N]` | 查看 Token 成本统计 |

## 评估

运行 RAG 评估套件，比较不同分块策略和检索方式的指标：

```bash
# 列出评估样本
nexus eval list

# 运行全量评估（自动对比 12 种配置组合）
nexus eval run
```

评估指标包含：

- **Faithfulness**: 生成答案是否忠实于检索上下文
- **Answer Relevancy**: 答案与问题的相关度
- **Context Precision**: 检索上下文中相关文档的比例
- **Context Recall**: 检索上下文覆盖标准答案的程度

## 配置

配置文件位于 `~/.agentnexus/config.yaml`，通过 `nexus init` 生成：

```yaml
llm:
  provider: deepseek          # 模型提供商
  model: deepseek-chat        # 模型名称
  api_key: your-api-key       # API Key
  api_base: https://api.deepseek.com  # API 地址

storage:
  base_dir: ~/.agentnexus     # 数据存储根目录
  chroma_dir: ~/.agentnexus/chroma    # 向量库目录
  traces_dir: ~/.agentnexus/traces    # Trace 日志目录

rag:
  chunk_size: 512             # 文档分块大小
  chunk_overlap: 64           # 分块重叠
  top_k: 5                    # 检索返回 top-k 结果
  use_hybrid: true            # 是否启用混合检索（稠密 + BM25）
```

## 项目结构

```
agentnexus/
├── agentnexus/
│   ├── __main__.py           # PyInstaller / python -m 入口
│   ├── cli/                  # Typer 命令定义
│   │   ├── __init__.py       #   主入口 + 全部命令实现
│   │   ├── run.py
│   │   ├── chat.py
│   │   ├── kb.py
│   │   ├── logs.py
│   │   ├── eval.py
│   │   └── render.py
│   ├── agents/               # Multi-Agent 编排
│   │   ├── orchestrator.py   #   LangGraph 主状态机
│   │   ├── research_agent.py
│   │   ├── coder_agent.py
│   │   ├── analyst_agent.py
│   │   └── critic_agent.py
│   ├── tools/                # Agent 工具
│   │   ├── web_search.py
│   │   ├── code_executor.py
│   │   ├── file_ops.py
│   │   └── api_caller.py
│   ├── rag/                  # RAG 知识库系统
│   │   ├── ingestion.py      #   文档摄取 pipeline
│   │   ├── retriever.py      #   双路由检索（Grep / RAG + fallback）
│   │   └── evaluator.py      #   RAGAS 评估
│   ├── memory/               # 记忆系统
│   │   ├── short_term.py     #   内存滚动摘要
│   │   └── long_term.py      #   SQLite 长期记忆
│   ├── observability/        # 可观测性
│   │   ├── tracer.py         #   JSONL 结构化日志
│   │   └── stats.py          #   Token 成本统计
│   └── core/                 # 核心基础设施
│       ├── config.py         #   Pydantic Settings
│       └── llm.py            #   LLM 统一封装
├── tests/                    # 测试
│   ├── unit/
│   ├── integration/
│   └── evals/                # 评估数据集
├── .github/workflows/
│   ├── ci.yml                # Lint + Test + Eval
│   └── release.yml           # PyInstaller 打包 + GitHub Release
├── agentnexus.spec           # PyInstaller 打包配置
├── pyproject.toml
└── README.md
```

## 开发

```bash
# 克隆并安装开发依赖
git clone https://github.com/agentnexus/agentnexus.git
cd agentnexus
pip install -e ".[dev,eval]"

# 代码检查
ruff check agentnexus/ tests/

# 运行测试
pytest tests/
python test_all.py

# 打包为单文件可执行文件
pip install pyinstaller
pyinstaller agentnexus.spec --noconfirm
# 产物在 dist/agentnexus（Linux）或 dist/agentnexus.exe（Windows）

# 构建发布包
pip install build
python -m build
```

## 技术栈

| 类别 | 技术 |
|------|------|
| Agent 框架 | LangGraph（状态机编排）、LangChain（工具链） |
| LLM 接口 | DeepSeek / 通义千问 / GPT-4o（通过 LiteLLM 统一接口） |
| 向量数据库 | ChromaDB（纯本地，无需外部服务） |
| 记忆存储 | SQLite（零配置单文件数据库） |
| CLI 框架 | Typer + Rich（流式渲染、彩色输出、进度条） |
| 检索 | rank-bm25（稀疏检索）+ Sentence Transformers（稠密向量） |
| 文档解析 | PyMuPDF（PDF 解析）+ jieba（中文分词） |
| 评估 | RAGAS（Faithfulness / Relevancy / Precision / Recall） |
| 打包分发 | PyInstaller（单文件可执行文件）、GitHub Actions（CI/CD） |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
