# 🪝 Hooks

AgentNexus's deterministic rule-enforcement layer: user-declared commands or in-process Python callbacks run at fixed lifecycle points. **The value of hooks is determinism** — rules execute unconditionally, without relying on LLM compliance.

Two kinds:

| Kind | Registration | Use case |
| --- | --- | --- |
| **Command hooks** | declared in `config.yaml` / `hooks.yaml`, executed as subprocesses | users: formatting, file protection, notifications, audit |
| **In-process hooks** | `@on(HookType.X)` Python decorator | framework/plugins: deep mutation (messages/tools/payload) |

## Command Hook Configuration

```yaml
# ~/.agentnexus/hooks.yaml (user-global, trusted by default)
# <workspace>/.agentnexus/hooks.yaml (project, hash trust review)
# config.yaml hooks: section (managed layer, trusted)
hooks:
  - event: before_tool_call
    matcher: "file_write|file_edit"     # optional regex on payload["name"]
    command: "python3 ~/.agentnexus/hooks/protect.py"
    timeout: 30                          # seconds, default 10; kills the whole tree
    async: false                         # true = background, observe-only, cannot block
    enabled: true
    on_failure: warn                     # warn | block (interceptable events only)
```

Layering (low → high precedence; all matching hooks run): `config.yaml` → `~/.agentnexus/hooks.yaml` → `<workspace>/.agentnexus/hooks.yaml`.

### Process contract (converged with Codex / Claude Code)

- **stdin**: JSON `{"hook_event", "session_id", "workspace", "payload", "abort_supported"}`
- **exit 0**: success; stdout JSON may carry `{"update": {merged into payload}, "context": "log text"}`
- **exit 2**: block. stderr is the reason, standardized via `HookContext.to_feedback()` and **always fed back to the model** (observation) plus audit
- **other non-zero / timeout / spawn failure**: handled per `on_failure` (`warn` and continue / `block`)

### Security

- Hooks run with a **scrubbed environment**: only `AGENTNEXUS_*` plus OS basics (PATH, …). LLM API keys and other secrets never propagate.
- Project hooks are gated by a sha256 fingerprint (source path + event + command) stored in `~/.agentnexus/hook_trust.json`; new or changed hooks are skipped with a warning until approved via `nexus hooks trust approve <fingerprint>` or `POST /api/hooks/trust`.
- `hook_trust: bypass` disables the gate (one-off automation only).
- Management: `nexus hooks list` / `nexus hooks test <event>` (dry run) / `GET /api/hooks`.

## Event Matrix

Mutable: ✅ = payload changes take effect; ❌ = read-only (mutations are called out in logs). Blockable: ✅ = `ctx.abort()` short-circuits.

### Tier 0: user interaction

| Event | Payload | Mutable | Blockable |
| --- | --- | --- | --- |
| `user_prompt_submit` | `prompt`, `agent_id` | ✅ (rewrites prompt) | ✅ refuses input; run never starts |
| `agent_stop` | `answer`, `steps`, `question`, `agent_id` | ❌ | ✅ vetoes the final answer, agent continues with feedback; **consecutive vetoes capped at 2** |
| `permission_request` | `name`, `params`, `caller`, `risk_level` | ✅ (`decision: "allow"` requires `hitl_hooks_may_approve: true`) | ✅ deny blocks; **audit records `hitl_decision=hook_denied:*`** |
| `notification` | `kind` (e.g. `hitl_request`), `tool`, `caller`, `risk_level` | ❌ | no (async) |

### Tier 1: agent / tool / model

| Event | Mutable | Blockable |
| --- | --- | --- |
| `before_tool_call` / `after_tool_call` / `on_tool_error` | ✅ (params) / ❌ / ❌ | ✅ / no / no |
| `before_model_call` (messages, tools, strategy) / `after_model_call` | ✅ | ✅ / no |
| `before_llm_call` / `after_llm_call` | ✅ | ✅ / no |
| `agent_start` / `agent_end` | ❌ | no |
| `before_memory_op` / `after_memory_op`, `before_ltm_save/search`, … | ❌ | ✅ (before_*) |

### Tier 2/3: infrastructure

`before_shell_exec` (✅ blockable), `before_registry_invoke`, `before/after_mcp_connect`, `before/after_mcp_call_tool`, `before/after_subagent_run`, `before/after_rag_search`, `before/after_kb_ingest`, `before/after_checkpoint`, `before/after_plugin_load`, `before/after_app_build`, `before/after_compact`, `before/after_workflow_step`, `before/after_eval_run`. All read-only; `before_*` blockable.

Full payload keys and types: `PAYLOAD_SCHEMAS` in `agentnexus/core/hooks.py` (fire-site drift logs an error when `hook_schema_check: true`).

## In-Process Hook API

```python
from agentnexus.core.hooks import AbortCode, HookType, on

@on(HookType.BEFORE_TOOL_CALL, priority=100)   # lower runs first; same name = last wins
def protect_env(ctx):
    path = str(ctx.payload.get("params", {}).get("path", ""))
    if ".env" in path:
        ctx.abort("no .env edits", code=AbortCode.POLICY_VIOLATION)
```

- `ctx.payload` is a shared mutable dict; mutations on read-only events produce a warning naming the hook and changed keys
- `ctx.abort(reason, code=..., details=...)` short-circuits; use `AbortCode` constants (`BLOCKED / PERMISSION_DENIED / VALIDATION_FAILED / POLICY_VIOLATION / RATE_LIMITED`)
- Callback exceptions are isolated: first failure logs a warning (with traceback), subsequent ones debug-counted
- Slow chains (>100ms) log a warning; every fire gets a `hook_fire` trace span
- Registry management: `list_hooks()` / `enable()` / `disable()` / `unregister()`

## Reliability

- **Per-hook timeout**: `register(..., timeout=seconds)` or `@on(..., timeout=seconds)` caps sync callbacks; on expiry the loop continues (the stray thread runs to completion — Python cannot kill threads, same caveat as registry SEC-008)
- **Journal**: per-hook outcomes append to `{traces_dir}/hooks.jsonl` (append-only JSONL: event/hook/outcome/duration_ms/changed_keys); disable with `hooks_journal: false`; `GET /api/hooks` returns the last 50 entries, `read_hook_journal()` for programmatic access
- **Tracing**: a `hook_call` child span per hook under the `hook_fire` span
- **Ordering**: same-priority hooks run in registration order (sort stability)

## Plugin Code Hooks

Declarative plugins (`plugin.yaml`) are zero-code by default. A plugin directory may ship `hooks.py` exposing `register(manager)`; declared via `entrypoint: hooks.py` in the manifest, it only loads when `plugins_allow_code: true`. The entrypoint must be a relative path inside the plugin directory.

## Execution Order

1. In-process hook chain (priority ascending; abort short-circuits)
2. Command hook chain (layer order; interceptable events sequential, observer events concurrent; abort short-circuits)
