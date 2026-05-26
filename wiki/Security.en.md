# 🔒 Security Model

## Tool Security (7 Gates)

All tools pass through [ToolRegistry](Tool-Governance.en.md) gates: RBAC → Schema → Rate-limit → Timeout → Risk → HITL → Audit.

## Code Execution Security

- E2B → bubblewrap/Seatbelt → Docker → local fallback (see [diagram](Code-Execution.en.md))
- Shell 3-layer blacklist (general + platform + user-defined)
- NFKC normalization to prevent Unicode bypass

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
