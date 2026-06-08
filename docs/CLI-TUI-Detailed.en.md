> **[中文](CLI-TUI-Detailed.md) | [English](CLI-TUI-Detailed.en.md)**

# 🖥 CLI and TUI Modules (Detailed)

## Overview

- **CLI**: Built on **Typer** framework, root command `nexus`, provides 6 top-level commands + 7 subcommand groups with 40+ entry points
- **TUI**: Built on **Textual** framework with **Catppuccin Mocha** theme, provides terminal-native chat interface

## CLI Command Structure

### Top-Level Commands

| Command | File | Description |
| --- | --- | --- |
| `nexus version` | `cli/__init__.py` | Show version (v0.1.0) |
| `nexus config` | `cli/config.py` | View/modify configuration |
| `nexus init` | `cli/config.py` | First-time setup wizard |
| `nexus serve` | `cli/serve_cmd.py` | Start HTTP/WebSocket API server |
| `nexus health` | `cli/health_cmd.py` | System health check |
| `nexus stats` | `cli/stats.py` | Token cost statistics |
| `nexus audit` | `cli/audit.py` | Tool call audit log |
| `nexus alerts` | `cli/alerts_cmd.py` | Alert history |
| `nexus sessions` | `cli/sessions.py` | Session management |
| `nexus tui` | `cli/tui_cmd.py` | Launch terminal chat UI |

### Subcommand Groups

| Group | Commands | Description |
| --- | --- | --- |
| `nexus kb` | add, list, search | Knowledge base management |
| `nexus wiki` | init, ingest, query, lint, stats, calibrate, full-check, review | Hybrid Wiki + RAG system |
| `nexus memory` | list, clear | Long-term memory management |
| `nexus logs` | list, view | Trace log viewer |
| `nexus skill` | list, init, validate, use, reset, status | Skill/workflow management |
| `nexus codegraph` | build, search, callers, callees, inherits, imports, context, stats, verify | Code knowledge graph |
| `nexus eval` | 14 direct commands + task/suite/transcript subgroups | Evaluation system (most complex) |

## TUI Architecture

### Screen Layout

```
┌──────────────────────────────────────────────────────────┐
│ Top Bar (workspace/branch/model/hotkeys)                  │
├─────────────────────────────────────────┬────────────────┤
│                                         │                │
│           Chat Area                     │  Side Panel    │
│         (Message Stream)                │  (Info Panel)  │
│                                         │                │
│                                         │  ┌ Model       │
│                                         │  ├ Task Timeline│
│                                         │  ├ Todo List   │
│                                         │  ├ Tools       │
│                                         │  ├ MCP         │
│                                         │  ├ Skill       │
│                                         │  └ Session     │
├─────────────────────────────────────────┴────────────────┤
│ Input Bar (> input field)                                 │
├──────────────────────────────────────────────────────────┤
│ HUD (model | context bar | tokens | version | workdir)   │
└──────────────────────────────────────────────────────────┘
```

### Widget Components

| Widget | File | Description |
| --- | --- | --- |
| `ChatScreen` | `tui/screens/chat.py` | Main screen, all interaction logic (1757+ lines) |
| `InputBar` | `tui/widgets/input_bar.py` | Bottom input area, sends `AppSubmit` message |
| `ChatMessage` | `tui/widgets/message.py` | Chat message rendering with code block support |
| `ToolCall` | `tui/widgets/message.py` | Tool call display (warm orange border) |
| `HUD` | `tui/widgets/hud.py` | Bottom status bar with context window `█░` progress |
| `SidePanel` | `tui/widgets/side_panel.py` | Right info panel (7 sections) |
| `ConfirmDialog` | `tui/widgets/confirm_dialog.py` | HITL confirmation modal |

### Built-in Slash Commands

| Command | Description |
| --- | --- |
| `/help` | Show help |
| `/clear [--all]` | Clear screen (clears checkpoints with `--all`) |
| `/undo` / `/redo` | Checkpoint navigation |
| `/compact [instructions]` | Compress conversation context |
| `/sessions` | List historical sessions |
| `/skill status/list/use/...` | Skill management |
| `/mcp status/tools/...` | MCP management |
| `/exit` | Exit TUI |

## Design Patterns

| Pattern | Application |
| --- | --- |
| **Typer Sub-app Nesting** | eval module 3 levels deep (eval > task/suite > baseline) |
| **Deferred Import** | CLI commands use lazy `from ... import` inside functions |
| **Textual Message Bus** | Components communicate via `Message` system |
| **ChatService Abstraction** | ChatScreen wraps agent interaction through ChatService |
| **Reactive Updates** | SidePanel uses `_refresh_section()` for per-section updates |
