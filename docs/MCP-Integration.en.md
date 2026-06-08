> **[中文](MCP-Integration.md) | [English](MCP-Integration.en.md)**

# 🔌 MCP Integration

MCP (Model Context Protocol) supports importing tools, resources, and prompts from external services.

## Transport Methods

| Type | Config | Scenario |
|------|------|------|
| `stdio` | `command` + `args` | Local subprocess |
| `streamable_http` | `url` + `headers` | Remote HTTP |

## Import Scope

| Category | Config Switch | Behavior |
|------|----------|------|
| `tools` | `import_tools` | Registered as `mcp_{server}__{tool}` |
| `resources` | `import_resources` | Converted to list/read tools |
| `prompts` | `import_prompts` | Converted to list/get tools |

## Governance Integration

MCP tools automatically receive all 7 ToolRegistry security gates.

## Key Configuration

| MCPServerConfig Field | Default | Description |
|----------------------|--------|------|
| `transport` | `"stdio"` | stdio / streamable_http |
| `risk_level` | `"medium"` | Maps to tool governance risk level |
| `require_hitl` | `false` | |
| `rate_limit_per_min` | `10` | |
| `timeout_sec` | `60` | |
| `allowed_agents` | `[react_agent, subagent_explorer, subagent_executor]` | |
| `health_check_interval_sec` | `30` | |
| `reconnect_max_attempts` | `0` | 0=unlimited |
| `max_concurrency_per_server` | `4` | |

## Configuration Example

```yaml
mcp_enabled: true
mcp_servers:
  - name: local-docs
    transport: stdio
    command: python
    args: [mcp_server.py]
    risk_level: medium
```
