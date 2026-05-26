> **[中文](Commands.md) | [English](Commands.en.md)**

# ⌨ Commands Reference

31 command entry points, 6 top-level + 5 subcommand groups.

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
| `nexus stats [--days N]` | Token cost statistics |
| `nexus audit [-n N] [-t tool]` | Tool audit log |

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
