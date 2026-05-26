> **[中文](Code-Execution.md) | [English](Code-Execution.en.md)**

# ⚡ Code Execution & Security

## Sandbox Degradation Chain

`python_execute` and `shell_exec` share the same backend selection logic:

```
code_execution_backend (default "auto")
    │
    ├── "disabled" → [blocked]
    │
    ├── "e2b" → E2B cloud (requires AGENTNEXUS_E2B_API_KEY)
    │
    ├── "native" → OS native
    │     Linux: bubblewrap (bwrap --seccomp --ro-bind)
    │     macOS: sandbox-exec / Seatbelt
    │     Windows: unavailable, degrades
    │
    ├── "docker" → docker run --rm
    │     --network none --cpus 1 --memory 256m
    │     --read-only --security-opt=no-new-privileges
    │
    └── "local_unsafe" → sys.executable -c <code>
          (requires code_execution_allow_unsafe_local=true)
```

`auto` mode tries E2B → Native → Docker → local fallback (with strong warnings).

## Shell 3-Layer Blacklist

1. **General**: `shutdown`, `rm -rf /`, encoded PowerShell obfuscation
2. **Platform-specific**: Windows blocks `format`/`diskpart`/`reg add`; Unix blocks `mkfs`/`dd`/`chmod 777 /`/fork bombs
3. **User-defined**: `shell_blacklist` config, NFKC normalized to prevent Unicode bypass

## Sub-agent Delegation

`subagent_run` creates an isolated Agent-in-Agent:

| Role | Available Tools |
|------|----------|
| **explorer** (unified mapping) | `web_search`, `grep_search`, `kb_search`, `file_read`, `file_list`, `memory_search`, `python_execute` |
| **executor** | `python_execute`, `file_read`, `file_list`, `grep_search` |

- Clones parent Agent's LLM config
- Retries with explorer role +1 max_steps on first failure
- Returns `{"status", "role", "answer", "steps_used", "allowed_tools"}`

## MCP Security

- Health check: background coroutine periodic ping, exponential backoff reconnect
- Sub-agent isolation: `allowed_agents` controls MCP tool exposure scope
- Full governance: MCP tools automatically enjoy all 7 Registry security gates
