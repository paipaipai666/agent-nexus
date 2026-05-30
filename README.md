> **[中文](README.zh.md) | [English](README.md)**

# AgentNexus — ReAct Single-Agent Task Collaboration CLI

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

AgentNexus is a production-grade **ReAct (Thought→Action→Observe) single-agent** CLI tool that runs entirely locally. It features an FSM-driven safety loop, 17 built-in tools, and a three-layer storage stack (ChromaDB/SQLite/JSONL).

```
User → CLI Layer (Typer+Rich) → ReActAgent(FSM+3-tier LLM Strategy)
          → ToolRegistry (7 Security Gates) → Tool Execution Layer (8 Providers + MCP)
          → Local: ChromaDB | SQLite | JSONL
```

## Features

| Capability | Description |
|------|-----------|
| Conversation & Tasks | TUI interface with ReAct loop: plan→execute→observe |
| Local Memory | STM compression pyramid + LTM (SQLite+ChromaDB, score-based eviction) |
| Knowledge Base RAG | Hybrid retrieval (dense+sparse+RRF+rerank), 8 file formats |
| Security Sandbox | E2B cloud → native(bubblewrap/Seatbelt) → Docker → local fallback |
| Tool Audit | 7 security gates (RBAC/Schema/Rate-limit/Timeout/Risk/HITL/Audit) |
| Observability | JSONL Trace + Token cost statistics |
| Evaluation | 8 evaluators (Agent/Trajectory/Hallucination/RAG/Code, etc.) |
| Skill System | Reusable workflow templates, TF-IDF auto-routing |
| MCP Integration | Import external tools via stdio/HTTP, full governance |
| Sub-agent Delegation | Agent-in-Agent isolated subtask execution |
| Code Knowledge Graph | Semantic search, relationship queries, context retrieval |

## Installation

```bash
pip install -e ".[dev,eval]"   # Python 3.11+
```

## Quick Start

```bash
nexus init                      # Interactive config
nexus tui                        # TUI chat
nexus kb add ./docs              # Add to knowledge base
nexus stats --days 7             # Token cost stats
nexus eval agent --days 1        # Agent quality eval
nexus codegraph build            # Build code knowledge graph
```

## Documentation

| Document | Content |
|------|------|
| [🏠 Wiki Home](wiki/Home.en.md) | Architecture diagram, core capabilities |
| [🤖 ReAct Agent](wiki/ReAct-Agent.en.md) | FSM state machine, 3-tier LLM strategy, JSON fault tolerance |
| [🔧 Tool Governance](wiki/Tool-Governance.en.md) | 7 security gates, 17 tool parameter tables |
| [⚡ Code Execution](wiki/Code-Execution.en.md) | Sandbox degradation chain, shell blacklist, sub-agents |
| [🧠 Memory System](wiki/Memory-System.en.md) | STM/LTM architecture, compression pyramid, score eviction |
| [📚 RAG System](wiki/RAG-System.en.md) | Hybrid retrieval pipeline, dual ChromaDB clients |
| [⚙ Configuration](wiki/Configuration.en.md) | All configuration items reference |
| [⌨ Commands](wiki/Commands.en.md) | 40 commands reference |
| [📊 Evaluation](wiki/Evaluation.en.md) | 8 evaluators, RAG metrics |
| [🔒 Security](wiki/Security.en.md) | PII masking, sandbox escape protection |
| [🎯 Skill System](wiki/Skill-System.en.md) | Skill discovery, routing, workflow execution |
| [🔌 MCP Integration](wiki/MCP-Integration.en.md) | External tool import, governance fusion |
| [📈 Observability](wiki/Observability.en.md) | Trace system, token statistics, audit logs |
| [📝 Prompt System](wiki/Prompt-System.en.md) | Template categories, variable injection |
| [🛠 Development](wiki/Development.en.md) | Environment setup, testing, CI pipeline |
| [🤝 Contributing](wiki/Contributing.en.md) | Issue/PR guidelines, testing requirements |

## License

[MIT](LICENSE) © 2026 AgentNexus
