> **[中文](Architecture.md) | [English](Architecture.en.md)**

# 🏗 Overall Architecture

## Layered Architecture

```text
┌───────────────────────────────────────────────────────────┐
│              CLI Layer (Typer + Rich)                       │
│  6 top-level commands + 7 subcommand groups = 40+          │
│  nexus init / config / tui / stats / audit / ver           │
│  nexus kb / wiki / memory / logs / eval / skill / codegraph│
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│              Service Facade Layer                           │
│  ChatService  │  SkillService  │  AppServices               │
│  Session mgmt │  Skill routing │  Service composition       │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│           ReActAgent (FSM Driven)                           │
│  16 states × 25 transitions                                 │
│  CallingStrategy 3-tier: Native → JSON → Prompt             │
│  AgentLLM (litellm streaming, 3x exponential backoff)       │
│  Batch sequential execution, max_steps hard limit           │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│         ToolRegistry Governance Gateway                     │
│         (7 Security Gates)                                  │
│  RBAC → Schema → Rate-limit → Timeout                      │
│  → Risk → HITL → Audit                                     │
│  18 built-in tools + MCP dynamic import + sub-agent         │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│              Local Infrastructure Layer                      │
│  ChromaDB (Vectors)  SQLite (Relational)                    │
│  JSONL (Traces)                                             │
│  SentenceTransformers + BM25 + BGE-Reranker                 │
│  E2B / bubblewrap / Docker / Local sandbox                  │
│  Playwright (Browser Automation)  OS Accessibility (Desktop)│
└───────────────────────────────────────────────────────────┘
```

## Project Structure

```text
agentnexus/
├── __main__.py              ── python -m entry point
├── app/runtime.py            ── AppRuntime dependency assembly
├── cli/                      ── Typer CLI layer
├── agents/                   ── ReActAgent + FSM
├── core/                     ── Settings + LLM
├── codegraph/                ── Code knowledge graph (AST parsing/semantic search)
├── evaluation/               ── 8 evaluators
├── extensions/               ── plugin system
├── memory/                   ── STM/LTM/version control/compaction/reflection/offload/projection/extraction
├── observability/            ── Trace + Token statistics
├── prompts/                  ── prompt templates
├── rag/                      ── ChromaDB client/retrieval/chunking
├── server/                   ── FastAPI API server
├── services/                 ── service facade
├── skills/                   ── Skill discovery/routing/runtime
├── storage/                  ── storage abstraction layer
├── tools/                    ── registry/providers/MCP/browser
│   └── computer_use/         ── desktop automation (OS accessibility APIs)
├── tui/                      ── Textual interface
└── wiki/                     ── hybrid Wiki + RAG knowledge management
```

## Tool Providers

The system uses `ToolProvider` protocol with 11 providers registered in order:

| Provider | Tools | Description |
| --- | --- | --- |
| `MemoryToolProvider` | `memory_search`, `memory_save` | Long-term memory search and save |
| `SearchToolProvider` | `grep_search`, `web_search`, `web_fetch`, `kb_search` | Search tools |
| `FilesystemToolProvider` | `file_read`, `file_list`, `file_write` | File operations |
| `ExecutionToolProvider` | `python_execute`, `shell_exec` | Code execution (sandboxed) |
| `SubagentToolProvider` | `subagent_run` | Sub-agent delegation |
| `McpBridgeToolProvider` | MCP dynamic import | External tool integration |
| `TodoToolProvider` | `todo_add`, `todo_update`, `todo_list` | Todo list management |
| `CodeGraphToolProvider` | `codegraph_search`, `codegraph_relations`, `codegraph_context` | Code knowledge graph |
| `BrowserToolProvider` | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_read`, `browser_screenshot`, `browser_evaluate`, `browser_wait`, `browser_scroll`, `browser_scroll_to` | Browser automation |
| `ComputerUseToolProvider` | `computer_snapshot`, `computer_list_windows`, `computer_switch_window`, `computer_launch`, `computer_click`, `computer_type`, `computer_key`, `computer_select`, `computer_toggle`, `computer_scroll` | Desktop automation (OS accessibility APIs) |

## Service Startup Sequence

`AppRuntime.build()` assembles all components in this order:

1. Load `Settings` (Pydantic lazy singleton)
2. Create `AgentLLM` + `ToolExecutor` + `ConfirmBridge`
3. Initialize `MCPToolManager` (if `mcp_enabled=True`)
4. Load `ExtensionManager`
5. `register_all_tools()` — register 11 providers + MCP
6. Create `MemoryManager` + `ConversationVersionManager`
7. Create `ReActAgent`
8. `SkillRegistry.discover()` — scan skill directories
9. Create `SkillService`
10. Configure trace output directory
11. Assemble `AppServices` (Chat/Config/Eval/KB/Skill)
12. Return `AppRuntime` instance

> See [ReAct Agent](ReAct-Agent.en.md) for FSM details, [Tool Governance](Tool-Governance.en.md) for the 7 security gates, [Browser Automation](Browser-Automation.en.md) for Playwright integration.

## API Routes

The server exposes the following REST routes (FastAPI, prefix `/api`):

| Route Prefix | Module | Description |
| --- | --- | --- |
| `/api` | chat, alerts | Chat interface, alerts |
| `/api/kb` | knowledge | Knowledge base management |
| `/api/memory` | memory | Memory management |
| `/api/skills` | skills | Skill management |
| `/api/stats` | stats | Statistics |
| `/api/config` | config | Configuration |
| `/api/audit` | audit | Audit logs |
| `/api/codegraph` | codegraph | Code knowledge graph |
| `/api/eval` | eval_routes | RAG evaluation |
| `/api/mcp` | mcp | MCP tool management |
| `/api/version` | version | Version info |
| `/api/runtime` | runtime | Runtime status |
| `/api/wiki` | wiki | Hybrid Wiki + RAG knowledge management |
