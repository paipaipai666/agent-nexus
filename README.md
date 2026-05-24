# AgentNexus — ReAct 单智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**AgentNexus** 是一个生产级 ReAct（Thought→Action→Observe）单智能体任务协同 CLI 工具。纯本地运行，ChromaDB + SQLite + JSONL 三存储，支持联网搜索、代码执行、文件操作、长期记忆、对话版本控制、四层评估体系。

---

## 系统架构

```
用户在终端输入 nexus <command>
              │
              ▼
┌──────────────────────────────────────────────────────────┐
│                    CLI 层（Typer + Rich）                 │
│  nexus kb / memory / logs / eval / stats / config / ...  │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│              ReActAgent（Thought→Action→Observe）        │
│                                                          │
│  ┌──────────────────┐    ┌──────────────────┐           │
│  │    Thought       │ →  │     Action       │           │
│  │  LLM 推理/规划   │    │  工具调用/代码    │           │
│  └──────────────────┘    └────────┬─────────┘           │
│                                   │                      │
│                                   ▼                      │
│  ┌──────────────────┐    ┌──────────────────┐           │
│  │   Observation    │ ←  │   ToolExecutor   │           │
│  │ 工具执行结果      │    │  内置工具 + MCP   │           │
│  └──────────────────┘    └──────────────────┘           │
│                                                          │
│  硬终止: max_steps / max_duration                        │
│  跨会话记忆: MemoryManager 注入上下文                     │
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
│  ChromaDB（向量库）│ SQLite（记忆+版本历史）│ JSONL（追踪）│
└──────────────────────────────────────────────────────────┘
```

## 核心特性

### Agent 执行

| 特性 | 说明 |
|------|------|
| **ReAct 循环** | Thought→Action→Observe 三步循环，LLM 驱动推理与工具调用 |
| **硬终止保护** | `max_steps` + `max_duration` 双重保险，防止 Agent 跑飞 |
| **11 种内置工具** | 代码执行（本地 + E2B 沙箱）、Web 搜索（Tavily）、文件读写、目录列表、grep 搜索、Shell 命令、记忆读写、知识库检索、子代理委派 |
| **MCP Client 扩展** | 可从外部 MCP server 导入 tools，统一纳入现有 ToolRegistry 的权限、Schema、HITL、超时、速率和审计链路 |
| **HITL 确认** | 高风险操作（代码执行 / Shell / 覆盖文件）需用户确认，`-n` 跳过 |
| **__main__ 自动追加** | AST 解析检测缺失的入口块，自动追加（工具方法） |

### 记忆系统

| 特性 | 说明 |
|------|------|
| **两级记忆** | STM（deque）+ LTM（SQLite + ChromaDB 向量检索） |
| **跨会话记忆管线** | plan 阶段注入历史记忆，任务结束自动提取偏好/事实/结论到 LTM |
| **遗忘机制** | 衰减评分 + 按重要性淘汰 + TTL 过期 + 中度记忆压缩为摘要 |
| **主动记忆工具** | Agent 可在推理时主动调用 `memory_search` / `memory_save` 读写 LTM |
| **PII 过滤** | 正则屏蔽 email/电话/API Key，保障隐私安全 |

### 评估体系（四层）

| 层 | 指标 | 命令 |
|------|------|------|
| **检索层** | Context Relevance / Recall / Precision / Latency | `nexus eval run` |
| **生成层** | Faithfulness / Relevance / Hallucination Rate | `nexus eval hallucination` |
| **Agent 层** | Tool Selection / Tool Success / Coherence | `nexus eval tool-selection` / `coherence` |
| **生产层** | Cost per Query / P99 Latency / Trajectory | `nexus stats` / `nexus eval ci` |

**设计原则**：Judge 模型和生成模型分离（不同模型家族），避免自己评自己导致分数虚高。

### 可观测性

| 特性 | 说明 |
|------|------|
| **JSONL Trace** | 逐 span 即时刷盘，Rich Tree 渲染，支持留存策略 |
| **Token 成本统计** | `nexus stats` — 按模型/日期聚合成本 + 延迟分析 |
| **工具审计日志** | `nexus audit` — 查看所有工具调用记录（参数/耗时/HITL） |
| **Rich 进度展示** | 每阶段 spinner + 进度指示 + Research claims URL 详情 |

### TUI 终端界面

基于 **Textual** 框架的原生终端对话界面，Catppuccin Mocha 主题，支持：
- 实时消息流式渲染
- 侧边栏记忆/工具面板
- HUD 状态指示器
- 键盘快捷键操作

---

## 快速安装

```bash
git clone https://github.com/agentnexus/agentnexus.git
cd ./agentnexus
pip install -e ".[dev,eval]"
```

> **注意**：安装直接在仓库根目录进行；`pyproject.toml` 就位于仓库根目录。

