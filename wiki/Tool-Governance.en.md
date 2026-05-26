> **[中文](Tool-Governance.md) | [English](Tool-Governance.en.md)**

# 🔧 Tool Governance System

All tool invocations pass through `ToolRegistry.invoke()`, executing 7 security gates sequentially.

## Seven Security Gates

### ① RBAC
`ToolMeta.allowed_agents` controls which callers are allowed. `"*"` is a wildcard. High-risk tools are restricted to whitelisted agents only.

### ② Schema Validation
JSON Schema validates parameter structure. Validator is compiled and cached at registration time.

### ③ Rate Limiting
Sliding window counter (60s). `python_execute` and `shell_exec` are not rate-limited.

### ④ Timeout Control
`ThreadPoolExecutor(max_workers=4)` + `future.result(timeout=N)`.

### ⑤ Risk Classification
| Level | Behavior |
|------|------|
| LOW | Pass through |
| MEDIUM | Requires HITL confirmation |
| HIGH | Requires confirmation + sandbox |

### ⑥ HITL Confirmation
`ConfirmBridge` is pluggable: TUI dialog / stdin / auto-approve.

### ⑦ Audit Logging
Each call records `AuditEntry{tool, caller, params(masked), duration, hitl, error}`.

## Tool Registration

`ToolProvider` protocol, 6 providers registered in order:

```python
MemoryToolProvider    → memory_search, memory_save
SearchToolProvider    → grep_search, web_search, kb_search
FilesystemToolProvider → file_read, file_list, file_write
ExecutionToolProvider  → python_execute, shell_exec
SubagentToolProvider   → subagent_run
McpBridgeToolProvider  → MCP dynamic import
```

## Built-in Tool Parameters

| Tool | Parameters | Rate Limit | Risk |
|------|------|------|------|
| `memory_search` | `query`, `category?` | 10/min | LOW |
| `memory_save` | `content`, `category?`, `importance?` | 10/min | LOW |
| `grep_search` | `pattern`, `path?`, `glob?`, `max_results?`, `literal?` | 20/min | LOW |
| `web_search` | `query`, `max_results?`, `search_depth?`, `time_range?`, `topic?`, `include_answer?` | 10/min | LOW |
| `kb_search` | `query`, `namespace?`, `top_k?`, `view?`, 6 filters | 20/min | LOW |
| `file_read` | `path`, `offset?`, `limit?` | 30/min | LOW |
| `file_list` | `path?`, `pattern?` | 20/min | LOW |
| `file_write` | `path`, `content`, `mode?`, `expected_version?` | 20/min | MEDIUM |
| `python_execute` | `code` | Unlimited | HIGH |
| `shell_exec` | `command`, `cwd?`, `timeout?` | Unlimited | HIGH |
| `subagent_run` | `task`, `role?`, `allowed_tools?`, `max_steps?` | 10/min | LOW |

> See [Code Execution](Code-Execution.en.md) for sandbox details, [MCP Integration](MCP-Integration.en.md) for external tool integration.
