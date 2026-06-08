> **[中文](Getting-Started.md) | [English](Getting-Started.en.md)**

# 🚀 Getting Started

## Installation

```bash
pip install -e ".[dev,eval]"   # Python 3.11+
```

Optional runtimes:
- `sentence-transformers` — local embedding model (otherwise falls back to hash bag-of-words)
- `pytesseract` — PDF OCR
- `scipy` — evaluation calibration

## Initialization

```bash
nexus init
```

Interactive input:
- **LLM API Key** (required)
- **Model ID** (default `deepseek/deepseek-v4-flash`)
- **Base URL** (default `https://api.deepseek.com`)

Config is written to `~/.agentnexus/config.yaml`.

## Launch TUI

```bash
nexus tui
```

Shortcuts: `Ctrl+Q` quit, `Ctrl+L` clear screen, `Tab` focus switch.

Resume a previous session:

```bash
nexus --continue                   # Resume most recent TUI session
nexus --continue <session_id>      # Resume a specific session
```

## Start API Server

```bash
nexus serve                        # Default port 8000
nexus serve --port 3000            # Custom port
nexus serve --no-auth              # Disable authentication
```

Starts the HTTP/WebSocket API server for desktop GUI clients.

## Wiki Quick Start

```bash
nexus wiki init my-project         # Initialize wiki namespace
nexus wiki ingest ./docs -n my-project  # Ingest documents
nexus wiki query "your question" -n my-project  # Query wiki
nexus wiki stats -n my-project     # View health statistics
```

Wiki provides confidence-based RAG queries, document ingestion, health checks, and a review workflow. See the `nexus wiki` section in the [Commands Reference](Commands.en.md) for details.

## Basic Workflow

```bash
nexus tui                           # TUI chat
nexus kb add ./docs                 # Add to knowledge base
nexus kb search "query" --top-k 5   # Search knowledge base
nexus stats --days 7                # View costs
nexus eval agent --days 1           # Evaluate Agent quality
nexus audit -n 10                   # Audit recent tool calls
```

## View Configuration

```bash
nexus config                        # List all config
nexus config --set max_agent_steps --value 10
```

## Next Steps

- [⌨ Commands](Commands.en.md)
- [⚙ Configuration](Configuration.en.md)
- [🤖 Agent Engine](ReAct-Agent.en.md)