## 快速开始

```bash
nexus init                                    # 配置 API Key
nexus tui                                     # TUI 终端界面
nexus kb add <path-to-docs>                   # 添加知识库
nexus stats                                   # 查看 Token 统计
```

## 命令参考

### 命令总览

| 命令 | 描述 |
|------|------|
| `nexus init` | 首次初始化（配置 API Key） |
| `nexus config [--set KEY --value VAL]` | 查看或修改配置 |
| `nexus tui` | 终端原生界面（Textual TUI，Catppuccin 主题） |
| `nexus version` | 显示版本 |
| `nexus kb add <path>` | 添加文档到知识库 |
| `nexus kb list` | 查看知识库状态 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |
| `nexus logs list [--days N]` | 列出历史 Trace |
| `nexus logs view --trace-id <id>` | 查看 Trace Span 树 |
| `nexus stats [--days N]` | Token 成本统计 |
| `nexus audit [-n N] [-t tool]` | 查看工具调用审计日志 |
| `nexus eval agent` | 单 Agent 评估 |
| `nexus eval list` | 列出内置评估数据集 |
| `nexus eval run` | RAG 质量评估（含 context_relevancy） |
| `nexus eval trajectory [-t ID] [-d N]` | 轨迹质量评估（5 项规则检查） |
| `nexus eval ci [-d N]` | CI 模式：全量评估，不达标 exit(1) |
| `nexus eval component` | 组件级评估 |
| `nexus eval hallucination [-t ID]` | 幻觉率检测 |
| `nexus eval tool-selection` | 工具选择准确率 |
| `nexus eval coherence [-t ID]` | 多步推理连贯性 |
| `nexus eval calibrate` | 校准评估配置 |

### 知识库 & 记忆

| 命令 | 描述 |
|------|------|
| `nexus kb add <path>` | 添加文档到知识库 |
| `nexus kb list` | 查看知识库状态 |
| `nexus memory list [--limit N]` | 查看长期记忆 |
| `nexus memory clear` | 清空长期记忆 |

### 评估

| 命令 | 描述 |
|------|------|
| `nexus eval run` | RAG 质量评估（含 context_relevancy） |
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

## 项目结构

```
<repo-root>/
├── .github/                       ← CI / Release workflows
├── agentnexus/                    ← 源码包
│   ├── __main__.py                ← python -m agentnexus / PyInstaller 入口
│   ├── cli/                       ← Typer CLI 层
│   ├── agents/                    ← ReActAgent 与状态机实现
│   ├── core/                      ← 配置、LLM、调用策略
│   ├── evaluation/                ← 评估器与指标实现
│   ├── memory/                    ← STM / LTM / 对话版本控制
│   ├── observability/             ← Trace 与统计
│   ├── prompts/                   ← `.txt` 提示词模板
│   ├── rag/                       ← 检索、分块、存储、评估
│   ├── tools/                     ← 工具注册与执行
│   └── tui/                       ← Textual 终端界面
├── tests/
│   ├── conftest.py                ← 共享 fixtures
│   ├── unit/                      ← 单元测试
│   ├── integration/               ← 集成测试
│   ├── regression/                ← 回归测试
│   └── evals/                     ← 评估数据集
├── AGENTS.md                      ← 开发者指南（架构要点 + 约定）
├── CLAUDE.md                      ← AI 助手提示
├── README.md                      ← 项目说明
├── agentnexus.spec                ← PyInstaller 打包配置
├── pyproject.toml                 ← 项目配置 + 依赖声明
└── requirements.txt               ← 兼容依赖列表
```

## 配置系统

**优先级**：YAML 文件（`~/.agentnexus/config.yaml`）→ 环境变量（`AGENTNEXUS_*`）→ Pydantic 默认值

| 配置项 | 默认值 | 环境变量 | 说明 |
|--------|--------|----------|------|
| `llm_api_key` | — | `AGENTNEXUS_LLM_API_KEY` | LLM API Key（必填） |
| `llm_model_id` | `deepseek/deepseek-v4-flash` | `AGENTNEXUS_LLM_MODEL_ID` | 模型 ID（含 provider 前缀） |
| `llm_base_url` | `https://api.deepseek.com` | `AGENTNEXUS_LLM_BASE_URL` | API 地址 |
| `llm_timeout` | `60` | — | 请求超时（秒） |
| `judge_model_id` | `zhipu/glm-4.7-flash` | — | Judge 模型（不同模型家族） |
| `judge_base_url` | `https://open.bigmodel.cn/api/paas/v4/` | — | Judge 模型 API 地址 |
| `tavily_api_key` | — | `AGENTNEXUS_TAVILY_API_KEY` | 搜索引擎 API Key（可选） |
| `e2b_api_key` | — | `AGENTNEXUS_E2B_API_KEY` | 代码沙箱 API Key（可选） |
| `max_agent_steps` | `5` | — | Agent 最大循环步数 |
| `embedding_model` | `BAAI/bge-small-zh-v1.5` | — | 嵌入模型 |
| `reranker_model` | `BAAI/bge-reranker-v2-m3` | — | Reranker 模型 |
| `max_memories` | `1000` | — | LTM 最大记忆数 |
| `memory_ttl_days` | `90` | — | LTM 过期天数 |
| `trace_retention_days` | `30` | — | Trace JSONL 留存天数 |

