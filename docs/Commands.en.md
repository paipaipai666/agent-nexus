> **[中文](Commands.md) | [English](Commands.en.md)**

# ⌨ Commands Reference

50+ command entry points, 8 top-level + 7 subcommand groups.

## Global Behavior

- Auto-loads `~/.agentnexus/config.yaml`
- Errors output to stderr (Rich formatted), exit code 0/1
- Interactive mode prompts HITL confirmation; `-n` skips it

## Top-Level Commands

| Command | Description |
|------|------|
| `nexus init` | First-time interactive initialization (LLM Key/Model/URL) |
| `nexus config` | View/set config (`--set <key> --value <val>`) |
| `nexus version` | Show version |
| `nexus tui` | Launch Textual TUI chat interface |
| `nexus --continue [session_id]` | Resume a previous TUI session |
| `nexus serve [--port N] [--host H] [--no-auth]` | Start HTTP/WebSocket API server for desktop GUI |
| `nexus stats [--days N]` | Token cost statistics + task-level metrics |
| `nexus audit [-n N] [-t tool]` | Tool audit log |
| `nexus health` | System health checks (LLM/MCP/Memory/Disk) |
| `nexus alerts [--days N] [-s severity]` | Alert history |

## Knowledge Base `nexus kb`

| Command | Description |
|------|------|
| `kb add <path>` | Add document (PDF/MD/TXT/HTML/JSON/DOCX/XLSX) |
| `kb list` | Knowledge base status |
| `kb search <query> [--top-k] [--view] [--source] [--format] [--section] [--page] [--block-type] [--has-code/--no-code] [--has-list/--no-list] [--heading-depth]` | Hybrid search |

## Memory `nexus memory`

| Command | Description |
|------|------|
| `memory list [--limit N]` | View long-term memory |
| `memory clear` | Clear long-term memory |

## Logs `nexus logs`

| Command | Description |
|------|------|
| `logs list [--days N]` | List historical traces |
| `logs view --trace-id <id>` | View trace span tree |

## Evaluation `nexus eval`

| Command | Description |
|------|------|
| `eval agent [--days N]` | Agent execution quality |
| `eval trajectory [-t ID] [-d N]` | Trajectory quality |
| `eval component` | Component decomposition evaluation |
| `eval hallucination [-t ID]` | Hallucination detection |
| `eval tool-selection` | Tool selection accuracy |
| `eval coherence [-t ID]` | Multi-step reasoning coherence |
| `eval list` | List evaluation datasets |
| `eval run [--ci] [--top-k N] [--dataset ...]` | RAG quality evaluation |
| `eval history` | Historical evaluation reports |
| `eval compare -b <baseline> -c <candidate>` | Compare two evaluations |
| `eval ci [-d N]` | CI mode |
| `eval calibrate [-o <path>] [-s <score_file>]` | Judge calibration |
| `eval humaneval [--dataset ...] [-t ID]` | HumanEval code generation |
| `eval swe-bench --dataset <path>` | SWE-bench |

## Skill `nexus skill`

| Command | Description |
|------|------|
| `skill list` | List all skills |
| `skill init <target> [--display-name] [--force] [--workflow]` | Create skill template |
| `skill validate [<target>]` | Validate skill structure |
| `skill use <target>` | Set default skill |
| `skill reset` | Clear default skill |
| `skill status` | Current skill status |

## Code Graph `nexus codegraph`

| Command | Description |
|------|------|
| `codegraph build [--force] [--path]` | Build/update code graph |
| `codegraph search <query> [--kind] [--limit]` | Semantic search for code entities |
| `codegraph callers <symbol> [--depth]` | Find who calls a specific entity |
| `codegraph callees <symbol> [--depth]` | Find what a specific entity calls |
| `codegraph inherits <cls>` | View inheritance tree |
| `codegraph imports <module>` | View import relationships |
| `codegraph context <symbol>` | Get entity full context |
| `codegraph stats` | Display graph statistics |
| `codegraph verify [--fix]` | Consistency diagnostics |

## Wiki `nexus wiki`

Knowledge base management with confidence-based routing for RAG queries and health checks.

| Command | Description |
|------|------|
| `wiki init <namespace>` | Initialize wiki for a RAG namespace |
| `wiki ingest <source> [-n namespace] [-t type]` | Ingest source document into wiki |
| `wiki query <question> [-n namespace] [-r] [-k N]` | Query wiki with confidence-based routing |
| `wiki lint [-n namespace]` | Run wiki health checks |
| `wiki calibrate <sample_file>` | Run threshold calibration |
| `wiki full-check [-n namespace]` | Run full wiki health check |
| `wiki stats [-n namespace]` | Show wiki health statistics |

### Wiki Review Sub-commands

| Command | Description |
|------|------|
| `wiki review list [-s status] [-l N]` | List review queue items |
| `wiki review resolve <item_id>` | Resolve a review item |
| `wiki review process` | Process overdue review items |
