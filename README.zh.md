> **[中文](README.zh.md) | [English](README.md)**

# AgentNexus

**生产级、纯本地 AI Agent — FSM 驱动安全循环 + 浏览器自动化 + 桌面自动化 + 269 个测试文件。**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00C853)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/AgentNexus/AgentNexus/ci.yml?label=CI&logo=github)](https://github.com/AgentNexus/AgentNexus/actions)
[![Tests](https://img.shields.io/badge/Tests-269%20files-00C853)](tests/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/AgentNexus/AgentNexus)

AgentNexus 是一个 **ReAct（Thought→Action→Observe）单智能体** CLI 工具，完全运行在本地。无云端依赖，无数据泄露。向量、记忆、Trace 日志全部留在你的设备上。

与纯 Prompt 驱动的 Agent 不同，AgentNexus 使用**有限状态机**（16 个状态、25 条转移规则）管控每个推理循环 —— 让 Agent 行为可确定、可审计。

```text
用户 → CLI/TUI/桌面端 → ReAct Agent (FSM + 三级 LLM 策略)
       → 工具网关 (7 道安全关卡) → 18 种内置工具 + MCP
       → 本地存储: ChromaDB | SQLite | JSONL
```

## 为什么选择 AgentNexus？

| 维度 | AgentNexus | 典型 Agent 工具 |
| --- | --- | --- |
| **数据隐私** | 100% 本地 — 向量、记忆、Trace 全部在设备上 | 依赖云端，数据离开本机 |
| **安全模型** | 7 层工具治理（RBAC、Schema、限流、超时、风险、HITL、审计） | 基础或无工具级安全 |
| **Agent 控制** | FSM 驱动循环 — 16 状态、25 条确定性转移 | Prompt 驱动，行为不可预测 |
| **代码沙箱** | 4 级降级：E2B → bubblewrap/Seatbelt → Docker → 本地 | 单一沙箱或无沙箱 |
| **安全测试** | 213 项专项测试，覆盖 8 个类别 | 临时或无安全测试 |
| **可观测性** | 6 层体系：Trace + 漂移检测 + 故障归因 + 告警 + 健康检查 + 改进闭环 | 基础日志 |
| **评估体系** | 8 个内置评估器（Agent、轨迹、幻觉、RAG、代码...） | 手动或无评估 |
| **交互方式** | TUI + 桌面端 (Electron) + API Server | 单一界面 |

## 功能

| 能力 | 说明 |
| --- | --- |
| 🗣️ **对话与任务执行** | TUI 交互界面，ReAct 循环自动规划→执行→观察 |
| 🧠 **本地记忆** | 短期（STM 压缩金字塔）+ 长期（SQLite+ChromaDB，评分驱逐） |
| 📚 **知识库 RAG** | 混合检索（稠密+稀疏+RRF+重排序），8 种文件格式导入 |
| 🌐 **浏览器自动化** | Playwright 驱动的浏览器控制，支持无障碍树和 CDP 模式 |
| 🖥️ **桌面自动化** | OS 级无障碍 API 驱动：快照、点击、输入、键盘、窗口管理 |
| 📖 **Wiki 系统** | 混合 Wiki + RAG 知识管理，Karpathy 的 LLM Wiki 模式，机械验证，置信度路由 |
| 🔒 **安全沙箱** | E2B 云端 → 原生 (bubblewrap/Seatbelt) → Docker → 本地兜底 |
| 🛡️ **工具审计** | 7 道关卡（RBAC/Schema/限流/超时/风险/HITL/日志） |
| 📈 **可观测性** | 6 层体系：JSONL Trace + 漂移检测 + 工具故障归因 + 告警管道 + 健康检查 + 改进闭环 |
| 📊 **评估体系** | 8 个评估器（Agent/Trajectory/幻觉/RAG/代码等） |
| 🎭 **Persona 系统** | Agent 身份、行为原则（立场/自主权/问责）、用户任务地图 |
| 🎯 **技能系统** | 可复用工作流模板，TF-IDF + 学习型重排序路由 |
| 🔌 **MCP 集成** | stdio/HTTP 导入外部工具，全量治理 |
| 🤖 **子代理委派** | Agent-in-Agent 隔离执行子任务 |
| 🕸️ **代码知识图谱** | 语义搜索、关系查询、上下文检索 |

## 快速开始

```bash
# 安装
pip install -e ".[dev,eval]"

# 初始化（交互式配置）
nexus init

# 开始对话
nexus tui
```

就这么简单。本地模型无需 API Key —— `nexus init` 时配置你的 LLM 提供商即可。

### 更多命令

```bash
nexus kb add ./docs              # 添加文档到知识库
nexus wiki init default          # 为 RAG 命名空间初始化 Wiki
nexus wiki ingest ./doc.md       # 将源文档导入 Wiki
nexus wiki query "什么是 X？"     # 置信度路由查询 Wiki
nexus serve                      # 启动 HTTP/WebSocket API 服务器（桌面端 GUI）
nexus stats --days 7             # 查看 Token 成本和任务指标
nexus health                     # 运行系统健康检查
nexus alerts --days 7            # 查看告警历史
nexus audit --limit 20           # 查看工具审计日志
nexus eval agent --days 1        # 运行 Agent 质量评估
nexus codegraph build            # 构建代码知识图谱
```

## 文档

| 文档 | 内容 |
| --- | --- |
| 🚀 [快速开始](docs/Getting-Started.md) | 安装、首次运行、快速导览 |
| 🏠 [Wiki 首页](docs/Home.md) | 架构图、核心能力表格 |
| 🏗️ [系统架构](docs/Architecture.md) | 系统架构、模块边界、数据流 |
| 🤖 [ReAct Agent](docs/ReAct-Agent.md) | FSM 状态机、三级 LLM 策略、JSON 容错 |
| 🔧 [工具治理](docs/Tool-Governance.md) | 7 道关卡、18 个工具参数表 |
| 🌐 [浏览器自动化](docs/Browser-Automation.md) | Playwright 集成、CDP 模式、无障碍树 |
| ⚡ [代码执行](docs/Code-Execution.md) | 沙箱降级链、Shell 黑名单、子代理 |
| 🧠 [记忆系统](docs/Memory-System.md) | STM/LTM 架构、压缩金字塔、评分驱逐 |
| 📚 [RAG 检索](docs/RAG-System.md) | 混合检索管线、ChromaDB 双客户端 |
| 🖥️ [桌面自动化](docs/Computer-Use.md) | OS 级无障碍自动化，Windows/Linux/macOS |
| 📖 [Wiki 系统](docs/Wiki-System.md) | 混合 Wiki + RAG、Karpathy 模式、置信度路由 |
| 📖 [Wiki 系统（详细）](docs/Wiki-System-Detailed.md) | Wiki 内部机制、验证管线、路由逻辑 |
| 🎭 [Persona 系统](docs/Persona.md) | Agent 身份、行为原则、任务地图 |
| ⌨️ [CLI & TUI](docs/CLI-TUI-Detailed.md) | CLI 命令、TUI 界面、快捷键 |
| 🖥️ [应用运行时](docs/App-Runtime.md) | 桌面应用运行时、Electron 集成 |
| ⚙️ [配置参考](docs/Configuration.md) | 全部配置项速查 |
| ⌨️ [命令参考](docs/Commands.md) | 40+ 个命令速查 |
| 📊 [评估体系](docs/Evaluation.md) | 8 个评估器、RAG 指标 |
| 🔒 [安全模型](docs/Security.md) | PII 脱敏、沙箱逃逸防护 |
| 🎯 [技能系统](docs/Skill-System.md) | Skill 发现、路由、工作流执行 |
| 🔌 [MCP 集成](docs/MCP-Integration.md) | 外部工具导入、治理融合 |
| 📈 [可观测性](docs/Observability.md) | 6 层可观测性：Trace、漂移检测、故障归因、告警、健康检查、改进闭环 |
| 📝 [提示词系统](docs/Prompt-System.md) | 模板分类、变量注入 |
| 🛠 [开发指南](docs/Development.md) | 环境搭建、测试、CI 流程 |
| 🤝 [贡献指南](docs/Contributing.md) | Issue/PR 规范、测试要求 |

## 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                        AgentNexus                                │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  CLI/TUI     │  桌面端       │  API Server  │  MCP Client        │
│  (Typer)     │  (Electron)  │  (FastAPI)   │  (stdio/HTTP)      │
├──────────────┴──────────────┴──────────────┴────────────────────┤
│                    ReAct Agent 核心                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐        │
│  │ FSM      │  │ LLM Strategy │  │ Prompt Builder     │        │
│  │ (16→25)  │  │ (三级)       │  │ (模板)             │        │
│  └──────────┘  └──────────────┘  └────────────────────┘        │
├─────────────────────────────────────────────────────────────────┤
│               工具网关 (7 道安全关卡)                            │
│  RBAC → Schema → 限流 → 超时 → 风险 → HITL → 审计               │
├─────────────────────────────────────────────────────────────────┤
│                     工具执行层                                   │
│  code_executor · shell · file_ops · web_search · kb_search      │
│  memory_save · subagent · grep_search · web_fetch · browser     │
│  computer_* · wiki · codegraph_* · todo · ...                   │
├──────────┬──────────────┬───────────────────────────────────────┤
│ ChromaDB │   SQLite     │  JSONL Trace 日志                     │
│ (向量)   │  (关系型)    │  (可观测性)                           │
└──────────┴──────────────┴───────────────────────────────────────┘
```

## 技术栈

**后端**: Python 3.11+ · litellm · Pydantic · Typer+Rich · FastAPI · ChromaDB · sentence-transformers · Playwright

**桌面端**: Electron · React 19 · TypeScript · Vite · TailwindCSS · Zustand

**测试**: pytest · 193 单元测试 · 16 安全测试 · 40 性能基准测试 · 10 集成测试 · 9 回归测试

## 贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献指南。欢迎提交 Bug 报告、功能请求和 Pull Request。

## 许可

[MIT](LICENSE) © 2026 AgentNexus
