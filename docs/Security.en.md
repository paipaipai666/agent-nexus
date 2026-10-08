> **[中文](Security.md) | [English](Security.en.md)**

# 🔒 Security Model

## Tool Security (7 Gates)

All tools pass through [ToolRegistry](Tool-Governance.en.md) gates: RBAC → Schema → Rate-limit → Timeout → Risk → HITL → Audit.

## Code Execution Security (shell_exec)

- `shell_exec` sandbox degradation chain: bubblewrap (Linux) / Seatbelt (macOS) / Low-IL restricted token + Job Object (Windows) → Docker (with daemon probe; a stopped daemon keeps the chain degrading) → warned local fallback
- Shell 3-layer blacklist (general + platform + user-defined)
- NFKC normalization to prevent Unicode bypass

## Path Sandbox (file tools)

Paths passed to `file_read` / `file_write` / `file_list` go through `_resolve_safe` with two layers (normpath blocks `..` traversal; resolve blocks symlink escapes). Allowed roots: session workspace + `~/.agentnexus` + package dir + per-run attachments + **user-approved paths** + config `allowed_paths`.

When a path escapes, two explicit-consent channels widen the sandbox:

1. **Session-scoped prompt**: tool_runner pre-flights the guard before invoking; if it escapes and a confirmation channel exists, the user is asked once — approval adds the path to a workspace-scoped session approval set (process lifetime; concurrent sessions don't inherit) and also covers file_write's HITL gate; denial skips execution (`HITL_BLOCKED`). With no channel (non-interactive), the legacy hard error remains.
2. **Persistent list**: `allowed_paths` in config.yaml (list of file/dir paths). Deliberately **not** in the config API's settable keys — widening the sandbox is the same trust class as `shell_blacklist` and stays YAML-only.

`shell_exec`'s `cwd` also passes `_resolve_safe`, but escapes there have no prompt channel (hard error by design).

## PII Masking

`MemoryManager._mask_pii()` implements partial masking. All text written to LTM passes through this function:

| Type | Original | Masked |
|------|------|--------|
| Email | `user@example.com` | `u***@***.com` |
| Phone | `13812345678` | `138****5678` |
| API Key | `sk-xxx...` | Keeps `sk-` prefix |
| Credit Card | `1234567890123456` | `1234****3456` |

## Secret Handling

- Secret fields in config use `SecretStr` type
- Audit log `_truncate_params()` auto-masks
- Trace input/output truncated to 5000 characters

## MCP Security

- Health check + exponential backoff reconnect
- `allowed_agents` controls exposure scope
- Imported tools automatically receive all 7 governance gates

## Sub-agent Isolation

| Role | Available Tools |
|------|----------|
| explorer | Read-only + search + code execution |
| executor | Code + file operations |

## Related Tests

`tests/security/` contains: sandbox escape, path traversal, data injection, indirect injection, MCP security, secret leakage, privilege escalation, tool isolation.
