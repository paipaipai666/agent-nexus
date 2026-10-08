> **[中文](Tool-Governance.md) | [English](Tool-Governance.en.md)**

# 🔧 Tool Governance System

All tool invocations pass through `ToolRegistry.invoke()`, executing 7 security gates sequentially.

## Seven Security Gates

### ① RBAC
`ToolMeta.allowed_agents` controls which callers are allowed. `"*"` is a wildcard. High-risk tools are restricted to whitelisted agents only.

### ② Schema Validation
JSON Schema validates parameter structure. Validator is compiled and cached at registration time.

### ③ Rate Limiting
Sliding window counter (60s). `shell_exec` is not rate-limited.

### ④ Timeout Control
`ThreadPoolExecutor(max_workers=4)` + `future.result(timeout=N)`.

### ⑤ Risk Classification

| Level | Behavior |
| --- | --- |
| LOW | Pass through |
| MEDIUM | Requires HITL confirmation |
| HIGH | Requires confirmation + sandbox |

### ⑥ HITL Confirmation
`ConfirmBridge` is pluggable: TUI dialog / stdin / auto-approve.

### ⑦ Audit Logging
Each call records `AuditEntry{tool, caller, params(masked), duration, hitl, error}`.

## Tool Registration

`ToolProvider` protocol, 9 providers registered in order:

```text
MemoryToolProvider       → memory_search, memory_save
SearchToolProvider       → grep_search, web_search, web_fetch, kb_search
FilesystemToolProvider   → file_read, file_list, file_write
ExecutionToolProvider    → shell_exec
SubagentToolProvider     → subagent_run
McpBridgeToolProvider    → MCP dynamic import
TodoToolProvider         → todo_add, todo_update, todo_list
ReactionToolProvider     → express_reaction
PlanModeToolProvider     → exit_plan_mode
```

## Built-in Tool Parameters

| Tool | Parameters | Rate Limit | Risk |
| --- | --- | --- | --- |
| `memory_search` | `query` | 10/min | LOW |
| `memory_save` | `content`, `category?`, `importance?`, `scope?`, `kind?`, `tags?` | 10/min | LOW |
| `memory_project_status` | No parameters | 10/min | LOW |
| `history_search` | `query`, `max_results?` | 15/min | LOW |
| `grep_search` | `pattern`, `path?`, `glob?`, `max_results?`, `literal?` | 20/min | LOW |
| `web_search` | `query`, `max_results?`, `search_depth?`, `time_range?`, `topic?`, `include_answer?`, `include_domains?`, `exclude_domains?` | 10/min | LOW |
| `web_fetch` | `urls`, `extract_depth?`, `format?` | 5/min | LOW |
| `kb_search` | `query`, `namespace?`, `top_k?`, `view?`, 8 filters | 20/min | LOW |
| `file_read` | `path`, `offset?`, `limit?` | 30/min | LOW |
| `file_list` | `path?`, `pattern?` | 20/min | LOW |
| `file_write` | `path`, `content`, `mode?`, `expected_version?` | 20/min | MEDIUM |
| `shell_exec` | `command`, `cwd?`, `timeout?` | Unlimited | HIGH |
| `subagent_run` | `task`, `role?`, `allowed_tools?`, `name?` | 10/min | LOW |
| `express_reaction` | `reaction`, `comment?` | 10/min | LOW |
| `exit_plan_mode` | `plan` | Unlimited | LOW |
| `todo_add` | `description` | Unlimited | LOW |
| `todo_update` | `item_id`, `status` | Unlimited | LOW |
| `todo_list` | No parameters | Unlimited | LOW |

> `todo_*` are bound per session (interactive TUI/Desktop); the other 15 register at startup.

> See [MCP Integration](MCP-Integration.en.md) for external tool integration (including external browser MCP tools).