**关键环境变量**：
- `AGENTNEXUS_HOME` — 数据根目录（默认 `~/.agentnexus`），控制 chroma/memory.db/traces 路径

### MCP 配置

MCP 为**默认关闭**的可选能力。当前首版支持：
- `stdio` transport
- `streamable_http` transport
- 导入远端 `tools`
- 主 Agent 与受控子代理共享同一套治理模型

示例 `config.yaml`：

```yaml
mcp_enabled: true
mcp_startup_timeout: 15
mcp_servers:
  - name: local-docs
    transport: stdio
    command: python
    args: [server.py]
    include_tools: [search_docs]
    allowed_agents: [react_agent, subagent_explorer]
    risk_level: medium
    require_hitl: false

  - name: remote-tools
    transport: streamable_http
    url: https://example.com/mcp
    headers:
      Authorization: Bearer ${TOKEN}
    exclude_tools: [dangerous_tool]
    allowed_agents: [react_agent]
    risk_level: high
    require_hitl: true
```

导入后的本地工具名会被规整为 `mcp_<server>__<tool>`，例如 `mcp_local_docs__search_docs`，以避免与内置工具重名。

当前限制：
- 只导入 MCP `tools`
- 不兼容旧 SSE transport
- `resources` / `prompts` 尚未接入当前 ReAct 流程
- 子代理不会自动继承全部 MCP tools，只有显式允许且满足安全策略的工具才会暴露给子代理

## Agent 可用工具

所有工具通过 `ToolRegistry` 统一注册，按风险等级分级，高风险操作需 HITL 确认。

| 工具 | 风险等级 | 说明 |
|------|----------|------|
| `web_search` | 低 | Tavily 搜索引擎，支持深度/时间/话题过滤 |
| `memory_search` | 低 | 检索长期记忆 |
| `memory_save` | 低 | 保存信息到长期记忆 |
| `grep_search` | 低 | ripgrep 代码搜索 |
| `file_read` | 低 | 读取文件（带行号） |
| `file_list` | 低 | 列出目录内容 |
| `file_write` | 中 | 写文件（覆盖需确认） |
| `python_execute` | 高 | 沙箱中执行 Python（需确认） |
| `shell_exec` | 高 | 执行 Shell 命令（需确认 + 黑名单保护） |
| `kb_search` | 低 | 检索本地知识库，返回来源与分数 |
| `subagent_run` | 低 | 委派受限子代理执行阅读、检索或受控验证任务 |
| `mcp_<server>__<tool>` | 按配置 | 从外部 MCP server 导入的工具，名称会自动加前缀并复用本地治理链路 |

## 开发

```bash
pip install -e ".[dev,eval]"

# Lint
ruff check agentnexus/ tests/

# 测试（CI 入口，覆盖 unit + integration + regression）
python -m pytest tests/ -v

# PyInstaller 打包
pyinstaller agentnexus.spec --noconfirm
```

### CI

- 触发：push/PR to `main`
- 步骤：ruff lint → pytest → eval sanity check
- 发布：push `v*` tag → PyInstaller 跨平台构建（ubuntu + windows）→ GitHub Release

## 技术栈

| 类别 | 技术 |
|------|------|
| Agent 编排 | ReActAgent（Thought→Action→Observe 循环） |
| LLM 接口 | litellm（兼容 DeepSeek / 通义千问 / GPT-4o）+ 3 次指数退避重试 |
| 向量数据库 | ChromaDB（纯本地 PersistentClient） |
| CLI 框架 | Typer + Rich + prompt_toolkit |
| TUI 框架 | Textual（Catppuccin Mocha 主题） |
| 检索 | Sentence Transformers + rank-bm25 + BGE-Reranker |
| 可观测性 | 自研 JSONL Trace（即时刷盘）+ 工具审计日志 + Token 统计 |
| 工具治理 | 自研 ToolRegistry（RBAC + Schema + 超时 + 速率 + 审计） |
| 记忆存储 | SQLite（LTM + 版本控制）+ ChromaDB（向量检索） |
| 对话版本控制 | SQLite DAG（STM 快照 + LTM 增量引用） |
| 评估 | 四层评估体系（Component / Trajectory / Hallucination / Tool-Selection / Coherence） |
| 代码沙箱 | 本地子进程隔离 + E2B 远程沙箱（可选） |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
