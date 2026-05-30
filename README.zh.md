> **[中文](README.zh.md) | [English](README.md)**

# AgentNexus — ReAct 单智能体任务协同 CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

AgentNexus 是一个生产级 **ReAct（Thought→Action→Observe）单智能体** CLI 工具，纯本地运行。FSM 驱动的安全循环 + 17 种内置工具 + ChromaDB/SQLite/JSONL 三层存储。

```
用户 → CLI 层 (Typer+Rich) → ReActAgent(FSM+三级LLM策略)
          → ToolRegistry(7道安全关卡) → 工具执行层(8 Provider+MCP)
          → 本地: ChromaDB | SQLite | JSONL
```

## 功能

| 能力 | 说明 |
|------|------|
| 对话与任务执行 | TUI 交互界面，ReAct 循环自动规划→执行→观察 |
| 本地记忆 | 短期（STM 压缩金字塔）+ 长期（SQLite+ChromaDB，评分驱逐） |
| 知识库 RAG | 混合检索（稠密+稀疏+RRF+重排序），8 种文件格式导入 |
| 安全沙箱 | E2B 云端 → 原生(bubblewrap/Seatbelt) → Docker → 本地兜底 |
| 工具审计 | 7 道关卡（RBAC/Schema/限流/超时/风险/HITL/日志） |
| 可观测性 | JSONL Trace + Token 成本统计 |
| 评估体系 | 8 个评估器（Agent/Trajectory/幻觉/RAG/代码等） |
| 技能系统 | 可复用工作流模板，TF-IDF 自动路由 |
| MCP 集成 | stdio/HTTP 导入外部工具，全量治理 |
| 子代理委派 | Agent-in-Agent 隔离执行子任务 |
| 代码知识图谱 | 语义搜索、关系查询、上下文检索 |

## 安装

```bash
pip install -e ".[dev,eval]"   # Python 3.11+
```

## 快速开始

```bash
nexus init                      # 交互式配置
nexus tui                        # TUI 对话
nexus kb add ./docs              # 添加知识库
nexus stats --days 7             # Token 成本统计
nexus eval agent --days 1        # Agent 质量评估
nexus codegraph build            # 构建代码知识图谱
```

## 文档

| 文档 | 内容 |
|------|------|
| [🏠 Wiki 首页](wiki/Home.md) | 架构图、核心能力表格 |
| [🤖 ReAct Agent](wiki/ReAct-Agent.md) | FSM 状态机、三级 LLM 策略、JSON 容错 |
| [🔧 工具治理](wiki/Tool-Governance.md) | 7 道关卡、17 个工具参数表 |
| [⚡ 代码执行](wiki/Code-Execution.md) | 沙箱降级链、Shell 黑名单、子代理 |
| [🧠 记忆系统](wiki/Memory-System.md) | STM/LTM 架构、压缩金字塔、评分驱逐 |
| [📚 RAG 检索](wiki/RAG-System.md) | 混合检索管线、ChromaDB 双客户端 |
| [⚙ 配置参考](wiki/Configuration.md) | 全部配置项速查 |
| [⌨ 命令参考](wiki/Commands.md) | 40 个命令速查 |
| [📊 评估体系](wiki/Evaluation.md) | 8 个评估器、RAG 指标 |
| [🔒 安全模型](wiki/Security.md) | PII 脱敏、沙箱逃逸防护 |
| [🎯 技能系统](wiki/Skill-System.md) | Skill 发现、路由、工作流执行 |
| [🔌 MCP 集成](wiki/MCP-Integration.md) | 外部工具导入、治理融合 |
| [📈 可观测性](wiki/Observability.md) | Trace 系统、Token 统计、审计日志 |
| [📝 提示词系统](wiki/Prompt-System.md) | 模板分类、变量注入 |
| [🛠 开发指南](wiki/Development.md) | 环境搭建、测试、CI 流程 |
| [🤝 贡献指南](wiki/Contributing.md) | Issue/PR 规范、测试要求 |

## 许可

[MIT](LICENSE) © 2026 AgentNexus
